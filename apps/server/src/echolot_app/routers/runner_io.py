"""The runner's side of a run (docs/PLAN.md 7.3): input, progress, result, with the run's token.
Any problem with the run id or token answers 404, so the endpoints say nothing to a guesser."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from echolot_app.db import File, Run, RunStatus
from echolot_app.deps import DbSession, State
from echolot_app.runs.service import input_lines, records_reader, store_result
from echolot_app.runs.tokens import token_matches

router = APIRouter(prefix="/runner/runs/{run_id}", tags=["runner"])


async def authorized_run(db: AsyncSession, run_id: uuid.UUID, authorization: str | None) -> Run:
    run = await db.get(Run, run_id)
    token = authorization.removeprefix("Bearer ").strip() if authorization else None
    if run is None or RunStatus(run.status).is_terminal or not token_matches(token, run.token_hash):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return run


Authorization = Annotated[str | None, Header()]


@router.get("/input")
async def runner_input(
    run_id: uuid.UUID, db: DbSession, state: State, authorization: Authorization = None
) -> StreamingResponse:
    run = await authorized_run(db, run_id, authorization)
    input_file = await db.get(File, run.input_file_id)
    if input_file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    reader = records_reader(state.storage, input_file)
    return StreamingResponse(input_lines(reader, run), media_type="application/x-ndjson")


@router.post("/progress", status_code=status.HTTP_204_NO_CONTENT)
async def runner_progress(
    run_id: uuid.UUID, body: dict[str, Any], db: DbSession, authorization: Authorization = None
) -> Response:
    run = await authorized_run(db, run_id, authorization)
    progress = {k: int(v) for k, v in body.items() if k in ("done", "total", "ok", "error")}
    run.progress = {**run.progress, **progress}
    if run.status == RunStatus.QUEUED.value:
        run.status = RunStatus.RUNNING.value  # the executor may report it a moment later
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/result", status_code=status.HTTP_204_NO_CONTENT)
async def runner_result(
    run_id: uuid.UUID,
    request: Request,
    db: DbSession,
    state: State,
    authorization: Authorization = None,
) -> Response:
    run = await authorized_run(db, run_id, authorization)
    await store_result(db, state.storage, run, request.stream())
    return Response(status_code=status.HTTP_204_NO_CONTENT)
