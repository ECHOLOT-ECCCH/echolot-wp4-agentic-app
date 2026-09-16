"""Conversion tools: sample runs with validation, and comparing two runs."""

from __future__ import annotations

import asyncio
import json
import tempfile
import uuid
from pathlib import Path
from typing import Any, Literal

import anyio
from pydantic_ai import ApprovalRequired, RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset
from sqlalchemy import select

from elexifier_agent.contracts import (
    ArtifactRef,
    ToolResult,
    UiAction,
    error_result,
    to_tool_return,
)
from elexifier_agent.conversion import (
    ConversionError,
    diff_dmlex,
    mapping_version,
    queue_full_run,
    run_sample,
)
from elexifier_agent.deps import AgentDeps
from elexifier_agent.tools._entries import ToolInputError, get_document, pick_entries
from elexifier_core.db.models import (
    ConversionRun,
    ConversionStatus,
    ConvertedEntry,
    File,
    FileKind,
    RunKind,
)
from elexifier_pipeline.dmlex.validator import validate_xml

conversion_toolset: FunctionToolset[AgentDeps] = FunctionToolset()

DMLEX_PREVIEW_ENTRIES = 3
DMLEX_PREVIEW_CHARS = 2500
MAX_DIFF_ENTRIES = 10
MAX_DIFF_LINES = 8


@conversion_toolset.tool
async def run_conversion(
    ctx: RunContext[AgentDeps],
    kind: Literal["sample", "full"] = "sample",
    n: int = 10,
    ordinals: list[int] | None = None,
    strategy: Literal["first", "spread", "random"] = "spread",
    query: str | None = None,
    mapping_version_number: int | None = None,
    document_id: str | None = None,
    number_homographs: bool = True,
) -> ToolReturn:
    """Convert entries with the stored mapping and validate the result against the DMLex schema.

    kind="sample" converts up to 20 entries right away and returns their issues; the entries
    open in the preview next to their source. Run it after every mapping change.
    kind="full" converts the whole dictionary in the background (the lexicographer approves it
    first): it writes DMLex XML and JSON output files, validates them and stores a report. Only
    propose a full run after a clean sample. Progress shows in the run panel; call run_stats
    with the run id to see the outcome when the lexicographer says it has finished.

    Args:
        kind: "sample" or "full".
        n: Sample size (1-20) when no ordinals are given.
        ordinals: Convert exactly these entries (ordinals from xml_sample_entries or xml_search).
        strategy: How to pick sample entries: first, spread across the file, or random.
        query: Only sample entries whose headword or snippet contains this text.
        mapping_version_number: Mapping version to use; omit for the latest.
        document_id: The document to convert from. Omit when the project has one.
        number_homographs: Full runs only: give duplicate headwords with the same part of
            speech homograph numbers 1..n when the source has none (DMLex requires them).
    """
    deps = ctx.deps
    if kind == "full" and not ctx.tool_call_approved:
        raise ApprovalRequired(
            metadata={
                "reason": "converts the whole dictionary and writes output files",
                "mapping_version": mapping_version_number,
            }
        )
    async with deps.session() as db:
        try:
            document = await get_document(deps, db, document_id)
            mapping = await mapping_version(db, deps.project.id, mapping_version_number)
            if mapping is None:
                raise ToolInputError(
                    "no mapping stored yet; write one with mapping_write first"
                    if mapping_version_number is None
                    else f"mapping version {mapping_version_number} does not exist"
                )
            if kind == "full":
                run = await queue_full_run(
                    db,
                    document=document,
                    mapping=mapping,
                    triggered_by=deps.user.id,
                    options={"number_homographs": number_homographs},
                    defer=deps.defer_job,
                )
                await db.commit()
                run_id = str(run.id)
                return to_tool_return(
                    ToolResult(
                        summary=f"Full run queued with mapping v{mapping.version} "
                        f"({document.entry_count:,} entries). It runs in the background; the "
                        "run panel shows progress. Use run_stats when it has finished.",
                        data={
                            "run_id": run_id,
                            "mapping_version": mapping.version,
                            "status": run.status,
                            "entries": document.entry_count,
                            "options": {"number_homographs": number_homographs},
                        },
                        ui_actions=[UiAction(type="open_run", run_id=run_id)],
                    )
                )
            entries = await pick_entries(
                db, document, n=n, strategy=strategy, ordinals=ordinals, query=query
            )
            if not entries:
                raise ToolInputError("no entries match; check the ordinals or the query")
            outcome = await run_sample(
                db,
                deps.storage,
                document=document,
                mapping=mapping,
                entries=entries,
                triggered_by=deps.user.id,
                scope={
                    "ordinals": [e.ordinal for e in entries],
                    "strategy": None if ordinals else strategy,
                    "query": query,
                },
            )
            await db.commit()
        except (ToolInputError, ConversionError) as exc:
            return error_result(str(exc))
        run, converted = outcome.run, outcome.converted
        run_id, stats, report = str(run.id), dict(run.stats), dict(run.validation_report)

        rows: list[dict[str, Any]] = []
        for index, row in enumerate(converted):
            item: dict[str, Any] = {
                "ordinal": row.ordinal,
                "headword": row.headword,
                "entry_id": str(row.entry_id) if row.entry_id else None,
                "issues": row.issues,
            }
            if index < DMLEX_PREVIEW_ENTRIES:
                serialized = json.dumps(row.dmlex, ensure_ascii=False)
                item["dmlex"] = (
                    row.dmlex
                    if len(serialized) <= DMLEX_PREVIEW_CHARS
                    else {"_clipped": serialized[:DMLEX_PREVIEW_CHARS] + "..."}
                )
            rows.append(item)
        focus = next((r for r in converted if r.issue_count), converted[0] if converted else None)

    with_issues = stats.get("with_issues", 0)
    summary = (
        f"Sample run with mapping v{mapping.version}: {stats['converted']} entries converted "
        f"from {stats['requested']} selected"
        + (f", {stats['skipped']} produced nothing" if stats.get("skipped") else "")
        + f"; {with_issues} with issues ({stats['errors']} errors, {stats['warnings']} warnings)"
        + (
            f"; {stats['resource_issues']} resource-level issue(s)"
            if stats.get("resource_issues")
            else ""
        )
        + ". "
        + (
            "Open the entries in the preview and fix the mapping before the next sample."
            if with_issues or stats.get("resource_issues")
            else "The sample is schema-valid."
        )
    )
    actions: list[UiAction] = [UiAction(type="open_run", run_id=run_id)]
    if focus is not None and focus.entry_id:
        actions.append(
            UiAction(
                type="open_entry",
                entry_id=str(focus.entry_id),
                document_id=str(document.id),
                run_id=run_id,
            )
        )
    return to_tool_return(
        ToolResult(
            summary=summary,
            data={
                "run_id": run_id,
                "mapping_version": mapping.version,
                "stats": stats,
                "validation": report.get("summary"),
                "resource_issues": report.get("resource_issues", []),
                "plan_warnings": report.get("plan_warnings", []),
                "skipped": outcome.skipped,
                "entries": rows,
                "note": "generated ids in samples are provisional; entries after the first "
                f"{DMLEX_PREVIEW_ENTRIES} only list issues, open them in the preview for the "
                "full output",
            },
            ui_actions=actions,
        )
    )


