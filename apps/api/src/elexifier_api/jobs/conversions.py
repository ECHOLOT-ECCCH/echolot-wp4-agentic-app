"""Full conversion job: stream the source, convert with a mapping version, write DMLex XML and
JSON to storage as output files, validate, store the report, publish progress."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select

from elexifier_api.jobs.documents import download_to_temp
from elexifier_api.jobs.runtime import job_database, job_storage
from elexifier_core.db.models import (
    ConversionRun,
    ConversionStatus,
    ConvertedEntry,
    Document,
    Entry,
    File,
    FileKind,
    Mapping,
)
from elexifier_core.db.session import Database
from elexifier_core.events import publish
from elexifier_core.jobs import QUEUE_HEAVY, app
from elexifier_core.storage import Storage, make_key
from elexifier_pipeline.xml.convert import FullRunResult, convert_document
from elexifier_pipeline.xml.mapping import load_mapping_spec

log = structlog.get_logger(__name__)

PROGRESS_INTERVAL_S = 2.0
REPORT_ERRORS_IN_ROW = 200
REPORT_ERRORS_IN_ARTIFACT = 10_000
ISSUE_ROWS_MAX = 5000
ENTRY_LOOKUP_BATCH = 1000


class ConversionJobError(Exception):
    pass


async def _file_chunks(path: Path, size: int = 1024 * 1024) -> AsyncIterator[bytes]:
    with path.open("rb") as fh:
        while chunk := fh.read(size):
            yield chunk


async def _publish_progress(
    db: Database, run_id: uuid.UUID, project_id: uuid.UUID, progress: dict[str, Any]
) -> None:
    async with db.session() as session:
        run = await session.get(ConversionRun, run_id)
        if run is None:
            return
        run.stats = {**(run.stats or {}), **progress}
        await publish(
            session,
            project_id,
            "job.progress",
            {"kind": "conversions.run", "run_id": str(run_id), **progress},
        )
        await session.commit()


async def _store_output(
    storage: Storage,
    session: Any,
    *,
    project_id: uuid.UUID,
    kind: str,
    name: str,
    path: Path,
    mime: str,
    created_by: uuid.UUID | None,
) -> File:
    file_id = uuid.uuid4()
    key = make_key(str(project_id), kind, str(file_id), name)
    stat = await storage.put(key, _file_chunks(path), content_type=mime)
    record = File(
        id=file_id,
        project_id=project_id,
        kind=kind,
        name=name,
        s3_key=key,
        size=stat.size,
        sha256=stat.sha256 or "",
        mime=mime,
        created_by=created_by,
    )
    session.add(record)
    await session.flush()
    return record


def _report(result: FullRunResult, run: ConversionRun) -> dict[str, Any]:
    validation = result.validation
    errors = [
        {
            "output_index": e.entry_index,
            "ordinal": result.ordinals.get(e.entry_index) if e.entry_index is not None else None,
            "path": e.path,
            "reason": e.reason,
        }
        for e in validation.errors
    ]
    return {
        "summary": validation.summary(),
        "valid": validation.valid,
        "error_count": validation.error_count,
        "limitation_count": validation.limitation_count,
        "truncated": validation.truncated,
        "issue_codes": result.issue_codes,
        "entries_with_issues": len(result.issues),
        "resource_issues": [i.as_dict() for i in result.resource_issues],
        "plan_warnings": result.plan_warnings,
        "resource_scalars": result.resource_scalars,
        "homographs_numbered": result.homographs_numbered,
        "timings": result.timings,
        "mapping_version": run.mapping_version,
        "errors": errors,
    }


async def run_full_conversion(run_id: str | uuid.UUID) -> dict[str, Any]:
    """The job body; callable directly (tests) or through the Procrastinate task."""
    db = job_database()
    storage = job_storage()
    rid = uuid.UUID(str(run_id))
    async with db.session() as session:
        run = await session.get(ConversionRun, rid)
        if run is None:
            raise ConversionJobError(f"run {rid} does not exist")
        document = await session.get(Document, run.document_id) if run.document_id else None
        if document is None:
            raise ConversionJobError("the run has no document")
        source = await session.get(File, document.source_file_id)
        mapping = (
            await session.execute(
                select(Mapping).where(
                    Mapping.project_id == run.project_id, Mapping.version == run.mapping_version
                )
            )
        ).scalar_one_or_none()
        if source is None or mapping is None:
            raise ConversionJobError("source file or mapping version is gone")
        project_id, document_id = run.project_id, document.id
        source_key, source_name = source.s3_key, source.name
        total = document.entry_count
        options = dict((run.scope or {}).get("options") or {})
        triggered_by = run.triggered_by
        mapping_spec = mapping.spec
        run.status = ConversionStatus.RUNNING.value
        run.error = None
        run.stats = {"phase": "downloading", "done": 0, "total": total}
        await publish(
            session,
            project_id,
            "job.progress",
            {"kind": "conversions.run", "run_id": str(rid), **run.stats},
        )
        await session.commit()

    workdir = Path(tempfile.mkdtemp(prefix="elexifier-run-"))
    source_path: Path | None = None
    try:
        source_path = await download_to_temp(storage, source_key)
        desc = load_mapping_spec(mapping_spec).to_desc()
        loop = asyncio.get_running_loop()
        latest: dict[str, Any] = {"phase": "converting", "done": 0, "total": total}
        published: dict[str, Any] = {}

        def on_progress(phase: str, done: int, phase_total: int | None) -> None:
            latest.update(phase=phase, done=done, total=phase_total)

        future = loop.run_in_executor(
            None,
            lambda: convert_document(
                source_path,  # type: ignore[arg-type]
                desc,
                workdir,
                progress=on_progress,
                total_hint=total,
                number_homographs=bool(options.get("number_homographs", True)),
            ),
        )
        while True:
            done, _pending = await asyncio.wait({future}, timeout=PROGRESS_INTERVAL_S)
            snapshot = dict(latest)
            if snapshot != published:
                await _publish_progress(db, rid, project_id, snapshot)
                published = snapshot
            if done:
                break
        result = future.result()

        stem = Path(source_name).stem or "dictionary"
        short = str(rid)[:8]
        async with db.session() as session:
            xml_file = await _store_output(
                storage,
                session,
                project_id=project_id,
                kind=FileKind.OUTPUT.value,
                name=f"{stem}-dmlex-v{mapping.version}-{short}.xml",
                path=result.xml_path,
                mime="application/xml",
                created_by=triggered_by,
            )
            json_file = await _store_output(
                storage,
                session,
                project_id=project_id,
                kind=FileKind.OUTPUT.value,
                name=f"{stem}-dmlex-v{mapping.version}-{short}.json",
                path=result.json_path,
                mime="application/json",
                created_by=triggered_by,
            )
            run = await session.get(ConversionRun, rid)
            assert run is not None
            report = _report(result, run)
            report_path = workdir / "report.json"
            full_report = {
                **report,
                "errors": [
                    {
                        "output_index": e.entry_index,
                        "ordinal": result.ordinals.get(e.entry_index)
                        if e.entry_index is not None
                        else None,
                        "path": e.path,
                        "reason": e.reason,
                    }
                    for e in result.validation.errors[:REPORT_ERRORS_IN_ARTIFACT]
                ],
            }
            report_path.write_text(json.dumps(full_report, ensure_ascii=False, indent=1))
            report_file = await _store_output(
                storage,
                session,
                project_id=project_id,
                kind=FileKind.ARTIFACT.value,
                name=f"report-run-{short}.json",
                path=report_path,
                mime="application/json",
                created_by=triggered_by,
            )
            await _store_issue_rows(session, run, document_id, result)
            errors, warnings = result.error_count, result.warning_count
            run.stats = {
                "phase": "done",
                "done": result.source_entries,
                "total": result.source_entries,
                "requested": result.source_entries,
                "source_entries": result.source_entries,
                "converted": result.output_entries,
                "with_issues": len(result.issues),
                "errors": errors,
                "warnings": warnings,
                "resource_issues": len(result.resource_issues),
                "homographs_numbered": result.homographs_numbered,
                "timings": result.timings,
                "output_files": [
                    {
                        "file_id": str(xml_file.id),
                        "name": xml_file.name,
                        "format": "xml",
                        "size": xml_file.size,
                    },
                    {
                        "file_id": str(json_file.id),
                        "name": json_file.name,
                        "format": "json",
                        "size": json_file.size,
                    },
                ],
                "report_file_id": str(report_file.id),
            }
            run.validation_report = {**report, "errors": report["errors"][:REPORT_ERRORS_IN_ROW]}
            run.output_file_id = xml_file.id
            run.status = ConversionStatus.DONE.value
            run.finished_at = datetime.now(UTC)
            await publish(
                session,
                project_id,
                "run.finished",
                {
                    "run_id": str(rid),
                    "kind": run.kind,
                    "status": run.status,
                    "mapping_version": run.mapping_version,
                    "converted": result.output_entries,
                    "valid": result.validation.valid,
                },
            )
            await session.commit()
        log.info(
            "conversion.finished",
            run_id=str(rid),
            entries=result.output_entries,
            valid=result.validation.valid,
            timings=result.timings,
        )
        return {
            "run_id": str(rid),
            "converted": result.output_entries,
            "valid": result.validation.valid,
            "xml_file_id": str(xml_file.id),
            "json_file_id": str(json_file.id),
        }
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"[:2000]
        log.warning("conversion.failed", run_id=str(rid), error=message)
        async with db.session() as session:
            run = await session.get(ConversionRun, rid)
            if run is not None:
                run.status = ConversionStatus.FAILED.value
                run.error = message
                run.finished_at = datetime.now(UTC)
                run.stats = {**(run.stats or {}), "phase": "failed"}
                await publish(
                    session,
                    project_id,
                    "run.finished",
                    {"run_id": str(rid), "kind": run.kind, "status": run.status, "error": message},
                )
                await session.commit()
        raise
    finally:
        if source_path is not None:
            source_path.unlink(missing_ok=True)
        shutil.rmtree(workdir, ignore_errors=True)


async def _store_issue_rows(
    session: Any, run: ConversionRun, document_id: uuid.UUID, result: FullRunResult
) -> None:
    """converted_entries rows for the entries with issues (the full output lives in the files)."""
    if not result.issues:
        return
    ordinals = sorted({o for o in result.ordinals.values()})
    entry_ids: dict[int, uuid.UUID] = {}
    for offset in range(0, len(ordinals), ENTRY_LOOKUP_BATCH):
        batch = ordinals[offset : offset + ENTRY_LOOKUP_BATCH]
        rows = (
            await session.execute(
                select(Entry.ordinal, Entry.id).where(
                    Entry.document_id == document_id, Entry.ordinal.in_(batch)
                )
            )
        ).all()
        entry_ids.update({o: i for o, i in rows})
    for index in sorted(result.issues)[:ISSUE_ROWS_MAX]:
        issues = [i.as_dict() for i in result.issues[index]]
        ordinal = result.ordinals.get(index)
        session.add(
            ConvertedEntry(
                run_id=run.id,
                entry_id=entry_ids.get(ordinal) if ordinal is not None else None,
                position=index,
                ordinal=ordinal,
                headword=None,
                dmlex={},
                issues=issues,
                issue_count=len(issues),
            )
        )
    await session.flush()


@app.task(name="conversions.run", queue=QUEUE_HEAVY, pass_context=False)
async def run_conversion_task(run_id: str) -> dict[str, Any]:
    return await run_full_conversion(run_id)
