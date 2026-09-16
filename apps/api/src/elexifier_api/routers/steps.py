"""Custom steps of the conversion pipeline (docs/PLAN.md M3.4): saved sandbox snippets that
run on the entry stream of every sample and full conversion, in order."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_api import audit
from elexifier_api.authz import get_project_or_404
from elexifier_api.deps import CurrentUser, DbSession
from elexifier_api.schemas import StepIn, StepOut, StepReorder, StepUpdate
from elexifier_core.db.models import File, FileKind, PipelineStep, ProjectRole
from elexifier_core.events import publish

router = APIRouter(prefix="/api/projects/{project_id}", tags=["steps"])


async def project_steps(db: AsyncSession, project_id: uuid.UUID) -> list[PipelineStep]:
    return list(
        (
            await db.execute(
                select(PipelineStep)
                .where(PipelineStep.project_id == project_id)
                .order_by(PipelineStep.position, PipelineStep.created_at)
            )
        )
        .scalars()
        .all()
    )


async def next_position(db: AsyncSession, project_id: uuid.UUID) -> int:
    highest = (
        await db.execute(
            select(func.max(PipelineStep.position)).where(PipelineStep.project_id == project_id)
        )
    ).scalar_one()
    return 0 if highest is None else int(highest) + 1


async def _step_or_404(db: AsyncSession, project_id: uuid.UUID, step_id: uuid.UUID) -> PipelineStep:
    step = await db.get(PipelineStep, step_id)
    if step is None or step.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Step not found")
    return step


@router.get("/steps", response_model=list[StepOut])
async def list_steps(project_id: uuid.UUID, db: DbSession, user: CurrentUser) -> list[StepOut]:
    await get_project_or_404(db, project_id, user)
    return [StepOut.model_validate(s) for s in await project_steps(db, project_id)]


@router.post("/steps", response_model=StepOut, status_code=status.HTTP_201_CREATED)
async def create_step(
    project_id: uuid.UUID, body: StepIn, db: DbSession, user: CurrentUser
) -> StepOut:
    """Register a code artifact (a snippet kept by run_code) as a step."""
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    artifact = await db.get(File, body.code_artifact_id)
    if (
        artifact is None
        or artifact.project_id != project_id
        or artifact.kind != FileKind.ARTIFACT.value
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No such code artifact")
    if not (artifact.mime == "text/x-python" or artifact.name.lower().endswith(".py")):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The artifact is not Python")
    step = PipelineStep(
        project_id=project_id,
        position=await next_position(db, project_id),
        name=body.name.strip(),
        config={"stage": "entries", **body.config, "code_sha256": artifact.sha256},
        code_artifact_id=artifact.id,
        enabled=body.enabled,
        created_by=user.id,
    )
    db.add(step)
    await db.flush()
    audit.record(db, user.id, "step.created", step.id, project=str(project_id), name=step.name)
    await publish(db, project_id, "steps.changed", {"step_id": str(step.id), "action": "created"})
    await db.refresh(step)  # server-side timestamps; the request commits on exit
    return StepOut.model_validate(step)


@router.patch("/steps/{step_id}", response_model=StepOut)
async def update_step(
    project_id: uuid.UUID,
    step_id: uuid.UUID,
    body: StepUpdate,
    db: DbSession,
    user: CurrentUser,
) -> StepOut:
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    step = await _step_or_404(db, project_id, step_id)
    if body.name is not None:
        step.name = body.name.strip()
    if body.enabled is not None:
        step.enabled = body.enabled
    if body.config is not None:
        step.config = {**step.config, **body.config}
    await publish(db, project_id, "steps.changed", {"step_id": str(step.id), "action": "updated"})
    await db.flush()
    await db.refresh(step)
    return StepOut.model_validate(step)


@router.delete("/steps/{step_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_step(
    project_id: uuid.UUID, step_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> Response:
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    step = await _step_or_404(db, project_id, step_id)
    audit.record(db, user.id, "step.deleted", step.id, project=str(project_id), name=step.name)
    await db.delete(step)
    await publish(db, project_id, "steps.changed", {"step_id": str(step_id), "action": "deleted"})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/steps/reorder", response_model=list[StepOut])
async def reorder_steps(
    project_id: uuid.UUID, body: StepReorder, db: DbSession, user: CurrentUser
) -> list[StepOut]:
    """``ids`` lists every step of the project in its new order."""
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    steps = {s.id: s for s in await project_steps(db, project_id)}
    if sorted(body.ids) != sorted(steps) or len(body.ids) != len(set(body.ids)):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "ids must list every step of the project once"
        )
    for position, step_id in enumerate(body.ids):
        steps[step_id].position = position
    await publish(db, project_id, "steps.changed", {"action": "reordered"})
    await db.flush()
    for step in steps.values():
        await db.refresh(step)
    return [StepOut.model_validate(s) for s in await project_steps(db, project_id)]
