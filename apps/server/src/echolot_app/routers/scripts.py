"""Workspace scripts: the versions of the per-record function (docs/PLAN.md 7.1)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from echolot_app.db import Author, Script
from echolot_app.deps import DbSession, require_workspace
from echolot_app.schemas import ScriptIn, ScriptOut, ScriptSummary
from echolot_app.scripts import ScriptError, add_script_version, get_script, latest_script

# every route checks that the signed-in user owns the workspace (docs/PLAN.md 7.11)
router = APIRouter(
    prefix="/api/workspaces/{workspace_id}/scripts",
    tags=["scripts"],
    dependencies=[Depends(require_workspace)],
)


@router.get("", response_model=list[ScriptSummary])
async def list_scripts(workspace_id: uuid.UUID, db: DbSession) -> list[ScriptSummary]:
    rows = (
        await db.execute(
            select(Script)
            .where(Script.workspace_id == workspace_id)
            .order_by(Script.version.desc())
        )
    ).scalars()
    return [ScriptSummary.model_validate(s) for s in rows]


@router.post("", response_model=ScriptOut, status_code=status.HTTP_201_CREATED)
async def create_script(workspace_id: uuid.UUID, body: ScriptIn, db: DbSession) -> ScriptOut:
    """A version written by the user in the editor."""
    try:
        script = await add_script_version(db, workspace_id, body.code, body.note, Author.USER)
    except ScriptError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return ScriptOut.model_validate(script)


@router.get("/{version}", response_model=ScriptOut)
async def read_script(workspace_id: uuid.UUID, version: str, db: DbSession) -> ScriptOut:
    """One version, or ``latest``."""
    if version == "latest":
        script = await latest_script(db, workspace_id)
    elif version.isdigit():
        script = await get_script(db, workspace_id, int(version))
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "version must be a number")
    if script is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Script not found")
    return ScriptOut.model_validate(script)
