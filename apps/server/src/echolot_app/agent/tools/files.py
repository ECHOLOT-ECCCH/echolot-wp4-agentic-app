"""Tools that look at the workspace's files: what is there, a profile per column, samples,
search and distinct values. The model sees at most 20 records per call and at most 4,000
characters per record (docs/PLAN.md 7.10)."""

from __future__ import annotations

import json
import uuid
from collections import Counter
from typing import Any, Literal

import anyio
from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset
from sqlalchemy import select

from echolot_app.agent.contracts import ToolResult, UiAction, error_result, to_tool_return
from echolot_app.agent.deps import AgentDeps
from echolot_app.db import File
from echolot_app.records import RecordsReader

MAX_RECORDS = 20
MAX_RECORD_CHARS = 4000
MAX_PROFILE_COLUMNS = 40
MAX_PROFILE_ROWS = 1_000_000
MAX_DISTINCT_TRACKED = 10_000
MAX_VALUE_CHARS = 80
MAX_SEARCH_SCAN_MATCHES = 10_000

files_toolset: FunctionToolset[AgentDeps] = FunctionToolset()


class FileToolError(Exception):
    pass


async def _records_file(deps: AgentDeps, file_id: str) -> tuple[File, RecordsReader]:
    try:
        fid = uuid.UUID(file_id.strip())
    except ValueError as exc:
        raise FileToolError(f"{file_id!r} is not a file id; use list_files") from exc
    async with deps.session() as db:
        record = await db.get(File, fid)
    if record is None or record.workspace_id != deps.workspace.id:
        raise FileToolError(f"no file {file_id} in this workspace; use list_files")
    if not record.meta.get("format") or "record_count" not in record.meta:
        reason = record.meta.get("error") or "it is not a CSV, TSV, JSON or JSONL file"
        raise FileToolError(f"{record.name} has no records: {reason}")
    reader = RecordsReader(
        deps.storage.local_path(record.rel_path), int(record.meta["record_count"])
    )
    return record, reader


