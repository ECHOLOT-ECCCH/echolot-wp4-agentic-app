"""Dependencies injected into every tool call."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from echolot_app.settings import Settings
from echolot_app.storage import LocalStorage


@dataclass
class WorkspaceContext:
    id: uuid.UUID
    name: str
    description: str | None = None


@dataclass
class AgentDeps:
    workspace: WorkspaceContext
    session_factory: async_sessionmaker[AsyncSession]
    storage: LocalStorage
    settings: Settings
    conversation_id: uuid.UUID | None = None

    def session(self) -> AsyncSession:
        """A fresh database session; tools commit their own work."""
        return self.session_factory()
