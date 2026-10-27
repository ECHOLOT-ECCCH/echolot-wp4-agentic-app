"""Runs from the chat: submit (approval), follow, read results, retry failed rows (approval),
cancel (docs/PLAN.md 7.9). A sample run (at most 25 records) waits for its results inside the
tool call; a larger run returns at once and the user follows it in the middle pane."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Literal

import anyio
from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset

from echolot_app.agent.contracts import ToolResult, UiAction, error_result, to_tool_return
from echolot_app.agent.deps import AgentDeps
from echolot_app.agent.tools.files import clip_record
from echolot_app.db import Author, File, Run, RunStatus, Script
from echolot_app.runs.service import (
    RowFilter,
    RunError,
    refresh_run,
    result_rows,
    retry_failed_run,
    run_decisions,
    start_run,
)
from echolot_app.schemas import RunCreate

SAMPLE_MAX = 25
SAMPLE_WAIT_S = 120.0
WAIT_STEP_S = 0.5
MAX_ROWS_SHOWN = 20

runs_toolset: FunctionToolset[AgentDeps] = FunctionToolset()


async def _run(deps: AgentDeps, run_id: str) -> Run:
    try:
        rid = uuid.UUID(run_id.strip())
    except ValueError as exc:
        raise RunError(f"{run_id!r} is not a run id") from exc
    async with deps.session() as db:
        run = await db.get(Run, rid)
    if run is None or run.workspace_id != deps.workspace.id:
        raise RunError(f"no run {run_id} in this workspace")
    return run


async def _describe(deps: AgentDeps, run: Run) -> dict[str, Any]:
    async with deps.session() as db:
        script = await db.get(Script, run.script_id)
        source = await db.get(File, run.input_file_id)
    data: dict[str, Any] = {
        "run_id": str(run.id),
        "status": run.status,
        "script_version": script.version if script else None,
        "file": source.name if source else None,
        "progress": run.progress,
    }
    if run.counts:
        data["counts"] = run.counts
    if run.error:
        data["error"] = run.error
    if run.started_at and run.finished_at:
        data["duration_s"] = round((run.finished_at - run.started_at).total_seconds(), 1)
    return data


async def _wait(deps: AgentDeps, run_id: uuid.UUID, timeout_s: float) -> Run:
    deadline = time.monotonic() + timeout_s
    while True:
        async with deps.session() as db:
            run = await db.get(Run, run_id)
            assert run is not None
            if await refresh_run(db, deps.executor, run):
                await db.commit()
            if RunStatus(run.status).is_terminal or time.monotonic() >= deadline:
                return run
        await asyncio.sleep(WAIT_STEP_S)


async def _rows(
    deps: AgentDeps, run: Run, n: int, status: str | None, offset: int = 0
) -> tuple[int, list[dict[str, Any]]]:
    async with deps.session() as db:
        source = await db.get(File, run.input_file_id)
        result = await db.get(File, run.result_file_id) if run.result_file_id else None
        decisions = await run_decisions(db, run.id)
    if source is None or result is None:
        return 0, []

    def page() -> tuple[int, list[dict[str, Any]]]:
        return result_rows(
            deps.storage,
            source,
            result,
            offset,
            n,
            where=RowFilter(status=status),
            decisions=decisions,
        )

    total, rows = await anyio.to_thread.run_sync(page)
    shown = []
    for row in rows:
        row["input"], _ = clip_record(row["input"], 2000)
        row["output"], _ = clip_record(row["output"], 2000)
        shown.append(row)
    return total, shown


def _status_line(data: dict[str, Any]) -> str:
    counts = data.get("counts") or {}
    if data["status"] == "done":
        return (
            f"Run {data['run_id'][:8]} done: {counts.get('ok', 0)} ok, "
            f"{counts.get('error', 0)} error(s) of {counts.get('total', 0)}"
        )
    if data["status"] == "failed":
        return f"Run {data['run_id'][:8]} failed: {data.get('error') or 'no reason given'}"
    progress = data.get("progress") or {}
    return (
        f"Run {data['run_id'][:8]} {data['status']}: "
        f"{progress.get('done', 0)} of {progress.get('total', '?')} records"
    )


def _open(run: Run) -> list[UiAction]:
    return [UiAction(type="open_run", run_id=str(run.id))]


@runs_toolset.tool(requires_approval=True)
async def submit_run(
    ctx: RunContext[AgentDeps],
    file_id: str,
    purpose: str,
    limit: int | None = None,
    script_version: int | None = None,
    concurrency: int | None = None,
    script_params: dict[str, Any] | None = None,
) -> ToolReturn:
    """Run the script over a file's records on the runner. The user approves every run: say in
    `purpose` what the run is for. Start with a sample (limit 10 to 25): it waits for the
    results and shows the first ones. A run without a limit covers the whole file and returns at
    once; follow it with run_status.

    Args:
        file_id: The input file (from list_files).
        purpose: One sentence for the approval card, e.g. "Try the matching on 20 people".
        limit: Run only the first N records; all records when omitted.
        script_version: The script version to run; the latest when omitted.
        concurrency: Records processed at the same time (default 4; services cap it further).
        script_params: Values the script reads from ctx.params, e.g. {"threshold": 0.5}.
    """
    try:
        body = RunCreate(
            file_id=uuid.UUID(file_id.strip()),
            script_version=script_version,
            limit=limit,
            concurrency=concurrency,
            script_params=script_params or {},
            purpose=purpose,
        )
    except ValueError as exc:
        return error_result(f"bad run parameters: {exc}")
    deps = ctx.deps
    async with deps.session() as db:
        try:
            run = await start_run(
                db,
                deps.storage,
                deps.settings,
                deps.executor,
                deps.workspace.id,
                body,
                Author.AGENT,
            )
        except RunError as exc:
            return error_result(str(exc))
    if RunStatus(run.status).is_terminal:  # it could not be started
        data = await _describe(deps, run)
        return to_tool_return(ToolResult(summary=_status_line(data), data=data))
    if limit is None or limit > SAMPLE_MAX:
        data = await _describe(deps, run)
        return to_tool_return(
            ToolResult(
                summary=f"Run {str(run.id)[:8]} started on {data['progress'].get('total')} "
                "records; follow it with run_status and wait_s",
                data=data,
                ui_actions=_open(run),
            )
        )
    run = await _wait(deps, run.id, SAMPLE_WAIT_S)
    data = await _describe(deps, run)
    if run.status == RunStatus.DONE.value:
        _, rows = await _rows(deps, run, MAX_ROWS_SHOWN, None)
        data["rows"] = rows
    elif not RunStatus(run.status).is_terminal:
        data["note"] = f"still running after {SAMPLE_WAIT_S:.0f} s; follow it with run_status"
    return to_tool_return(ToolResult(summary=_status_line(data), data=data, ui_actions=_open(run)))


MAX_STATUS_WAIT_S = 60


@runs_toolset.tool
async def run_status(ctx: RunContext[AgentDeps], run_id: str, wait_s: int = 0) -> ToolReturn:
    """The status of a run: progress, counts when done, the reason when it failed, and the
    first failed records. To follow a run, call it once with wait_s rather than many times.

    Args:
        run_id: The run id from submit_run.
        wait_s: Wait up to this many seconds (at most 60) for the run to finish.
    """
    try:
        run = await _run(ctx.deps, run_id)
    except RunError as exc:
        return error_result(str(exc))
    run = await _wait(ctx.deps, run.id, max(0, min(wait_s, MAX_STATUS_WAIT_S)))
    data = await _describe(ctx.deps, run)
    if run.status == RunStatus.DONE.value and (run.counts or {}).get("error"):
        _, failed = await _rows(ctx.deps, run, 5, "error")
        data["first_errors"] = [{"row": r["row"], "error": r["error"]} for r in failed]
    return to_tool_return(ToolResult(summary=_status_line(data), data=data, ui_actions=_open(run)))


@runs_toolset.tool
async def run_results(
    ctx: RunContext[AgentDeps],
    run_id: str,
    n: int = 10,
    status: Literal["ok", "error"] | None = None,
    offset: int = 0,
) -> ToolReturn:
    """Results of a finished run: each record's input next to the script's output (or error),
    and the reviewer's decision when there is one (a chosen QID, or null for "none of these").
    Long values are shortened.

    Args:
        run_id: The run id.
        n: How many results, at most 20.
        status: Only "ok" or only "error" results.
        offset: Skip this many (matching) results.
    """
    try:
        run = await _run(ctx.deps, run_id)
    except RunError as exc:
        return error_result(str(exc))
    if run.result_file_id is None:
        return error_result(f"run {run_id} has no results ({run.status})")
    total, rows = await _rows(ctx.deps, run, max(1, min(n, MAX_ROWS_SHOWN)), status, max(offset, 0))
    which = f"{status} " if status else ""
    summary = f"{len(rows)} of {total} {which}result(s) of run {str(run.id)[:8]}"
    first = rows[0]["row"] if rows else None
    return to_tool_return(
        ToolResult(
            summary=summary,
            data={"run_id": str(run.id), "total": total, "rows": rows},
            ui_actions=[UiAction(type="open_run", run_id=str(run.id), row=first)],
        )
    )


@runs_toolset.tool(requires_approval=True)
async def retry_failed(ctx: RunContext[AgentDeps], run_id: str) -> ToolReturn:
    """Run the latest script again over the records that failed in a finished run (for example
    after the service was down, or after fixing the script). Needs the user's approval.

    Args:
        run_id: The finished run whose failed records to retry.
    """
    deps = ctx.deps
    try:
        run = await _run(deps, run_id)
        async with deps.session() as db:
            parent = await db.get(Run, run.id)
            assert parent is not None
            retry = await retry_failed_run(
                db, deps.storage, deps.settings, deps.executor, parent, Author.AGENT
            )
    except RunError as exc:
        return error_result(str(exc))
    data = await _describe(deps, retry)
    summary = f"Run {str(retry.id)[:8]} retries {data['progress'].get('total')} failed record(s)"
    return to_tool_return(ToolResult(summary=summary, data=data, ui_actions=_open(retry)))


@runs_toolset.tool
async def cancel_run(ctx: RunContext[AgentDeps], run_id: str) -> ToolReturn:
    """Stop a run that is still going. Results of a cancelled run are not kept.

    Args:
        run_id: The run id.
    """
    try:
        run = await _run(ctx.deps, run_id)
    except RunError as exc:
        return error_result(str(exc))
    if RunStatus(run.status).is_terminal or run.external_id is None:
        return error_result(f"run {run_id} is already {run.status}")
    await ctx.deps.executor.cancel(run.external_id)
    run = await _wait(ctx.deps, run.id, 10)
    data = await _describe(ctx.deps, run)
    return to_tool_return(ToolResult(summary=_status_line(data), data=data, ui_actions=_open(run)))
