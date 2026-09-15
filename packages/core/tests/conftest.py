"""Shared fixtures for core tests.

Database-backed tests need a reachable PostgreSQL at TEST_DATABASE_URL (``make dev-infra``).
They are skipped when it is not reachable unless ELX_REQUIRE_DB=1 is set (CI).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from elexifier_core.settings import Settings, reset_settings_cache

CORE_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = CORE_DIR / "alembic.ini"

pytest_plugins: list[str] = []


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ELX_SECRET_KEY", "b2r2m2eZ3xZlYQ0T0m4Y5gk0YQ1r5x6zBxJ3v4eKk9k=")
    monkeypatch.setenv("ELX_SESSION_SECRET", "test-session-secret-0123456789")
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()


@pytest.fixture
def settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def _dsn_reachable(dsn: str) -> bool:
    async def probe() -> None:
        conn = await asyncpg.connect(dsn, timeout=3)
        await conn.close()

    try:
        asyncio.run(probe())
    except Exception:
        return False
    return True


@pytest.fixture(scope="session")
def test_db_url() -> str:
    # Settings honours TEST_DATABASE_URL from the environment or the repository .env file.
    url = Settings(  # type: ignore[call-arg]
        elx_secret_key="unused", elx_session_secret="unused-unused-unused"
    ).test_database_url
    dsn = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    if not _dsn_reachable(dsn):
        message = f"test database not reachable at {dsn} (run `make dev-infra`)"
        if os.environ.get("ELX_REQUIRE_DB"):
            pytest.fail(message)
        pytest.skip(message)
    return url


@pytest.fixture(scope="session")
def alembic_config(test_db_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", test_db_url)
    return cfg


@pytest.fixture(scope="session")
def migrated_db(alembic_config: Config, test_db_url: str) -> Iterator[str]:
    """A test database at the latest migration; torn down to base at session end."""
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    yield test_db_url
    command.downgrade(alembic_config, "base")
