"""Runs of a workspace: start, follow, read, export, cancel, retry (docs/PLAN.md section 9)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

import anyio
from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from echolot_app.db import Author, File, Run, RunDecision, RunStatus, Script, utcnow
from echolot_app.deps import DbSession, State, get_workspace_or_404
from echolot_app.runs.service import (
    RowFilter,
    RunError,
    export_csv,
    export_jsonl,
    output_columns,
    refresh_run,
    result_row_numbers,
    result_rows,
    retry_failed_run,
    run_decisions,
    start_run,
)
from echolot_app.schemas import DecisionIn, DecisionOut, RunCreate, RunOut, RunRow, RunRowsPage

router = APIRouter(prefix="/api/workspaces/{workspace_id}/runs", tags=["runs"])

MAX_ROWS_PAGE = 200


async def run_out(db: AsyncSession, run: Run) -> RunOut:
    script = await db.get(Script, run.script_id)
    input_file = await db.get(File, run.input_file_id)
    rows = run.selection.get("rows")
    return RunOut(
        id=run.id,
        workspace_id=run.workspace_id,
        script_version=script.version if script else None,
        services=list(script.services) if script else [],
        input_file_id=run.input_file_id,
        input_file_name=input_file.name if input_file else None,
        parent_run_id=run.parent_run_id,
        record_limit=run.record_limit,
        selected_rows=len(rows) if isinstance(rows, list) else None,
        concurrency=run.concurrency,
        script_params=run.script_params,
        purpose=run.purpose,
        status=run.status,
        executor=run.executor,
        progress=run.progress,
        counts=run.counts,
        result_file_id=run.result_file_id,
        error=run.error,
        submitted_by=run.submitted_by,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


async def get_run_or_404(db: AsyncSession, workspace_id: uuid.UUID, run_id: uuid.UUID) -> Run:
    run = await db.get(Run, run_id)
    if run is None or run.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return run


@router.get("", response_model=list[RunOut])
async def list_runs(workspace_id: uuid.UUID, db: DbSession) -> list[RunOut]:
    await get_workspace_or_404(db, workspace_id)
    rows = (
        await db.execute(
            select(Run).where(Run.workspace_id == workspace_id).order_by(Run.created_at.desc())
        )
    ).scalars()
    return [await run_out(db, r) for r in rows]


@router.post("", response_model=RunOut, status_code=status.HTTP_201_CREATED)
async def create(workspace_id: uuid.UUID, body: RunCreate, db: DbSession, state: State) -> RunOut:
    """A run started by the user in the UI: no approval card (docs/PLAN.md section 9)."""
    await get_workspace_or_404(db, workspace_id)
    try:
        run = await start_run(
            db, state.storage, state.settings, state.executor, workspace_id, body, Author.USER
        )
    except RunError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return await run_out(db, run)


@router.get("/{run_id}", response_model=RunOut)
async def get_run(workspace_id: uuid.UUID, run_id: uuid.UUID, db: DbSession) -> RunOut:
    return await run_out(db, await get_run_or_404(db, workspace_id, run_id))


@router.get("/{run_id}/rows", response_model=RunRowsPage)
async def run_rows(
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    db: DbSession,
    state: State,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=MAX_ROWS_PAGE),
    row_status: Literal["ok", "error"] | None = Query(default=None, alias="status"),
    field: str | None = Query(default=None, max_length=200, description="an output field"),
    equals: str | None = Query(default=None, max_length=2000, description="its value"),
    decided: bool | None = None,
) -> RunRowsPage:
    """Results joined with their input records and decisions; empty until the results arrived.
    Filters: ``status``, ``field`` + ``equals`` (an output field's value), ``decided``."""
    run = await get_run_or_404(db, workspace_id, run_id)
    input_file = await db.get(File, run.input_file_id)
    result = await db.get(File, run.result_file_id) if run.result_file_id else None
    in_columns = list(input_file.meta.get("columns") or []) if input_file else []
    if input_file is None or result is None:
        return RunRowsPage(
            total=0,
            offset=offset,
            run_status=run.status,
            input_columns=in_columns,
            output_columns=[],
            rows=[],
        )
    where = RowFilter(status=row_status, field=field, equals=equals, decided=decided)
    decisions = await run_decisions(db, run.id)

    def page() -> tuple[int, list[dict[str, Any]]]:
        return result_rows(
            state.storage, input_file, result, offset, limit, where=where, decisions=decisions
        )

    total, rows = await anyio.to_thread.run_sync(page)
    return RunRowsPage(
        total=total,
        offset=offset,
        run_status=run.status,
        input_columns=in_columns,
        output_columns=output_columns(result),
        rows=[RunRow.model_validate(r) for r in rows],
    )


@router.get("/{run_id}/export")
async def export(
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    db: DbSession,
    state: State,
    format: Literal["csv", "jsonl"] = "csv",
) -> StreamingResponse:
    run = await get_run_or_404(db, workspace_id, run_id)
    input_file = await db.get(File, run.input_file_id)
    result = await db.get(File, run.result_file_id) if run.result_file_id else None
    if input_file is None or result is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The run has no results yet")
    stem = (input_file.name.rsplit(".", 1)[0] or "results") + f"-run-{str(run.id)[:8]}"
    decisions = await run_decisions(db, run.id)
    if format == "csv":
        body = export_csv(state.storage, input_file, result, decisions)
        media, name = "text/csv; charset=utf-8", f"{stem}.csv"
    else:
        body = export_jsonl(state.storage, input_file, result, decisions)
        media, name = "application/x-ndjson", f"{stem}.jsonl"
    return StreamingResponse(
        body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


@router.post("/{run_id}/cancel", response_model=RunOut)
async def cancel(workspace_id: uuid.UUID, run_id: uuid.UUID, db: DbSession, state: State) -> RunOut:
    run = await get_run_or_404(db, workspace_id, run_id)
    if not RunStatus(run.status).is_terminal and run.external_id is not None:
        await state.executor.cancel(run.external_id)
        await refresh_run(db, state.executor, run)
    return await run_out(db, run)


@router.post("/{run_id}/retry-failed", response_model=RunOut, status_code=status.HTTP_201_CREATED)
async def retry_failed(
    workspace_id: uuid.UUID, run_id: uuid.UUID, db: DbSession, state: State
) -> RunOut:
    run = await get_run_or_404(db, workspace_id, run_id)
    try:
        retry = await retry_failed_run(
            db, state.storage, state.settings, state.executor, run, Author.USER
        )
    except RunError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return await run_out(db, retry)


@router.put("/{run_id}/rows/{row}/decision", response_model=DecisionOut)
async def decide(
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    row: int,
    body: DecisionIn,
    db: DbSession,
    state: State,
) -> DecisionOut:
    """The reviewer's choice for one result row (docs/PLAN.md M3.3)."""
    run = await get_run_or_404(db, workspace_id, run_id)
    result = await db.get(File, run.result_file_id) if run.result_file_id else None
    if result is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The run has no results yet")
    rows = await anyio.to_thread.run_sync(result_row_numbers, state.storage, result)
    if row not in rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Row {row} is not in this run's results")
    value: dict[str, Any] = {"qid": body.qid}
    if body.note:
        value["note"] = body.note
    decision = await db.get(RunDecision, (run.id, row))
    if decision is None:
        decision = RunDecision(run_id=run.id, row=row, value=value)
        db.add(decision)
    else:
        decision.value = value
        decision.decided_at = utcnow()
    await db.flush()
    return DecisionOut(row=row, value=decision.value, decided_at=decision.decided_at)


@router.delete("/{run_id}/rows/{row}/decision", status_code=status.HTTP_204_NO_CONTENT)
async def undecide(workspace_id: uuid.UUID, run_id: uuid.UUID, row: int, db: DbSession) -> Response:
    run = await get_run_or_404(db, workspace_id, run_id)
    decision = await db.get(RunDecision, (run.id, row))
    if decision is not None:
        await db.delete(decision)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
