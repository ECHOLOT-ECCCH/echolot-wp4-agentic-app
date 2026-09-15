"""Project-level authorization."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_core.db.models import Project, ProjectMember, ProjectRole, User, UserRole

ROLE_RANK = {ProjectRole.VIEWER.value: 1, ProjectRole.EDITOR.value: 2, ProjectRole.OWNER.value: 3}


async def membership_role(db: AsyncSession, project_id: uuid.UUID, user: User) -> str | None:
    row = (
        await db.execute(
            select(ProjectMember.role).where(
                ProjectMember.project_id == project_id, ProjectMember.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    return row


async def get_project_or_404(
    db: AsyncSession, project_id: uuid.UUID, user: User, min_role: str = ProjectRole.VIEWER.value
) -> tuple[Project, str]:
    """Return the project and the caller's effective role, or 404 (existence is not revealed)."""
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    role = await membership_role(db, project_id, user)
    if role is None and user.role == UserRole.ADMIN.value:
        role = ProjectRole.OWNER.value  # instance administrators may manage every project
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    if ROLE_RANK[role] < ROLE_RANK[min_role]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires project role {min_role}")
    return project, role
