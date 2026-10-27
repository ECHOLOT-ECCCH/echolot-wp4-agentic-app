"""Per-process application state, created in the lifespan and read through dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from echolot_app.agent.runtime import AgentRuntime, PydanticAIRuntime
from echolot_app.db import Database
from echolot_app.runs.executor import Executor
from echolot_app.runs.local import LocalExecutor
from echolot_app.runs.poller import Poller
from echolot_app.runs.prefect import PrefectExecutor
from echolot_app.settings import Settings
from echolot_app.storage import LocalStorage


@dataclass
class AppState:
    settings: Settings
    db: Database
    storage: LocalStorage
    runtime: AgentRuntime
    executor: Executor
    poller: Poller


def build_executor(settings: Settings) -> Executor:
    if settings.executor == "prefect":
        auth = settings.prefect_auth_string
        return PrefectExecutor(
            settings.prefect_api_url,
            settings.prefect_deployment,
            auth.get_secret_value() if auth else None,
        )
    return LocalExecutor(
        settings.data_dir / "runner", settings.local_services_file, settings.local_runner_env_file
    )


def build_state(settings: Settings) -> AppState:
    db = Database(settings)
    executor = build_executor(settings)
    return AppState(
        settings=settings,
        db=db,
        storage=LocalStorage(settings.data_dir),
        runtime=PydanticAIRuntime(),
        executor=executor,
        poller=Poller(db, executor, settings.run_poll_interval_s),
    )