async def _run_of(ctx: RunContext[AgentDeps], db: Any, run_id: str) -> ConversionRun:
    try:
        rid = uuid.UUID(run_id)
    except ValueError as exc:
        raise ToolInputError("run_id must be an id returned by run_conversion") from exc
    run = await db.get(ConversionRun, rid)
    if run is None or run.project_id != ctx.deps.project.id:
        raise ToolInputError("no such run in this project")
    return run


def _output_artifacts(run: ConversionRun) -> list[ArtifactRef]:
    refs = [
        ArtifactRef(file_id=str(f["file_id"]), name=str(f["name"]), kind="export")
        for f in (run.stats or {}).get("output_files", [])
    ]
    report_id = (run.stats or {}).get("report_file_id")
    if report_id:
        refs.append(ArtifactRef(file_id=str(report_id), name="validation report", kind="report"))
    return refs


@conversion_toolset.tool
async def run_stats(ctx: RunContext[AgentDeps], run_id: str) -> ToolReturn:
    """Status, statistics and validation outcome of a run: progress while it runs; entry counts,
    issue counts by kind, the first schema errors and the output files when it is done.

    Args:
        run_id: The run id from run_conversion.
    """
    async with ctx.deps.session() as db:
        try:
            run = await _run_of(ctx, db, run_id)
        except ToolInputError as exc:
            return error_result(str(exc))
        stats = dict(run.stats or {})
        report = dict(run.validation_report or {})
        status_, kind, version, error = run.status, run.kind, run.mapping_version, run.error
        artifacts = _output_artifacts(run)
    if status_ in (ConversionStatus.QUEUED.value, ConversionStatus.RUNNING.value):
        done, total = stats.get("done", 0), stats.get("total")
        summary = (
            f"{kind.capitalize()} run with mapping v{version} is {status_}: "
            f"{stats.get('phase', status_)}, {done:,}"
            + (f" of {total:,}" if total else "")
            + " entries."
        )
    elif status_ == ConversionStatus.FAILED.value:
        summary = f"{kind.capitalize()} run with mapping v{version} failed: {error}"
    else:
        summary = (
            f"{kind.capitalize()} run with mapping v{version} done: "
            f"{stats.get('converted', 0):,} entries, {stats.get('with_issues', 0):,} with issues "
            f"({stats.get('errors', 0):,} errors, {stats.get('warnings', 0):,} warnings); "
            f"{report.get('summary', '')}"
            + (
                f"; {stats['homographs_numbered']} homograph number(s) assigned"
                if stats.get("homographs_numbered")
                else ""
            )
            + "."
        )
    return to_tool_return(
        ToolResult(
            summary=summary[:500],
            data={
                "run_id": run_id,
                "kind": kind,
                "status": status_,
                "mapping_version": version,
                "stats": {k: v for k, v in stats.items() if k not in ("output_files",)},
                "validation": {
                    k: v
                    for k, v in report.items()
                    if k in ("summary", "valid", "error_count", "limitation_count", "issue_codes")
                },
                "resource_issues": report.get("resource_issues", []),
                "plan_warnings": report.get("plan_warnings", []),
                "first_errors": (report.get("errors") or [])[:15],
                "output_files": stats.get("output_files", []),
                "error": error,
            },
            artifacts=artifacts,
            ui_actions=[UiAction(type="open_run", run_id=run_id)],
        )
    )


