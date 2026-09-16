"""Full structuring job: every reviewed span of a PDF document becomes a DMLex entry through the
structurer, in batches with a result cache, progress and cost accounting; the entries are then
written, validated and stored as output files (rebuildable after the lexicographer's edits)."""

from __future__ import annotations

import asyncio
import shutil
import statistics
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_agent.models import build_model
from elexifier_agent.structuring import (
    cache_key,
    cached_results,
    extract,
    full_texts,
    usable_spans,
)
from elexifier_api.jobs.conversions import _store_output
from elexifier_api.jobs.runtime import job_database, job_settings, job_storage
from elexifier_core.crypto import SecretBox
from elexifier_core.db.models import (
    ConversionRun,
    ConversionStatus,
    ConvertedEntry,
    Document,
    File,
    FileKind,
    ProviderCredential,
    UsageRecord,
)
from elexifier_core.db.session import Database
from elexifier_core.events import publish
from elexifier_core.jobs import QUEUE_HEAVY, app
from elexifier_core.storage import Storage
from elexifier_pipeline.dmlex.validator import check_consistency, validate_xml
from elexifier_pipeline.dmlex.writers import WriterReport, write_json, write_xml
from elexifier_pipeline.pdf.structurers import (
    LlmStructurer,
    RuleStructurer,
    StructuredEntry,
    StructureProfile,
    renumber_entry,
)

log = structlog.get_logger(__name__)

BATCH = 16
LOW_CONFIDENCE = 0.6


class StructuringJobError(Exception):
    pass


async def _model_for(db: AsyncSession, spec: dict[str, Any]) -> Any:
    """The project's model rebuilt in the worker from the stored credential."""
    settings = job_settings()
    provider = str(spec.get("provider") or "")
    model_id = str(spec.get("model_id") or "")
    if not provider or not model_id:
        raise StructuringJobError("the run has no model")
    api_key = None
    base_url = None
    credential_id = spec.get("credential_id")
    if credential_id:
        credential = await db.get(ProviderCredential, uuid.UUID(str(credential_id)))
        if credential is None:
            raise StructuringJobError("the credential used for this run was deleted")
        if credential.encrypted_key:
            api_key = SecretBox(settings.secret_keys).decrypt(credential.encrypted_key)
        base_url = credential.base_url
    return build_model(
        provider,
        model_id,
        api_key,
        base_url,
        app_url=settings.public_url,
        allow_test_model=settings.elx_enable_test_model,
    )


async def _progress(
    db: Database, run_id: uuid.UUID, project_id: uuid.UUID, stats: dict[str, Any]
) -> None:
    async with db.session() as session:
        run = await session.get(ConversionRun, run_id)
        if run is None:
            return
        run.stats = {**(run.stats or {}), **stats}
        await publish(
            session,
            project_id,
            "job.progress",
            {"kind": "pdf.structure", "run_id": str(run_id), **stats},
        )
        await session.commit()


def _entry_of(row: ConvertedEntry) -> dict[str, Any] | None:
    entry = row.override or row.dmlex
    return entry if entry and entry.get("headword") else None


