"""Async engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from elexifier_core.settings import Settings, get_settings


def create_engine(settings: Settings | None = None, **kwargs: object) -> AsyncEngine:
    settings = settings or get_settings()
    return create_async_engine(settings.database_url, pool_pre_ping=True, **kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


class Database:
    """Holds the engine and session factory for one process (API, worker, tests)."""

    def __init__(self, settings: Settings | None = None, url: str | None = None) -> None:
        settings = settings or get_settings()
        self.engine: AsyncEngine = create_async_engine(
            url or settings.database_url, pool_pre_ping=True
        )
        self.session_factory = create_session_factory(self.engine)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()
