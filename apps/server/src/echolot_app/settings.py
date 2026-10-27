"""Application settings from ``ECHO_*`` environment variables and an optional .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_env_file() -> Path | None:
    """Walk up from the current directory to find a .env file (repo root in development)."""
    for directory in [Path.cwd(), *Path.cwd().parents]:
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ECHO_",
        env_file=_find_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # SQLite database (app.db) and stored files live here
    data_dir: Path = Path("./data")

    # The one model the agent uses (docs/PLAN.md section 3); provider "test" needs
    # enable_test_model and no key
    model_provider: str = "anthropic"
    model_id: str = "claude-sonnet-5-5"
    model_api_key: SecretStr | None = None
    model_base_url: str | None = None
    enable_test_model: bool = False

    # Where runs execute (docs/PLAN.md 7.6) and the URL the runner uses to reach this app
    executor: Literal["local", "prefect"] = "local"
    public_url: str = "http://127.0.0.1:8000"
    prefect_api_url: str = "http://127.0.0.1:4200/api"
    prefect_auth_string: SecretStr | None = None
    prefect_deployment: str = "run-script/default"
    # the agent-facing service docs (docs/PLAN.md 7.7)
    services_dir: Path = Path("./services")
    # LocalExecutor: the services the runner subprocess may call (keys from this environment)
    local_services_file: Path = Path("./services.toml")
    run_poll_interval_s: float = 3.0

    log_level: str = "INFO"
    log_json: bool = False

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{(self.data_dir / 'app.db').resolve()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
