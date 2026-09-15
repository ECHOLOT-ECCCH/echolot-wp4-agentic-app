"""Shared fixtures for core tests.

Database-backed tests need a reachable PostgreSQL at TEST_DATABASE_URL (``make dev-infra``).
They are skipped when it is not reachable unless ELX_REQUIRE_DB=1 is set (CI).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from elexifier_core.settings import Settings, reset_settings_cache
from elexifier_core.testing import (
    TEST_SECRET_KEY,
    TEST_SESSION_SECRET,
    test_database_url_or_skip,
)

CORE_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = CORE_DIR / "alembic.ini"

pytest_plugins: list[str] = []


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ELX_SECRET_KEY", TEST_SECRET_KEY)
    monkeypatch.setenv("ELX_SESSION_SECRET", TEST_SESSION_SECRET)
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()


@pytest.fixture
def settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


@pytest.fixture(scope="session")
def test_db_url() -> str:
    return test_database_url_or_skip()


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
