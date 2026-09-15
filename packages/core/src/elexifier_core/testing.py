"""Helpers shared by the test suites of all packages (not used at runtime)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from elexifier_core.db.base import Base
from elexifier_core.settings import Settings

CORE_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI = CORE_DIR / "alembic.ini"

TEST_SECRET_KEY = "b2r2m2eZ3xZlYQ0T0m4Y5gk0YQ1r5x6zBxJ3v4eKk9k="
TEST_SESSION_SECRET = "test-session-secret-0123456789"


def to_dsn(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def dsn_reachable(dsn: str) -> bool:
    async def probe() -> None:
        conn = await asyncpg.connect(dsn, timeout=3)
        await conn.close()

    try:
        asyncio.run(probe())
    except Exception:
        return False
    return True


def test_database_url_or_skip() -> str:
    """The TEST_DATABASE_URL (env or .env). Skips the test unless ELX_REQUIRE_DB=1 is set."""
    url = Settings(  # type: ignore[call-arg]
        elx_secret_key="unused", elx_session_secret="unused-unused-unused"
    ).test_database_url
    if not dsn_reachable(to_dsn(url)):
        message = f"test database not reachable at {to_dsn(url)} (run `make dev-infra`)"
        if os.environ.get("ELX_REQUIRE_DB"):
            pytest.fail(message)
        pytest.skip(message)
    return url


def alembic_config_for(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def migrate(url: str) -> None:
    cfg = alembic_config_for(url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


async def truncate_all_async(url: str) -> None:
    """Empty every application table (keeps the schema). Fast per-test isolation."""
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    conn = await asyncpg.connect(to_dsn(url))
    try:
        await conn.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
    finally:
        await conn.close()


def truncate_all(url: str) -> None:
    """Synchronous variant for sync fixtures (must not be called from a running event loop)."""
    asyncio.run(truncate_all_async(url))
