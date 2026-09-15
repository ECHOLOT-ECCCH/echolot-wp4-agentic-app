"""FastAPI dependencies: settings, database session, current user, CSRF."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_api.auth.session import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, SessionStore
from elexifier_api.state import AppState
from elexifier_core.crypto import SecretBox
from elexifier_core.db.models import Session, User, UserRole
from elexifier_core.events import EventBus
from elexifier_core.settings import Settings
from elexifier_core.storage import Storage

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


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


def get_storage(state: State) -> Storage:
    return state.storage


def get_secret_box(state: State) -> SecretBox:
    return state.secret_box


def get_session_store(state: State) -> SessionStore:
    return state.session_store


def get_event_bus(state: State) -> EventBus:
    return state.event_bus


CurrentSettings = Annotated[Settings, Depends(get_settings_dep)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentStorage = Annotated[Storage, Depends(get_storage)]
CurrentSecretBox = Annotated[SecretBox, Depends(get_secret_box)]
CurrentSessionStore = Annotated[SessionStore, Depends(get_session_store)]
CurrentEventBus = Annotated[EventBus, Depends(get_event_bus)]


async def get_auth(
    request: Request, db: DbSession, store: CurrentSessionStore
) -> tuple[Session, User]:
    loaded = await store.load(db, request.cookies.get(SESSION_COOKIE))
    if loaded is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    session, user = loaded
    if request.method not in SAFE_METHODS:
        header = request.headers.get(CSRF_HEADER)
        cookie = request.cookies.get(CSRF_COOKIE)
        if not header or header != cookie or header != session.csrf_token:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="CSRF token missing or invalid")
    request.state.user = user
    request.state.session = session
    return session, user


Auth = Annotated[tuple[Session, User], Depends(get_auth)]


async def get_current_user(auth: Auth) -> User:
    return auth[1]


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_admin_user(user: CurrentUser) -> User:
    if user.role != UserRole.ADMIN.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Administrator role required")
    return user


AdminUser = Annotated[User, Depends(get_admin_user)]
