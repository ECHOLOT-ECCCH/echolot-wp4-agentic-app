"""Process-wide resources for job functions (one engine and storage per worker process)."""

from __future__ import annotations

from functools import lru_cache

from elexifier_agent.sandbox import RunnerClient
from elexifier_core.db.session import Database
from elexifier_core.settings import Settings, get_settings
from elexifier_core.storage import Storage, build_storage


@lru_cache(maxsize=1)
def job_database() -> Database:
    return Database(get_settings())


@lru_cache(maxsize=1)
def job_storage() -> Storage:
    return build_storage(get_settings())


def job_settings() -> Settings:
    return get_settings()


def job_runner() -> RunnerClient:
    """The sandbox runner client for jobs that replay custom steps."""
    settings = get_settings()
    return RunnerClient(settings.sandbox_runner_url, settings.sandbox_runner_token)


def reset_job_runtime() -> None:
    """Tests swap settings; drop the cached engine and storage."""
    job_database.cache_clear()
    job_storage.cache_clear()
