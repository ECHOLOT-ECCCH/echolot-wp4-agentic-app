"""FastAPI dependencies: state, settings, database session, storage, workspace lookup."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from echolot_app.db import Workspace
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


CurrentSettings = Annotated[Settings, Depends(get_settings_dep)]
# "function" scope commits before the response is sent: a client that creates something and
# reads it right away must find it (the default "request" scope commits after the response)
DbSession = Annotated[AsyncSession, Depends(get_db, scope="function")]
CurrentStorage = Annotated[LocalStorage, Depends(get_storage)]


async def get_workspace_or_404(db: AsyncSession, workspace_id: uuid.UUID) -> Workspace:
    workspace = await db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")
    return workspace
