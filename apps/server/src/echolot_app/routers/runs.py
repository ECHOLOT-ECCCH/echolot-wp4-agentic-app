"""Runs of a workspace: start, follow, read, export, cancel, retry (docs/PLAN.md section 9)."""

from __future__ import annotations

import uuid
from typing import Literal

import anyio
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from echolot_app.db import Author, File, Run, RunStatus, Script
from echolot_app.deps import DbSession, State, get_workspace_or_404
from echolot_app.routers.files import get_file_or_404
from echolot_app.runs.service import (
    RunError,
    RunRequest,
    create_run,
    export_csv,
    export_jsonl,
    failed_rows,
    output_columns,
    refresh_run,
    result_rows,
)
from echolot_app.schemas import RunCreate, RunOut, RunRow, RunRowsPage
from echolot_app.scripts import get_script, latest_script
from echolot_app.state import AppState

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


async def start_run(
    db: AsyncSession, state: AppState, workspace_id: uuid.UUID, body: RunCreate, author: str
) -> Run:
    """Shared by the route and the agent's submit_run."""
    input_file = await get_file_or_404(db, workspace_id, body.file_id)
    if body.script_version is None:
        script = await latest_script(db, workspace_id)
    else:
        script = await get_script(db, workspace_id, body.script_version)
    if script is None:
        raise RunError(
            "there is no script to run yet"
            if body.script_version is None
            else f"there is no version {body.script_version} of the script"
        )
    return await create_run(
        db,
        state.storage,
        state.settings,
        state.executor,
        RunRequest(
            input_file=input_file,
            script=script,
            record_limit=body.limit,
            concurrency=body.concurrency,
            script_params=body.script_params,
            purpose=body.purpose,
            submitted_by=author,
        ),
    )


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
        run = await start_run(db, state, workspace_id, body, Author.USER)
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
) -> RunRowsPage:
    """Results joined with their input records; empty until the results arrived."""
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
    total, rows = await anyio.to_thread.run_sync(
        result_rows, state.storage, input_file, result, offset, limit, row_status
    )
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
    if format == "csv":
        body = export_csv(state.storage, input_file, result)
        media, name = "text/csv; charset=utf-8", f"{stem}.csv"
    else:
        body = export_jsonl(state.storage, input_file, result)
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


async def retry_failed_run(db: AsyncSession, state: AppState, run: Run, author: str) -> Run:
    """A new run over the rows that failed in ``run``, with the latest script."""
    if not RunStatus(run.status).is_terminal:
        raise RunError("the run is not finished yet")
    result = await db.get(File, run.result_file_id) if run.result_file_id else None
    if result is None:
        raise RunError("the run has no results to retry")
    rows = await failed_rows(state.storage, result)
    if not rows:
        raise RunError("no record failed in this run")
    input_file = await db.get(File, run.input_file_id)
    script = await latest_script(db, run.workspace_id)
    if input_file is None or script is None:
        raise RunError("the run's input file or script no longer exists")
    return await create_run(
        db,
        state.storage,
        state.settings,
        state.executor,
        RunRequest(
            input_file=input_file,
            script=script,
            concurrency=run.concurrency,
            script_params=run.script_params,
            purpose=f"retry the {len(rows)} failed record(s) of run {str(run.id)[:8]}",
            submitted_by=author,
            selection_rows=rows,
            parent_run_id=run.id,
        ),
    )


@router.post("/{run_id}/retry-failed", response_model=RunOut, status_code=status.HTTP_201_CREATED)
async def retry_failed(
    workspace_id: uuid.UUID, run_id: uuid.UUID, db: DbSession, state: State
) -> RunOut:
    run = await get_run_or_404(db, workspace_id, run_id)
    try:
        retry = await retry_failed_run(db, state, run, Author.USER)
    except RunError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return await run_out(db, retry)
