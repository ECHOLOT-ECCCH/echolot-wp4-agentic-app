"""Projects and project members."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from elexifier_api import audit
from elexifier_api.authz import get_project_or_404
from elexifier_api.deps import CurrentStorage, CurrentUser, DbSession
from elexifier_api.schemas import MemberAdd, MemberOut, ProjectCreate, ProjectOut, ProjectUpdate
from elexifier_core.db.models import File, Project, ProjectMember, ProjectRole, User

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _out(project: Project, role: str) -> ProjectOut:
    fields = {name: getattr(project, name) for name in ProjectOut.model_fields if name != "my_role"}
    return ProjectOut(**fields, my_role=role)


@router.get("", response_model=list[ProjectOut])
async def list_projects(db: DbSession, user: CurrentUser) -> list[ProjectOut]:
    rows = (
        await db.execute(
            select(Project, ProjectMember.role)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(ProjectMember.user_id == user.id)
            .order_by(Project.updated_at.desc())
        )
    ).all()
    return [_out(project, role) for project, role in rows]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreate, db: DbSession, user: CurrentUser) -> ProjectOut:
    project = Project(
        owner_id=user.id,
        name=body.name.strip(),
        description=body.description,
        source_type=body.source_type,
    )
    db.add(project)
    await db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role=ProjectRole.OWNER.value))
    audit.record(db, user.id, "project.created", project.id, name=project.name)
    await db.flush()
    await db.refresh(project)
    return _out(project, ProjectRole.OWNER.value)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: uuid.UUID, db: DbSession, user: CurrentUser) -> ProjectOut:
    project, role = await get_project_or_404(db, project_id, user)
    return _out(project, role)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID, body: ProjectUpdate, db: DbSession, user: CurrentUser
) -> ProjectOut:
    project, role = await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(project, field, value.strip() if field == "name" and value else value)
    audit.record(db, user.id, "project.updated", project.id, fields=sorted(changes))
    await db.flush()
    await db.refresh(project)
    return _out(project, role)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID, db: DbSession, user: CurrentUser, storage: CurrentStorage
) -> Response:
    project, _ = await get_project_or_404(db, project_id, user, ProjectRole.OWNER.value)
    keys = (await db.execute(select(File.s3_key).where(File.project_id == project.id))).scalars()
    for key in keys:
        await storage.delete(key)
    audit.record(db, user.id, "project.deleted", project.id, name=project.name)
    await db.delete(project)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- members -----------------------------------------------------------------------------


@router.get("/{project_id}/members", response_model=list[MemberOut])
async def list_members(project_id: uuid.UUID, db: DbSession, user: CurrentUser) -> list[MemberOut]:
    await get_project_or_404(db, project_id, user)
    rows = (
        await db.execute(
            select(ProjectMember, User)
            .join(User, User.id == ProjectMember.user_id)
            .where(ProjectMember.project_id == project_id)
            .order_by(ProjectMember.created_at)
        )
    ).all()
    return [MemberOut(user_id=u.id, email=u.email, name=u.name, role=m.role) for m, u in rows]


@router.post("/{project_id}/members", response_model=MemberOut, status_code=201)
async def add_member(
    project_id: uuid.UUID, body: MemberAdd, db: DbSession, user: CurrentUser
) -> MemberOut:
    await get_project_or_404(db, project_id, user, ProjectRole.OWNER.value)
    target = (
        await db.execute(select(User).where(User.email == body.email.strip().lower()))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No user with that email has signed in yet")
    existing = await db.get(ProjectMember, (project_id, target.id))
    if existing is not None:
        if existing.role == ProjectRole.OWNER.value:
            raise HTTPException(status.HTTP_409_CONFLICT, "The owner's role cannot be changed")
        existing.role = body.role
    else:
        db.add(ProjectMember(project_id=project_id, user_id=target.id, role=body.role))
    audit.record(db, user.id, "member.added", project_id, member=str(target.id), role=body.role)
    await db.flush()
    return MemberOut(user_id=target.id, email=target.email, name=target.name, role=body.role)


@router.delete("/{project_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    project_id: uuid.UUID, member_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> Response:
    await get_project_or_404(db, project_id, user, ProjectRole.OWNER.value)
    member = await db.get(ProjectMember, (project_id, member_id))
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    if member.role == ProjectRole.OWNER.value:
        raise HTTPException(status.HTTP_409_CONFLICT, "The owner cannot be removed")
    await db.delete(member)
    audit.record(db, user.id, "member.removed", project_id, member=str(member_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
