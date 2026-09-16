"""Conversion tools: sample runs with validation, and comparing two runs."""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset
from sqlalchemy import select

from elexifier_agent.contracts import ToolResult, UiAction, error_result, to_tool_return
from elexifier_agent.conversion import (
    ConversionError,
    diff_dmlex,
    mapping_version,
    run_sample,
)
from elexifier_agent.deps import AgentDeps
from elexifier_agent.tools._entries import ToolInputError, get_document, pick_entries
from elexifier_core.db.models import ConversionRun, ConvertedEntry

conversion_toolset: FunctionToolset[AgentDeps] = FunctionToolset()

DMLEX_PREVIEW_ENTRIES = 3
DMLEX_PREVIEW_CHARS = 2500
MAX_DIFF_ENTRIES = 10
MAX_DIFF_LINES = 8


@conversion_toolset.tool
async def run_conversion(
    ctx: RunContext[AgentDeps],
    kind: Literal["sample"] = "sample",
    n: int = 10,
    ordinals: list[int] | None = None,
    strategy: Literal["first", "spread", "random"] = "spread",
    query: str | None = None,
    mapping_version_number: int | None = None,
    document_id: str | None = None,
) -> ToolReturn:
    """Convert a sample of entries with the stored mapping and validate the result against the
    DMLex schema. Run it after every mapping change and look at the issues before telling the
    lexicographer the mapping is ready. The converted entries open in the preview, next to their
    source. (Full runs of the whole dictionary arrive in a later version; say so if asked.)

    Args:
        kind: Only "sample" for now.
        n: How many entries to convert (1-20) when no ordinals are given.
        ordinals: Convert exactly these entries (ordinals from xml_sample_entries or xml_search).
        strategy: How to pick entries without ordinals: first, spread across the file, or random.
        query: Only sample entries whose headword or snippet contains this text.
        mapping_version_number: Mapping version to use; omit for the latest.
        document_id: The document to convert from. Omit when the project has one.
    """
    if kind != "sample":
        return error_result("only kind='sample' is available in this version")
    deps = ctx.deps
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
