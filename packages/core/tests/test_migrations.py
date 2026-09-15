"""Migration round trip against the test database."""

from __future__ import annotations

import asyncio

import asyncpg
from alembic import command
from alembic.config import Config

EXPECTED_TABLES = {
    "users",
    "sessions",
    "provider_credentials",
    "audit_log",
    "projects",
    "project_members",
    "files",
    "conversations",
    "messages",
    "agent_runs",
    "agent_events",
    "approvals",
    "usage_records",
}


def _tables(dsn: str) -> set[str]:
    async def query() -> set[str]:
        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        finally:
            await conn.close()
        return {r["tablename"] for r in rows}

    return asyncio.run(query())


def test_upgrade_and_downgrade(alembic_config: Config, test_db_url: str) -> None:
    dsn = test_db_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    assert _tables(dsn) >= EXPECTED_TABLES
    command.downgrade(alembic_config, "base")
    assert not (EXPECTED_TABLES & _tables(dsn))
    command.upgrade(alembic_config, "head")
