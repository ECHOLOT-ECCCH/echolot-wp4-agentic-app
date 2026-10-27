"""Per-process application state, created in the lifespan and read through dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from echolot_app.agent.runtime import AgentRuntime, PydanticAIRuntime
from echolot_app.db import Database
from echolot_app.settings import Settings
from echolot_app.storage import LocalStorage


@dataclass
class AppState:
    settings: Settings
    db: Database
    storage: LocalStorage
    runtime: AgentRuntime


def build_state(settings: Settings) -> AppState:
    return AppState(
        settings=settings,
        db=Database(settings),
        storage=LocalStorage(settings.data_dir),
        runtime=PydanticAIRuntime(),
    )