@conversion_toolset.tool
async def export(
    ctx: RunContext[AgentDeps], run_id: str, format: Literal["xml", "json"] = "xml"
) -> ToolReturn:
    """Point the lexicographer at the DMLex output of a finished full run for download.

    Args:
        run_id: The run id of a finished full run.
        format: "xml" (DMLex XML, schema-validated) or "json" (DMLex JSON).
    """
    async with ctx.deps.session() as db:
        try:
            run = await _run_of(ctx, db, run_id)
        except ToolInputError as exc:
            return error_result(str(exc))
        if run.kind != RunKind.FULL.value:
            return error_result("only full runs produce output files; run kind='full' first")
        if run.status != ConversionStatus.DONE.value:
            return error_result(f"the run is {run.status}; outputs exist once it is done")
        outputs = [f for f in (run.stats or {}).get("output_files", []) if f["format"] == format]
        if not outputs:
            return error_result(f"no {format} output for this run")
        file = await db.get(File, uuid.UUID(str(outputs[0]["file_id"])))
        if file is None:
            return error_result("the output file was deleted")
        valid = (run.validation_report or {}).get("valid")
        version = run.mapping_version
        name, size, file_id = file.name, file.size, str(file.id)
    return to_tool_return(
        ToolResult(
            summary=f"DMLex {format.upper()} export of mapping v{version}: {name} "
            f"({size:,} bytes){'' if valid else ', with validation errors (see the report)'}. "
            "The lexicographer can download it from the preview or the files list.",
            data={
                "file_id": file_id,
                "name": name,
                "size": size,
                "format": format,
                "valid": valid,
                "run_id": run_id,
            },
            artifacts=[ArtifactRef(file_id=file_id, name=name, kind="export")],
            ui_actions=[UiAction(type="open_artifact", file_id=file_id)],
        )
    )


@conversion_toolset.tool
async def validate_dmlex(
    ctx: RunContext[AgentDeps], run_id: str | None = None, file_id: str | None = None
) -> ToolReturn:
    """Validate DMLex XML against the schema: the XML output of a finished full run, or any XML
    file in the project (for example a DMLex file the lexicographer uploaded).

    Args:
        run_id: A finished full run; its XML output is validated.
        file_id: An XML file in the project (from list_files) to validate instead.
    """
    deps = ctx.deps
    async with deps.session() as db:
        try:
            if file_id:
                try:
                    file = await db.get(File, uuid.UUID(file_id))
                except ValueError:
                    file = None
                if file is None or file.project_id != deps.project.id:
                    raise ToolInputError("no such file in this project; call list_files")
            elif run_id:
                run = await _run_of(ctx, db, run_id)
                if run.output_file_id is None:
                    raise ToolInputError("the run has no XML output (not a finished full run)")
                file = await db.get(File, run.output_file_id)
                if file is None:
                    raise ToolInputError("the output file was deleted")
            else:
                raise ToolInputError("pass run_id or file_id")
        except ToolInputError as exc:
            return error_result(str(exc))
        key, name, kind = file.s3_key, file.name, file.kind
        fid = str(file.id)
    with tempfile.NamedTemporaryFile(
        prefix="elexifier-validate-", suffix=".xml", delete=False
    ) as fh:
        async for chunk in deps.storage.open(key):
            fh.write(chunk)
        path = Path(fh.name)
    try:
        report = await asyncio.to_thread(validate_xml, path, max_errors=2000)
    except Exception as exc:  # malformed XML, not a DMLex document
        return error_result(f"{name} could not be validated: {exc}"[:400])
    finally:
        await anyio.Path(path).unlink(missing_ok=True)
    errors = [
        {"entry_index": e.entry_index, "path": e.path, "reason": e.reason} for e in report.errors
    ]
    return to_tool_return(
        ToolResult(
            summary=f"{name}: {report.summary()}.",
            data={
                "file_id": fid,
                "file_kind": kind,
                "valid": report.valid,
                "error_count": report.error_count,
                "limitation_count": report.limitation_count,
                "first_errors": errors[:20],
            },
            artifacts=[ArtifactRef(file_id=fid, name=name, kind="export")]
            if kind == FileKind.OUTPUT.value
            else [],
        )
    )


