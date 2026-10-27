"""Creating, submitting, following and reading runs (docs/PLAN.md 7.2-7.5)."""

from __future__ import annotations

import csv
import io
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from echolot_app.db import File, FileKind, Run, RunStatus, Script, utcnow
from echolot_app.records import RecordsReader, normalize, runner_line
from echolot_app.runs.executor import ExecState, Executor, app_status
from echolot_app.runs.tokens import new_token
from echolot_app.settings import Settings
from echolot_app.storage import LocalStorage, run_key

log = structlog.get_logger(__name__)

DEFAULT_CONCURRENCY = 4
MAX_CONCURRENCY = 32
RESERVED = ("_row", "_status", "_error", "_ms")


class RunError(Exception):
    """A run cannot be created or read as asked; the message is for the caller."""


def records_reader(storage: LocalStorage, file: File) -> RecordsReader:
    if not file.meta.get("format") or "record_count" not in file.meta:
        raise RunError(f"{file.name} has no records (CSV, TSV, JSON or JSONL)")
    return RecordsReader(storage.local_path(file.rel_path), int(file.meta["record_count"]))


def selected_total(run: Run, record_count: int) -> int:
    rows = run.selection.get("rows")
    total = len(rows) if isinstance(rows, list) else record_count
    return total if run.record_limit is None else min(total, run.record_limit)


# --- creating and submitting -----------------------------------------------------------------


@dataclass
class RunRequest:
    input_file: File
    script: Script
    record_limit: int | None = None
    concurrency: int | None = None
    script_params: dict[str, Any] | None = None
    purpose: str | None = None
    submitted_by: str = "user"
    selection_rows: list[int] | None = None
    parent_run_id: uuid.UUID | None = None


def runner_params(settings: Settings, run: Run, script: Script, token: str) -> dict[str, Any]:
    """docs/PLAN.md 7.2."""
    base = f"{settings.public_url.rstrip('/')}/runner/runs/{run.id}"
    return {
        "run_id": str(run.id),
        "script": script.code,
        "script_sha256": script.sha256,
        "script_params": run.script_params,
        "services": list(script.services),
        "input_url": f"{base}/input",
        "progress_url": f"{base}/progress",
        "result_url": f"{base}/result",
        "token": token,
        "limit": run.record_limit,
        "concurrency": run.concurrency,
    }


async def create_run(
    db: AsyncSession,
    storage: LocalStorage,
    settings: Settings,
    executor: Executor,
    request: RunRequest,
) -> Run:
    """Store the run, hand it to the executor and return it (committed)."""
    reader = records_reader(storage, request.input_file)
    if request.record_limit is not None and request.record_limit < 1:
        raise RunError("limit must be at least 1")
    concurrency = max(1, min(request.concurrency or DEFAULT_CONCURRENCY, MAX_CONCURRENCY))
    token, hashed = new_token()
    run = Run(
        workspace_id=request.input_file.workspace_id,
        script_id=request.script.id,
        input_file_id=request.input_file.id,
        parent_run_id=request.parent_run_id,
        selection={"rows": request.selection_rows} if request.selection_rows is not None else {},
        record_limit=request.record_limit,
        concurrency=concurrency,
        script_params=dict(request.script_params or {}),
        purpose=(request.purpose or "").strip() or None,
        status=RunStatus.QUEUED.value,
        executor=executor.name,
        token_hash=hashed,
        submitted_by=request.submitted_by,
    )
    db.add(run)
    await db.flush()
    total = selected_total(run, reader.record_count)
    if total == 0:
        raise RunError("the run would have no records")
    run.progress = {"done": 0, "total": total, "ok": 0, "error": 0}
    await db.commit()  # the runner may ask for its input before submit returns
    try:
        run.external_id = await executor.submit(
            str(run.id), runner_params(settings, run, request.script, token)
        )
    except Exception as exc:
        log.warning("run.submit_failed", run=str(run.id), error=str(exc))
        run.status = RunStatus.FAILED.value
        run.error = f"the run could not be started: {exc}"
        run.finished_at = utcnow()
    await db.commit()
    return run


# --- following --------------------------------------------------------------------------------


async def refresh_run(db: AsyncSession, executor: Executor, run: Run) -> bool:
    """Ask the executor about a run that is not finished; return True when it changed."""
    if RunStatus(run.status).is_terminal or run.external_id is None:
        return False
    try:
        state = await executor.status(run.external_id)
    except Exception as exc:
        log.warning("run.status_failed", run=str(run.id), error=str(exc))
        return False
    return apply_state(run, state)


def apply_state(run: Run, state: ExecState) -> bool:
    status = app_status(state, result_received=run.result_file_id is not None)
    if status is RunStatus.QUEUED and run.status == RunStatus.RUNNING.value:
        status = RunStatus.RUNNING  # the runner already reported progress; never step back
    changed = status.value != run.status
    if status is RunStatus.RUNNING and run.started_at is None:
        run.started_at = utcnow()
        changed = True
    if not changed:
        return False
    run.status = status.value
    if status.is_terminal:
        run.finished_at = utcnow()
        if status is RunStatus.FAILED:
            if state.kind == "COMPLETED":
                run.error = "the run finished without sending its results"
            else:
                run.error = state.message or f"the executor reports {state.kind.lower()}"
    return True


# --- the runner's input and result ------------------------------------------------------------