async def rebuild_outputs(
    db: AsyncSession, storage: Storage, run: ConversionRun, *, stem: str
) -> dict[str, Any]:
    """Write DMLex XML and JSON for a run's entries (overrides win), validate, store the files
    and refresh per-entry schema issues. Returns the validation report."""
    rows = (
        (
            await db.execute(
                select(ConvertedEntry)
                .where(ConvertedEntry.run_id == run.id)
                .order_by(ConvertedEntry.position)
            )
        )
        .scalars()
        .all()
    )
    profile = StructureProfile.model_validate((run.scope or {}).get("profile") or {})
    entries: list[dict[str, Any]] = []
    index_to_row: dict[int, ConvertedEntry] = {}
    for row in rows:
        entry = _entry_of(row)
        if entry is not None:
            index_to_row[len(entries)] = row
            entries.append(renumber_entry(entry, f"{profile.entry_id_prefix}{row.position + 1}"))
    scalars: dict[str, Any] = {
        "uri": f"urn:elexifier:{run.document_id}",
        "langCode": profile.source_language if profile.source_language != "unknown" else "und",
    }
    if profile.target_languages:
        scalars["translationLanguages"] = [{"langCode": lang} for lang in profile.target_languages]
    workdir = Path(tempfile.mkdtemp(prefix="elexifier-structure-"))
    try:
        xml_path = workdir / "dmlex.xml"
        json_path = workdir / "dmlex.json"
        report = WriterReport()
        with xml_path.open("wb") as out:
            write_xml(out, scalars, entries, report)
        with json_path.open("w", encoding="utf-8") as out_json:
            write_json(out_json, scalars, entries)
        validation = await asyncio.to_thread(validate_xml, xml_path, max_errors=5000)
        consistency = check_consistency(entries)
        per_index: dict[int, list[dict[str, Any]]] = {}
        for error in validation.errors:
            if error.entry_index is not None:
                per_index.setdefault(error.entry_index, []).append(
                    {
                        "severity": "error",
                        "source": "schema",
                        "code": "schema",
                        "path": error.path.split("/entry", 1)[-1].lstrip("[0123456789]/")
                        or "entry",
                        "message": error.reason,
                    }
                )
        for finding in consistency:
            per_index.setdefault(finding.entry_index, []).append(
                {
                    "severity": "warning",
                    "source": "consistency",
                    "code": finding.code,
                    "path": "entry",
                    "message": finding.message,
                }
            )
        for index, row in index_to_row.items():
            kept = [i for i in (row.issues or []) if i.get("source") == "structurer"]
            issues = kept + per_index.get(index, [])
            row.issues = issues
            row.issue_count = len(issues)
        short = str(run.id)[:8]
        xml_file = await _store_output(
            storage,
            db,
            project_id=run.project_id,
            kind=FileKind.OUTPUT.value,
            name=f"{stem}-dmlex-{short}.xml",
            path=xml_path,
            mime="application/xml",
            created_by=run.triggered_by,
        )
        json_file = await _store_output(
            storage,
            db,
            project_id=run.project_id,
            kind=FileKind.OUTPUT.value,
            name=f"{stem}-dmlex-{short}.json",
            path=json_path,
            mime="application/json",
            created_by=run.triggered_by,
        )
        report_payload = {
            "summary": validation.summary(),
            "valid": validation.valid,
            "error_count": validation.error_count,
            "limitation_count": validation.limitation_count,
            "entries": len(entries),
            "writer_warnings": report.warnings[:20],
            "profile": profile.model_dump(),
            "rebuilt_at": datetime.now(UTC).isoformat(),
            "errors": [
                {"output_index": e.entry_index, "path": e.path, "reason": e.reason}
                for e in validation.errors[:200]
            ],
        }
        run.output_file_id = xml_file.id
        run.stats = {
            **(run.stats or {}),
            "converted": len(entries),
            "with_issues": sum(1 for r in rows if r.issue_count),
            "errors": sum(
                1 for r in rows for i in (r.issues or []) if i.get("severity") == "error"
            ),
            "warnings": sum(
                1 for r in rows for i in (r.issues or []) if i.get("severity") != "error"
            ),
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
        }
        run.validation_report = report_payload
        await db.flush()
        return report_payload
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def run_full_structuring(run_id: str | uuid.UUID) -> dict[str, Any]:
    """The job body; callable directly (tests) or through the Procrastinate task."""
    db = job_database()
    storage = job_storage()
    rid = uuid.UUID(str(run_id))
    async with db.session() as session:
        run = await session.get(ConversionRun, rid)
        if run is None:
            raise StructuringJobError(f"run {rid} does not exist")
        document = await session.get(Document, run.document_id) if run.document_id else None
        if document is None:
            raise StructuringJobError("the run has no document")
        file = await session.get(File, document.source_file_id)
        if file is None:
            raise StructuringJobError("the source file is gone")
        scope = dict(run.scope or {})
        project_id = run.project_id
        profile = StructureProfile.model_validate(scope.get("profile") or {})
        structurer_name = str(scope.get("structurer") or "llm")
        with_images = bool(scope.get("with_images"))
        model_spec = dict(scope.get("model") or {})
        triggered_by = run.triggered_by
        stem = Path(file.name).stem or "dictionary"
        spans = await usable_spans(session, document)
        if structurer_name == "llm":
            model = await _model_for(session, model_spec)
            structurer: Any = LlmStructurer(model, profile=profile)
        else:
            structurer = RuleStructurer(profile)
        run.status = ConversionStatus.RUNNING.value
        run.error = None
        run.stats = {"phase": "structuring", "done": 0, "total": len(spans), "cached": 0}
        await session.execute(delete(ConvertedEntry).where(ConvertedEntry.run_id == rid))
        await publish(
            session,
            project_id,
            "job.progress",
            {"kind": "pdf.structure", "run_id": str(rid), **run.stats},
        )
        await session.commit()

    usage_total = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "requests": 0}
    done = cached = failed = 0
    confidences: list[float] = []
    try:
        for start in range(0, len(spans), BATCH):
            batch = spans[start : start + BATCH]
            async with db.session() as session:
                texts = await full_texts(session, batch)
                keys = [
                    cache_key(model_spec, structurer_name, profile, texts.get(a.id, ""))
                    for a in batch
                ]
                reuse = await cached_results(session, project_id, keys)
                fresh = [(a, k) for a, k in zip(batch, keys, strict=True) if k not in reuse]
                results: dict[str, StructuredEntry] = {}
                if fresh:
                    extracted = await extract(
                        storage,
                        document,
                        file,
                        [a for a, _k in fresh],
                        with_images=with_images,
                        texts=texts,
                    )
                    async for result in structurer.structure(extracted):
                        results[result.span_id] = result
                for position, (annotation, key) in enumerate(
                    zip(batch, keys, strict=True), start=start
                ):
                    hit = reuse.get(key)
                    if hit is not None:
                        cached += 1
                        row = ConvertedEntry(
                            run_id=rid,
                            annotation_id=annotation.id,
                            cache_key=key,
                            position=position,
                            ordinal=annotation.position,
                            headword=hit.headword,
                            dmlex=renumber_entry(
                                hit.override or hit.dmlex,
                                f"{profile.entry_id_prefix}{position + 1}",
                            ),
                            issues=[],
                            issue_count=0,
                            confidence=hit.confidence,
                            notes=[*(hit.notes or []), "reused from an earlier run"],
                        )
                    else:
                        result = results.get(str(annotation.id))
                        if result is None:
                            failed += 1
                            result = StructuredEntry(
                                span_id=str(annotation.id),
                                entry={},
                                confidence=0.0,
                                notes=["no result"],
                                error="no result",
                            )
                        for k in usage_total:
                            usage_total[k] += int(result.usage.get(k, 0))
                        if result.error:
                            failed += 1
                        row = ConvertedEntry(
                            run_id=rid,
                            annotation_id=annotation.id,
                            cache_key=key,
                            position=position,
                            ordinal=annotation.position,
                            headword=(
                                str(
                                    result.entry.get("headword")
                                    or (annotation.payload or {}).get("headword_guess")
                                    or ""
                                )[:300]
                                or None
                            ),
                            dmlex=renumber_entry(
                                result.entry, f"{profile.entry_id_prefix}{position + 1}"
                            )
                            if result.entry
                            else {},
                            issues=[
                                {
                                    "severity": "error",
                                    "source": "structurer",
                                    "code": "model-error",
                                    "path": "entry",
                                    "message": result.error,
                                }
                            ]
                            if result.error
                            else [],
                            issue_count=1 if result.error else 0,
                            confidence=result.confidence,
                            notes=list(result.notes),
                        )
                    if row.dmlex and row.confidence is not None:
                        confidences.append(row.confidence)
                    session.add(row)
                    done += 1
                await session.commit()
            await _progress(
                db,
                rid,
                project_id,
                {
                    "phase": "structuring",
                    "done": done,
                    "total": len(spans),
                    "cached": cached,
                    "failed": failed,
                    "usage": dict(usage_total),
                },
            )

        await _progress(
            db, rid, project_id, {"phase": "writing", "done": done, "total": len(spans)}
        )
        async with db.session() as session:
            run = await session.get(ConversionRun, rid)
            assert run is not None
            report = await rebuild_outputs(session, storage, run, stem=stem)
            estimate = (run.scope or {}).get("estimate") or {}
            estimated = int(estimate.get("input_tokens") or 0) + int(
                estimate.get("output_tokens") or 0
            )
            actual = usage_total["input_tokens"] + usage_total["output_tokens"]
            run.stats = {
                **run.stats,
                "phase": "done",
                "done": done,
                "total": len(spans),
                "requested": len(spans),
                "cached": cached,
                "failed": failed,
                "usage": dict(usage_total),
                "confidence_mean": round(statistics.fmean(confidences), 3) if confidences else 0.0,
                "low_confidence": sum(1 for c in confidences if c < LOW_CONFIDENCE),
                "cost_check": {
                    "estimated_tokens": estimated,
                    "actual_tokens": actual,
                    "ratio": round(actual / estimated, 3) if estimated else None,
                },
            }
            run.validation_report = {**report, "profile": profile.model_dump()}
            run.status = ConversionStatus.DONE.value
            run.finished_at = datetime.now(UTC)
            if model_spec and actual:
                session.add(
                    UsageRecord(
                        user_id=triggered_by,
                        project_id=project_id,
                        provider=str(model_spec.get("provider") or "unknown"),
                        model=str(model_spec.get("model_id") or "unknown"),
                        purpose="structure",
                        input_tokens=usage_total["input_tokens"],
                        output_tokens=usage_total["output_tokens"],
                    )
                )
            await publish(
                session,
                project_id,
                "run.finished",
                {
                    "run_id": str(rid),
                    "kind": run.kind,
                    "status": run.status,
                    "converted": run.stats.get("converted"),
                    "valid": report.get("valid"),
                },
            )
            await session.commit()
        log.info(
            "structuring.finished",
            run_id=str(rid),
            spans=len(spans),
            cached=cached,
            failed=failed,
            usage=usage_total,
        )
        return {
            "run_id": str(rid),
            "spans": len(spans),
            "cached": cached,
            "failed": failed,
            "usage": usage_total,
            "valid": report.get("valid"),
        }
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"[:2000]
        log.warning("structuring.failed", run_id=str(rid), error=message)
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


@app.task(name="pdf.structure", queue=QUEUE_HEAVY, pass_context=False)
async def structure_task(run_id: str) -> dict[str, Any]:
    return await run_full_structuring(run_id)
