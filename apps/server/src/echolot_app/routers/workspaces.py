"""Workspaces and their task notes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status
from sqlalchemy import select

from echolot_app.db import Workspace, utcnow
from echolot_app.deps import CurrentStorage, DbSession, get_workspace_or_404
from echolot_app.schemas import TaskIn, TaskOut, WorkspaceCreate, WorkspaceOut, WorkspaceUpdate

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


@router.get("", response_model=list[WorkspaceOut])
async def list_workspaces(db: DbSession) -> list[WorkspaceOut]:
    rows = (await db.execute(select(Workspace).order_by(Workspace.updated_at.desc()))).scalars()
    return [WorkspaceOut.model_validate(w) for w in rows]


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
async def create_workspace(body: WorkspaceCreate, db: DbSession) -> WorkspaceOut:
    workspace = Workspace(name=body.name.strip(), description=body.description)
    db.add(workspace)
    await db.flush()
    return WorkspaceOut.model_validate(workspace)


@router.get("/{workspace_id}", response_model=WorkspaceOut)
async def get_workspace(workspace_id: uuid.UUID, db: DbSession) -> WorkspaceOut:
    return WorkspaceOut.model_validate(await get_workspace_or_404(db, workspace_id))


@router.patch("/{workspace_id}", response_model=WorkspaceOut)
async def update_workspace(
    workspace_id: uuid.UUID, body: WorkspaceUpdate, db: DbSession
) -> WorkspaceOut:
    workspace = await get_workspace_or_404(db, workspace_id)
    if body.name is not None:
        workspace.name = body.name.strip()
    if "description" in body.model_fields_set:
        workspace.description = body.description
    await db.flush()
    await db.refresh(workspace)
    return WorkspaceOut.model_validate(workspace)


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: uuid.UUID, db: DbSession, storage: CurrentStorage
) -> Response:
    workspace = await get_workspace_or_404(db, workspace_id)
    await db.delete(workspace)
    await db.flush()
    await storage.delete_prefix(f"workspaces/{workspace_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{workspace_id}/task", response_model=TaskOut)
async def get_task(workspace_id: uuid.UUID, db: DbSession) -> TaskOut:
    workspace = await get_workspace_or_404(db, workspace_id)
    return TaskOut(markdown=workspace.task_md, updated_at=workspace.updated_at)


@router.put("/{workspace_id}/task", response_model=TaskOut)
async def put_task(workspace_id: uuid.UUID, body: TaskIn, db: DbSession) -> TaskOut:
    workspace = await get_workspace_or_404(db, workspace_id)
    workspace.task_md = body.markdown
    workspace.updated_at = utcnow()
    await db.flush()
    return TaskOut(markdown=workspace.task_md, updated_at=workspace.updated_at)