def input_lines(reader: RecordsReader, run: Run) -> Iterator[bytes]:
    """The run's records as the runner reads them (docs/PLAN.md 7.3)."""
    rows = run.selection.get("rows")
    limit = run.record_limit
    if isinstance(rows, list):
        picked = rows if limit is None else rows[:limit]
        for row, record in reader.rows([int(r) for r in picked]):
            yield runner_line(row, record)
        return
    for count, (row, record) in enumerate(reader.iter_from(0)):
        if limit is not None and count >= limit:
            return
        yield runner_line(row, record)


def _count_statuses(path: Path, record_count: int) -> dict[str, int]:
    counts = {"total": 0, "ok": 0, "error": 0}
    for _row, line in RecordsReader(path, record_count).iter_from(0):
        counts["total"] += 1
        counts["ok" if line.get("_status") == "ok" else "error"] += 1
    return counts


async def store_result(
    db: AsyncSession, storage: LocalStorage, run: Run, body: AsyncIterator[bytes]
) -> File:
    """Keep the uploaded results as a file (normalized, so it opens as records) and count them."""
    key = run_key(str(run.workspace_id), str(run.id), "result.jsonl")
    stat = await storage.put(key, body, content_type="application/x-ndjson")
    path = storage.local_path(key)
    meta = (await anyio.to_thread.run_sync(normalize, path, "jsonl")).as_dict()
    counts = await anyio.to_thread.run_sync(_count_statuses, path, int(meta["record_count"]))
    previous = await db.get(File, run.result_file_id) if run.result_file_id else None
    if previous is not None:
        await db.delete(previous)
    result = File(
        workspace_id=run.workspace_id,
        kind=FileKind.RESULT.value,
        name=f"result-{str(run.id)[:8]}.jsonl",
        rel_path=key,
        size=stat.size,
        sha256=stat.sha256,
        mime="application/x-ndjson",
        meta={**meta, "run_id": str(run.id)},
    )
    db.add(result)
    await db.flush()
    run.result_file_id = result.id
    run.counts = counts
    run.progress = {**run.progress, "done": counts["total"], **counts}
    return result


# --- reading results ----------------------------------------------------------------------------


def output_columns(result: File) -> list[str]:
    return [c for c in result.meta.get("columns") or [] if not str(c).startswith("_")]


def _row_out(line: dict[str, Any], record: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "row": int(line["_row"]),
        "status": line.get("_status"),
        "error": line.get("_error"),
        "ms": line.get("_ms"),
        "input": record or {},
        "output": {k: v for k, v in line.items() if k not in RESERVED},
    }


def result_rows(
    storage: LocalStorage,
    input_file: File,
    result: File,
    offset: int,
    limit: int,
    status: str | None,
) -> tuple[int, list[dict[str, Any]]]:
    """A page of results with their input records; ``status`` filters on ok or error."""
    results = records_reader(storage, result)
    if status is None:
        total = results.record_count
        lines = [line for _, line in results.page(offset, limit)]
    else:
        matching = [line for _, line in results.iter_from(0) if line.get("_status") == status]
        total = len(matching)
        lines = matching[offset : offset + limit]
    inputs = dict(records_reader(storage, input_file).rows([int(x["_row"]) for x in lines]))
    return total, [_row_out(line, inputs.get(int(line["_row"]))) for line in lines]


def joined(
    storage: LocalStorage, input_file: File, result: File
) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Every result line with its input record, in row order (a merge of two sorted streams)."""
    inputs = records_reader(storage, input_file).iter_from(0)
    current = next(inputs, None)
    for _, line in records_reader(storage, result).iter_from(0):
        row = int(line["_row"])
        while current is not None and current[0] < row:
            current = next(inputs, None)
        record = current[1] if current is not None and current[0] == row else {}
        yield record, line


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def export_csv(storage: LocalStorage, input_file: File, result: File) -> Iterator[str]:
    """Input columns, then output columns, then the row's status and error."""
    in_columns = list(input_file.meta.get("columns") or [])
    out_columns = output_columns(result)
    clash = set(in_columns) & set(out_columns)
    out_names = [f"{c}_out" if c in clash else c for c in out_columns]
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def flush() -> str:
        text = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate()
        return text

    writer.writerow([*in_columns, *out_names, "status", "error"])
    yield flush()
    for record, line in joined(storage, input_file, result):
        writer.writerow(
            [
                *(_cell(record.get(c)) for c in in_columns),
                *(_cell(line.get(c)) for c in out_columns),
                line.get("_status") or "",
                line.get("_error") or "",
            ]
        )
        if buffer.tell() > 64 * 1024:
            yield flush()
    yield flush()


def export_jsonl(storage: LocalStorage, input_file: File, result: File) -> Iterator[bytes]:
    for record, line in joined(storage, input_file, result):
        row = _row_out(line, record)
        yield json.dumps(row, ensure_ascii=False).encode("utf-8") + b"\n"


async def failed_rows(storage: LocalStorage, result: File) -> list[int]:
    def scan() -> list[int]:
        return [
            int(line["_row"])
            for _, line in records_reader(storage, result).iter_from(0)
            if line.get("_status") != "ok"
        ]

    return await anyio.to_thread.run_sync(scan)


async def run_script(db: AsyncSession, run: Run) -> Script:
    script = await db.get(Script, run.script_id)
    if script is None:
        raise RunError("the run's script no longer exists")
    return script


async def unfinished_runs(db: AsyncSession) -> list[Run]:
    rows = await db.execute(
        select(Run).where(Run.status.in_([RunStatus.QUEUED.value, RunStatus.RUNNING.value]))
    )
    return list(rows.scalars())
