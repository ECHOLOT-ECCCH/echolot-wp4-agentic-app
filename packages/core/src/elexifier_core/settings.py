"""Application settings loaded from environment variables and an optional .env file."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
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
        env_file=_find_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://elexifier:elexifier@localhost:5432/elexifier",
        description="SQLAlchemy async URL (postgresql+asyncpg://...)",
    )
    test_database_url: str = (
        "postgresql+asyncpg://elexifier:elexifier@localhost:5432/elexifier_test"
    )

    # Secrets
    elx_secret_key: str = Field(
        description="Fernet key(s) for provider credentials; comma-separated for rotation"
    )
    elx_session_secret: str = Field(min_length=16)

    # URLs
    public_url: str = "http://localhost:5173"

    # OIDC
    oidc_issuer: str = "http://localhost:8080/realms/elexifier"
    oidc_internal_issuer: str | None = Field(
        default=None,
        description="Issuer URL reachable from the API container (e.g. http://keycloak:8080/realms/x)"
        " when the public issuer is not routable server-side; tokens still carry oidc_issuer",
    )
    oidc_client_id: str = "elexifier-web"
    oidc_client_secret: str = "dev-client-secret"

    # Storage
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_root: Path = Path("./data/storage")
    s3_endpoint: str | None = None
    s3_bucket: str = "elexifier"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "garage"

    # Background jobs: modules that register Procrastinate tasks
    task_modules: str = "elexifier_core.tasks,elexifier_api.jobs"

    # Limits
    max_upload_mb: int = 2048
    max_preview_bytes: int = (
        5 * 1024 * 1024
    )  # whole-file previews above this are refused (PLAN 7.13)

    # Sandbox runner
    sandbox_runner_url: str = "http://localhost:8010"
    sandbox_runner_token: str = "dev-runner-token"
    sandbox_runtime: Literal["runc", "runsc"] = "runc"
    sandbox_image: str = "elexifier-sandbox:local"  # built by `make sandbox-image`

    # Development conveniences
    elx_enable_test_model: bool = False
    log_level: str = "INFO"
    environment: Literal["development", "test", "production"] = "development"

    @field_validator("database_url", "test_database_url")
    @classmethod
    def _must_be_async_postgres(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("database URLs must use the postgresql+asyncpg:// scheme")
        return value

    @property
    def secret_keys(self) -> list[str]:
        return [k.strip() for k in self.elx_secret_key.split(",") if k.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def cookie_secure(self) -> bool:
        return self.public_url.startswith("https://")

    @property
    def database_dsn(self) -> str:
        """Plain libpq DSN (for asyncpg LISTEN connections and psycopg / procrastinate)."""
        return _plain_dsn(self.database_url)

    @property
    def test_database_dsn(self) -> str:
        return _plain_dsn(self.test_database_url)


def _plain_dsn(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")  # keep logs clean
    return Settings()  # type: ignore[call-arg]  # values come from the environment


def reset_settings_cache() -> None:
    """For tests that change environment variables."""
    get_settings.cache_clear()