def clip(value: str, limit: int = MAX_VALUE_CHARS) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def as_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def clip_record(
    record: dict[str, Any], limit: int = MAX_RECORD_CHARS
) -> tuple[dict[str, Any], bool]:
    """Shorten long string values until the record fits ``limit`` characters as JSON."""
    if len(json.dumps(record, ensure_ascii=False)) <= limit:
        return record, False
    per_value = max(40, limit // max(len(record), 1))
    clipped = {
        key: (
            value[:per_value] + "…" if isinstance(value, str) and len(value) > per_value else value
        )
        for key, value in record.items()
    }
    if len(json.dumps(clipped, ensure_ascii=False)) > limit:
        clipped = {k: clip(as_text(v), per_value) for k, v in clipped.items()}
    return clipped, True


def _shown(rows: list[tuple[int, dict[str, Any]]]) -> tuple[list[dict[str, Any]], bool]:
    shown: list[dict[str, Any]] = []
    any_clipped = False
    for row, record in rows:
        clipped, was_clipped = clip_record(record)
        any_clipped |= was_clipped
        shown.append({"row": row, "record": clipped})
    return shown, any_clipped


def _open(record: File, row: int | None = None) -> UiAction:
    return UiAction(type="open_file", file_id=str(record.id), row=row)


@files_toolset.tool
async def list_files(ctx: RunContext[AgentDeps]) -> ToolReturn:
    """List the files in this workspace with their format, record count and columns. Inputs are
    the user's uploads; results and exports come from runs."""
    async with ctx.deps.session() as db:
        rows = (
            (
                await db.execute(
                    select(File)
                    .where(File.workspace_id == ctx.deps.workspace.id)
                    .order_by(File.created_at)
                )
            )
            .scalars()
            .all()
        )
    files = []
    for f in rows:
        item: dict[str, Any] = {
            "file_id": str(f.id),
            "name": f.name,
            "kind": f.kind,
            "size_bytes": f.size,
            "format": f.meta.get("format"),
        }
        if f.meta.get("format"):
            columns = list(f.meta.get("columns") or [])
            item["record_count"] = f.meta.get("record_count")
            item["columns"] = columns[:MAX_PROFILE_COLUMNS]
            if len(columns) > MAX_PROFILE_COLUMNS:
                item["more_columns"] = len(columns) - MAX_PROFILE_COLUMNS
        if f.meta.get("error"):
            item["error"] = f.meta["error"]
        files.append(item)
    name = ctx.deps.workspace.name
    if files:
        summary = f"{len(files)} file(s) in workspace '{name}'"
    else:
        summary = f"Workspace '{name}' has no files yet. Ask the user to upload one."
    return to_tool_return(ToolResult(summary=summary, data={"files": files}))


def _profile(reader: RecordsReader, columns: list[str]) -> dict[str, Any]:
    wanted = columns[:MAX_PROFILE_COLUMNS]
    filled: Counter[str] = Counter()
    lengths: dict[str, list[int]] = {c: [] for c in wanted}  # [min, max]
    distinct: dict[str, Counter[str]] = {c: Counter() for c in wanted}
    overflow: set[str] = set()
    types: dict[str, Counter[str]] = {c: Counter() for c in wanted}
    scanned = 0
    for _row, record in reader.iter_from(0):
        if scanned >= MAX_PROFILE_ROWS:
            break
        scanned += 1
        for column in wanted:
            value = record.get(column)
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            filled[column] += 1
            types[column][type(value).__name__] += 1
            text = as_text(value)
            bounds = lengths[column]
            if not bounds:
                bounds.extend([len(text), len(text)])
            else:
                bounds[0] = min(bounds[0], len(text))
                bounds[1] = max(bounds[1], len(text))
            counter = distinct[column]
            if text in counter or len(counter) < MAX_DISTINCT_TRACKED:
                counter[text] += 1
            else:
                overflow.add(column)
    profile = []
    for column in wanted:
        counter = distinct[column]
        entry: dict[str, Any] = {
            "column": column,
            "filled": filled[column],
            "fill_rate": round(filled[column] / scanned, 3) if scanned else 0,
            "distinct": f">={MAX_DISTINCT_TRACKED}" if column in overflow else len(counter),
        }
        if lengths[column]:
            entry["length_min"], entry["length_max"] = lengths[column]
        if set(types[column]) - {"str"}:
            entry["types"] = dict(types[column])
        repeated = [(v, n) for v, n in counter.most_common(5) if n > 1]
        if repeated:
            entry["top_values"] = [{"value": clip(v), "count": n} for v, n in repeated]
        entry["examples"] = [clip(v) for v in list(counter)[:3]]
        profile.append(entry)
    return {"scanned": scanned, "columns": profile}


@files_toolset.tool
async def profile_file(ctx: RunContext[AgentDeps], file_id: str) -> ToolReturn:
    """Profile a records file column by column: how often each column is filled, how many
    distinct values it has, the most frequent values, a few examples and the value lengths.
    Use it first on a new file, before sampling records.

    Args:
        file_id: The file id from list_files.
    """
    try:
        record, reader = await _records_file(ctx.deps, file_id)
    except FileToolError as exc:
        return error_result(str(exc))
    columns = list(record.meta.get("columns") or [])
    result = await anyio.to_thread.run_sync(_profile, reader, columns)
    notes = []
    if len(columns) > MAX_PROFILE_COLUMNS:
        notes.append(f"only the first {MAX_PROFILE_COLUMNS} of {len(columns)} columns profiled")
    if result["scanned"] < reader.record_count:
        notes.append(f"only the first {result['scanned']:,} records profiled")
    for key in ("ragged_rows", "invalid_count"):
        if record.meta.get(key):
            notes.append(f"{key.replace('_', ' ')}: {record.meta[key]}")
    data = {
        "file": record.name,
        "format": record.meta.get("format"),
        "record_count": reader.record_count,
        **result,
    }
    if notes:
        data["notes"] = notes
    summary = f"{record.name}: {reader.record_count:,} records, {len(columns)} columns"
    return to_tool_return(ToolResult(summary=summary, data=data, ui_actions=[_open(record)]))


@files_toolset.tool
async def sample_records(
    ctx: RunContext[AgentDeps],
    file_id: str,
    n: int = 5,
    strategy: Literal["first", "random", "spread"] = "spread",
    offset: int = 0,
) -> ToolReturn:
    """Show a few records of a file. Long values are shortened.

    Args:
        file_id: The file id from list_files.
        n: How many records, at most 20.
        strategy: "first" (from offset on), "random", or "spread" (evenly across the file).
        offset: The first row for strategy "first" (rows are 0-based).
    """
    try:
        record, reader = await _records_file(ctx.deps, file_id)
    except FileToolError as exc:
        return error_result(str(exc))
    n = max(1, min(n, MAX_RECORDS))
    if strategy == "first":
        rows = await anyio.to_thread.run_sync(reader.page, max(offset, 0), n)
    else:
        rows = await anyio.to_thread.run_sync(reader.sample, n, strategy)
    shown, clipped = _shown(rows)
    data: dict[str, Any] = {"file": record.name, "record_count": reader.record_count}
    data["records"] = shown
    if clipped:
        data["note"] = "long values were shortened"
    first = rows[0][0] if rows else None
    summary = f"{len(shown)} of {reader.record_count:,} records of {record.name} ({strategy})"
    return to_tool_return(ToolResult(summary=summary, data=data, ui_actions=[_open(record, first)]))


def _search(
    reader: RecordsReader, query: str, column: str | None, limit: int
) -> tuple[list[tuple[int, dict[str, Any]]], int]:
    needle = query.casefold()
    hits: list[tuple[int, dict[str, Any]]] = []
    total = 0
    for row, record in reader.iter_from(0):
        values = [record.get(column)] if column else list(record.values())
        if any(v is not None and needle in as_text(v).casefold() for v in values):
            total += 1
            if len(hits) < limit:
                hits.append((row, record))
            if total >= MAX_SEARCH_SCAN_MATCHES:
                break
    return hits, total


@files_toolset.tool
async def search_records(
    ctx: RunContext[AgentDeps],
    file_id: str,
    query: str,
    column: str | None = None,
    limit: int = 10,
) -> ToolReturn:
    """Find records containing a text (case-insensitive), in one column or in any.

    Args:
        file_id: The file id from list_files.
        query: The text to look for.
        column: Only look in this column; all columns when omitted.
        limit: How many matching records to show, at most 20.
    """
    try:
        record, reader = await _records_file(ctx.deps, file_id)
    except FileToolError as exc:
        return error_result(str(exc))
    if not query.strip():
        return error_result("query is empty")
    if column and column not in (record.meta.get("columns") or []):
        return error_result(f"no column {column!r} in {record.name}")
    limit = max(1, min(limit, MAX_RECORDS))
    hits, total = await anyio.to_thread.run_sync(_search, reader, query, column, limit)
    shown, _ = _shown(hits)
    more = "+" if total >= MAX_SEARCH_SCAN_MATCHES else ""
    summary = f"{total:,}{more} record(s) of {record.name} contain {query!r}"
    data = {"file": record.name, "matches": total, "records": shown}
    actions = [_open(record, hits[0][0])] if hits else []
    return to_tool_return(ToolResult(summary=summary, data=data, ui_actions=actions))


def _distinct(reader: RecordsReader, column: str) -> tuple[Counter[str], int, bool]:
    counter: Counter[str] = Counter()
    empty = 0
    overflow = False
    for _row, record in reader.iter_from(0):
        value = record.get(column)
        if value is None or (isinstance(value, str) and not value.strip()):
            empty += 1
            continue
        text = as_text(value)
        if text in counter or len(counter) < MAX_DISTINCT_TRACKED:
            counter[text] += 1
        else:
            overflow = True
    return counter, empty, overflow


@files_toolset.tool
async def distinct_values(
    ctx: RunContext[AgentDeps], file_id: str, column: str, limit: int = 30
) -> ToolReturn:
    """The distinct values of one column with their counts, most frequent first.

    Args:
        file_id: The file id from list_files.
        column: The column name.
        limit: How many values to show, at most 50.
    """
    try:
        record, reader = await _records_file(ctx.deps, file_id)
    except FileToolError as exc:
        return error_result(str(exc))
    if column not in (record.meta.get("columns") or []):
        return error_result(f"no column {column!r} in {record.name}")
    limit = max(1, min(limit, 50))
    counter, empty, overflow = await anyio.to_thread.run_sync(_distinct, reader, column)
    distinct = f">={MAX_DISTINCT_TRACKED}" if overflow else len(counter)
    data = {
        "file": record.name,
        "column": column,
        "distinct": distinct,
        "empty": empty,
        "values": [{"value": clip(v, 200), "count": n} for v, n in counter.most_common(limit)],
    }
    summary = f"{column}: {distinct} distinct value(s), {empty:,} empty"
    return to_tool_return(ToolResult(summary=summary, data=data, ui_actions=[_open(record)]))
