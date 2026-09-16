"""Dependencies injected into every tool call."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from elexifier_agent.models import ModelInfo
from elexifier_core.settings import Settings
from elexifier_core.storage import Storage


@dataclass
class ProjectContext:
    id: uuid.UUID
    name: str
    source_type: str  # "xml" | "pdf"
    description: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class UserContext:
    id: uuid.UUID
    email: str
    name: str
    role: str = "user"  # project role: owner | editor | viewer


@dataclass
class AgentDeps:
    project: ProjectContext
    user: UserContext
    session_factory: async_sessionmaker[AsyncSession]
    storage: Storage
    settings: Settings
    model_info: ModelInfo | None = None
    conversation_id: uuid.UUID | None = None
    # Hands a background job to the queue: (task name, kwargs) -> job id. None when no queue.
    defer_job: Callable[[str, dict[str, Any]], Awaitable[int | None]] | None = None

    def session(self) -> AsyncSession:
        """A fresh database session; tools commit their own work."""
        return self.session_factory()
