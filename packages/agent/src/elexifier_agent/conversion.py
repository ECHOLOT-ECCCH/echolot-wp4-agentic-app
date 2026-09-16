"""Sample conversions: convert selected indexed entries with a mapping version, validate them,
and store the run. Shared by the ``run_conversion`` tool and the API (full runs are a job, M1.8).
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lxml import etree
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_agent.sandbox import RunnerClient
from elexifier_agent.steps import (
    StepFailed,
    StepRecord,
    apply_steps_to_entries,
    enabled_steps,
    records_snapshot,
    snapshot_of,
)
from elexifier_core.db.models import (
    ConversionRun,
    ConversionStatus,
    ConvertedEntry,
    Document,
    Entry,
    File,
    Mapping,
    RunKind,
)
from elexifier_core.events import publish
from elexifier_core.storage import Storage
from elexifier_pipeline.dmlex.validator import EntryValidation, validate_entries
from elexifier_pipeline.dmlex.writers import entry_element
from elexifier_pipeline.xml import streaming
from elexifier_pipeline.xml.dmlexifier import MappingError, new_transformer
from elexifier_pipeline.xml.mapping import load_mapping_spec

MAX_HEAD_BYTES = 8 * 1024 * 1024
MAX_SAMPLE_RUN_ENTRIES = 50
FULL_RUN_TASK = "conversions.run"

Deferrer = Callable[[str, dict[str, Any]], Awaitable[int | None]]


class ConversionError(Exception):
    """A problem the caller (model or user) can act on."""


@dataclass
class SampleOutcome:
    run: ConversionRun
    converted: list[ConvertedEntry]
    validation: EntryValidation
    skipped: list[dict[str, Any]] = field(default_factory=list)  # entries with no output
    plan_warnings: list[str] = field(default_factory=list)


async def latest_mapping(db: AsyncSession, project_id: uuid.UUID) -> Mapping | None:
    return (
        await db.execute(
            select(Mapping)
            .where(Mapping.project_id == project_id)
            .order_by(Mapping.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def mapping_version(
    db: AsyncSession, project_id: uuid.UUID, version: int | None
) -> Mapping | None:
    if version is None:
        return await latest_mapping(db, project_id)
    return (
        await db.execute(
            select(Mapping).where(Mapping.project_id == project_id, Mapping.version == version)
        )
    ).scalar_one_or_none()


async def _read_range(storage: Storage, key: str, start: int, end_exclusive: int) -> bytes:
    if end_exclusive <= start:
        return b""
    return b"".join([c async for c in storage.open(key, (start, end_exclusive - 1))])


async def document_head(db: AsyncSession, storage: Storage, document: Document) -> bytes:
    """Bytes before the first indexed entry: root element and header."""
    first = (
        await db.execute(
            select(Entry).where(Entry.document_id == document.id).order_by(Entry.ordinal).limit(1)
        )
    ).scalar_one_or_none()
    file = await db.get(File, document.source_file_id)
    if first is None or file is None:
        raise ConversionError("the document has no indexed entries or its source file is gone")
    start = int(first.source_ref["start"])
    if start > MAX_HEAD_BYTES:
        raise ConversionError("the part of the file before the first entry is larger than 8 MB")
    return await _read_range(storage, file.s3_key, 0, start)


def entry_xml(dmlex: dict[str, Any]) -> str:
    """Pretty DMLex XML for one converted entry (as it appears inside the resource)."""
    element = entry_element(dmlex)
    etree.indent(element, space="  ")
    return etree.tostring(element, encoding="unicode")


async def run_sample(
    db: AsyncSession,
    storage: Storage,
    *,
    document: Document,
    mapping: Mapping,
    entries: list[Entry],
    triggered_by: uuid.UUID | None,
    scope: dict[str, Any] | None = None,
    runner: RunnerClient | None = None,
) -> SampleOutcome:
    """Convert ``entries`` with ``mapping`` and store a done sample run. Commits nothing; the
    caller commits. Raises ConversionError for problems the caller should report."""
    if not entries:
        raise ConversionError("no entries selected")
    if len(entries) > MAX_SAMPLE_RUN_ENTRIES:
        raise ConversionError(f"a sample converts at most {MAX_SAMPLE_RUN_ENTRIES} entries")
    try:
        desc = load_mapping_spec(mapping.spec).to_desc()
        plan = streaming.plan_for(desc)
    except MappingError as exc:
        raise ConversionError(
            f"mapping version {mapping.version} cannot be applied: {exc}"
        ) from exc
    file = await db.get(File, document.source_file_id)
    if file is None:
        raise ConversionError("the source file of this document is gone")
    head = await document_head(db, storage, document)
    chain = streaming.head_ancestors(head, plan)
    transformer = new_transformer()
    scalars = streaming.resource_scalars(chain, plan, transformer)

    fragments: list[tuple[Entry, bytes]] = []
    for entry in sorted(entries, key=lambda e: e.ordinal):
        start, end = int(entry.source_ref["start"]), int(entry.source_ref["end"])
        fragments.append((entry, await _read_range(storage, file.s3_key, start, end)))

    def convert() -> tuple[list[tuple[Entry, dict[str, Any]]], list[dict[str, Any]]]:
        produced: list[tuple[Entry, dict[str, Any]]] = []
        skipped: list[dict[str, Any]] = []
        # With one entry descriptor, generated ids follow the source ordinal as in a full run.
        # Secondary descriptors (multiword units) share the counter, so seeding would collide.
        seed_ids = len(plan.entry_descs) == 1
        for entry, fragment in fragments:
            if seed_ids:
                streaming.seed_entry_counter(transformer, entry.ordinal + 1)
            try:
                results = streaming.convert_fragment(chain, fragment, plan, transformer)
            except etree.XMLSyntaxError as exc:
                raise ConversionError(
                    f"entry {entry.ordinal} could not be parsed on its own: {exc}"
                ) from exc
            if not results:
                skipped.append({"ordinal": entry.ordinal, "headword": entry.headword})
            produced.extend((entry, result) for result in results)
        return produced, skipped

    produced, skipped = await asyncio.to_thread(convert)
    # custom steps (M3.5): the sample shows what the full run will produce
    steps = await enabled_steps(db, document.project_id)
    step_records: list[StepRecord] = []
    if steps and produced:
        if runner is None:
            raise ConversionError(
                "this project has custom steps, which need the sandbox runner; it is not "
                "available in this context"
            )
        workdir = Path(tempfile.mkdtemp(prefix="elexifier-sample-steps-"))
        try:
            transformed, step_records = await apply_steps_to_entries(
                runner,
                storage,
                steps,
                [dmlex for _entry, dmlex in produced],
                project_id=document.project_id,
                run_id=f"sample-{uuid.uuid4().hex[:8]}",
                workdir=workdir,
                source_key=file.s3_key,
            )
        except StepFailed as exc:
            raise ConversionError(str(exc)) from exc
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        if len(transformed) != len(produced):
            raise ConversionError(
                f"the custom steps changed the number of entries ({len(produced)} in, "
                f"{len(transformed)} out); a sample needs one output per source entry"
            )
        produced = [(entry, new) for (entry, _old), new in zip(produced, transformed, strict=True)]
    validation = await asyncio.to_thread(
        validate_entries, scalars, [dmlex for _entry, dmlex in produced]
    )

    run = ConversionRun(
        project_id=document.project_id,
        document_id=document.id,
        kind=RunKind.SAMPLE.value,
        mapping_version=mapping.version,
        scope=scope or {"ordinals": [e.ordinal for e in entries]},
        status=ConversionStatus.DONE.value,
        triggered_by=triggered_by,
        finished_at=datetime.now(UTC),
        steps_snapshot=records_snapshot(steps, step_records),
    )
    db.add(run)
    await db.flush()
    converted: list[ConvertedEntry] = []
    errors = warnings = 0
    for position, (entry, dmlex) in enumerate(produced):
        issues = [issue.as_dict() for issue in validation.issues_for(position)]
        errors += sum(1 for i in issues if i["severity"] == "error")
        warnings += sum(1 for i in issues if i["severity"] != "error")
        row = ConvertedEntry(
            run_id=run.id,
            entry_id=entry.id,
            position=position,
            ordinal=entry.ordinal,
            headword=(str(dmlex.get("headword") or entry.headword or "")[:300]) or None,
            dmlex=dmlex,
            issues=issues,
            issue_count=len(issues),
        )
        db.add(row)
        converted.append(row)
    resource_issues = [issue.as_dict() for issue in validation.resource_issues]
    run.stats = {
        "requested": len(entries),
        "converted": len(converted),
        "skipped": len(skipped),
        "with_issues": sum(1 for c in converted if c.issue_count),
        "errors": errors,
        "warnings": warnings,
        "resource_issues": len(resource_issues),
        "steps": len(step_records),
    }
    run.validation_report = {
        "summary": validation.report.summary(),
        "valid": validation.report.valid,
        "error_count": validation.report.error_count,
        "limitation_count": validation.report.limitation_count,
        "resource_issues": resource_issues,
        "plan_warnings": plan.warnings,
        "resource_scalars": scalars,
    }
    await publish(
        db,
        document.project_id,
        "run.finished",
        {"run_id": str(run.id), "kind": run.kind, "mapping_version": mapping.version},
    )
    await db.flush()
    return SampleOutcome(
        run=run,
        converted=converted,
        validation=validation,
        skipped=skipped,
        plan_warnings=plan.warnings,
    )


async def queue_full_run(
    db: AsyncSession,
    *,
    document: Document,
    mapping: Mapping,
    triggered_by: uuid.UUID | None,
    options: dict[str, Any] | None = None,
    defer: Deferrer | None,
) -> ConversionRun:
    """Create a queued full run and hand it to the worker. Commits nothing."""
    if defer is None:
        raise ConversionError("background jobs are not available in this context")
    try:
        streaming.plan_for(load_mapping_spec(mapping.spec).to_desc())
    except MappingError as exc:
        raise ConversionError(
            f"mapping version {mapping.version} cannot be applied: {exc}"
        ) from exc
    running = (
        (
            await db.execute(
                select(ConversionRun).where(
                    ConversionRun.project_id == document.project_id,
                    ConversionRun.kind == RunKind.FULL.value,
                    ConversionRun.status.in_(
                        [ConversionStatus.QUEUED.value, ConversionStatus.RUNNING.value]
                    ),
                )
            )
        )
        .scalars()
        .first()
    )
    if running is not None:
        raise ConversionError(
            f"a full run is already {running.status} (run {running.id}); wait for it to finish"
        )
    steps = await enabled_steps(db, document.project_id)
    run = ConversionRun(
        project_id=document.project_id,
        document_id=document.id,
        kind=RunKind.FULL.value,
        steps_snapshot=snapshot_of(steps),
        mapping_version=mapping.version,
        scope={"options": options or {}},
        status=ConversionStatus.QUEUED.value,
        stats={"phase": "queued", "done": 0, "total": document.entry_count},
        triggered_by=triggered_by,
    )
    db.add(run)
    await db.flush()
    run.job_id = await defer(FULL_RUN_TASK, {"run_id": str(run.id)})
    await publish(
        db,
        document.project_id,
        "job.progress",
        {"kind": FULL_RUN_TASK, "run_id": str(run.id), "phase": "queued", "done": 0},
    )
    await db.flush()
    return run


def diff_dmlex(a: Any, b: Any, path: str = "") -> list[str]:
    """Human-readable differences between two converted entries (paths that changed)."""
    changes: list[str] = []
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            sub = f"{path}.{key}" if path else key
            if key not in a:
                changes.append(f"+ {sub}")
            elif key not in b:
                changes.append(f"- {sub}")
            else:
                changes.extend(diff_dmlex(a[key], b[key], sub))
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            changes.append(f"~ {path}: {len(a)} -> {len(b)} items")
        for i, (x, y) in enumerate(zip(a, b, strict=False)):
            changes.extend(diff_dmlex(x, y, f"{path}[{i}]"))
    elif a != b:
        changes.append(f"~ {path}: {_short(a)} -> {_short(b)}")
    return changes


def _short(value: Any) -> str:
    text = str(value)
    return text if len(text) <= 60 else text[:57] + "..."