@conversion_toolset.tool
async def diff_runs(ctx: RunContext[AgentDeps], run_a: str, run_b: str) -> ToolReturn:
    """Compare the converted entries of two runs (usually the same sample with two mapping
    versions): which entries changed, how, and whether issues went up or down.

    Args:
        run_a: The earlier run id.
        run_b: The later run id.
    """
    try:
        ids = (uuid.UUID(run_a), uuid.UUID(run_b))
    except ValueError:
        return error_result("run ids must be the ids returned by run_conversion")
    async with ctx.deps.session() as db:
        runs = {
            r.id: r
            for r in (
                await db.execute(
                    select(ConversionRun).where(
                        ConversionRun.id.in_(ids),
                        ConversionRun.project_id == ctx.deps.project.id,
                    )
                )
            ).scalars()
        }
        if len(runs) != 2 or ids[0] == ids[1]:
            return error_result("both runs must exist in this project and differ")
        rows = (
            (
                await db.execute(
                    select(ConvertedEntry)
                    .where(ConvertedEntry.run_id.in_(ids))
                    .order_by(ConvertedEntry.position)
                )
            )
            .scalars()
            .all()
        )
    by_run: dict[uuid.UUID, dict[Any, ConvertedEntry]] = {ids[0]: {}, ids[1]: {}}
    for row in rows:
        key = row.entry_id or ("pos", row.position)
        # several output entries per source entry (multiword units): key by position too
        while key in by_run[row.run_id]:
            key = (key, "+")
        by_run[row.run_id][key] = row
    a_rows, b_rows = by_run[ids[0]], by_run[ids[1]]
    common = [k for k in a_rows if k in b_rows]
    changed: list[dict[str, Any]] = []
    same = 0
    issue_delta = 0
    for key in common:
        a, b = a_rows[key], b_rows[key]
        issue_delta += b.issue_count - a.issue_count
        lines = diff_dmlex(a.dmlex, b.dmlex)
        if not lines:
            same += 1
            continue
        if len(changed) < MAX_DIFF_ENTRIES:
            changed.append(
                {
                    "ordinal": b.ordinal,
                    "headword": b.headword,
                    "entry_id": str(b.entry_id) if b.entry_id else None,
                    "issues_before": a.issue_count,
                    "issues_after": b.issue_count,
                    "changes": lines[:MAX_DIFF_LINES]
                    + (
                        [f"... {len(lines) - MAX_DIFF_LINES} more"]
                        if len(lines) > MAX_DIFF_LINES
                        else []
                    ),
                }
            )
    only_a = [a_rows[k].headword for k in a_rows if k not in b_rows]
    only_b = [b_rows[k].headword for k in b_rows if k not in a_rows]
    total_changed = len(common) - same
    ra, rb = runs[ids[0]], runs[ids[1]]
    summary = (
        f"Run with mapping v{ra.mapping_version} vs run with mapping v{rb.mapping_version}: "
        f"{total_changed} of {len(common)} shared entries changed, {same} identical"
        + (f", {len(only_a)} only in the first" if only_a else "")
        + (f", {len(only_b)} only in the second" if only_b else "")
        + f"; issues {'+' if issue_delta > 0 else ''}{issue_delta}."
    )
    return to_tool_return(
        ToolResult(
            summary=summary,
            data={
                "run_a": {
                    "id": str(ra.id),
                    "mapping_version": ra.mapping_version,
                    "stats": ra.stats,
                },
                "run_b": {
                    "id": str(rb.id),
                    "mapping_version": rb.mapping_version,
                    "stats": rb.stats,
                },
                "changed": changed,
                "changed_total": total_changed,
                "identical": same,
                "only_in_a": only_a[:20],
                "only_in_b": only_b[:20],
                "issue_delta": issue_delta,
            },
            ui_actions=[UiAction(type="open_run", run_id=str(rb.id))],
        )
    )
