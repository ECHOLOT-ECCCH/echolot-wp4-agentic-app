"""FastAPI dependencies: state, settings, database session, storage, the signed-in user and the
workspaces they own."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from echolot_app.auth import SESSION_COOKIE, user_for_token
from echolot_app.crypto import SecretBox
from echolot_app.db import User, Workspace
from echolot_app.settings import Settings
from echolot_app.state import AppState
from echolot_app.storage import LocalStorage


def get_state(request: Request) -> AppState:
    return request.app.state.app  # type: ignore[no-any-return]


State = Annotated[AppState, Depends(get_state)]


def get_settings_dep(state: State) -> Settings:
    return state.settings


async def get_db(state: State) -> AsyncIterator[AsyncSession]:
    async with state.db.session() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


def get_storage(state: State) -> LocalStorage:
    return state.storage


def get_secret_box(state: State) -> SecretBox:
    return state.secret_box


CurrentSettings = Annotated[Settings, Depends(get_settings_dep)]
# "function" scope commits before the response is sent: a client that creates something and
# reads it right away must find it (the default "request" scope commits after the response)
DbSession = Annotated[AsyncSession, Depends(get_db, scope="function")]
CurrentStorage = Annotated[LocalStorage, Depends(get_storage)]
CurrentSecretBox = Annotated[SecretBox, Depends(get_secret_box)]


async def get_current_user(request: Request, db: DbSession, settings: CurrentSettings) -> User:
    user = await user_for_token(db, request.cookies.get(SESSION_COOKIE), settings.session_days)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can do this")
    return user


AdminUser = Annotated[User, Depends(get_admin)]


async def get_workspace_or_404(db: AsyncSession, workspace_id: uuid.UUID, user: User) -> Workspace:
    """The workspace when the user owns it. Someone else's answers 404, as a missing one does."""
    workspace = await db.get(Workspace, workspace_id)
    if workspace is None or workspace.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")
    return workspace


async def require_workspace(workspace_id: uuid.UUID, db: DbSession, user: CurrentUser) -> Workspace:
    """Router dependency for everything under /api/workspaces/{workspace_id}/: every route is
    checked, including the ones that never load the workspace themselves."""
    return await get_workspace_or_404(db, workspace_id, user)


OwnedWorkspace = Annotated[Workspace, Depends(require_workspace)]
