from __future__ import annotations

from datetime import UTC

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from echolot_app.db import File, FileKind, Script, Workspace
from echolot_app.state import AppState


async def test_sqlite_runs_in_wal_mode_with_foreign_keys(state: AppState) -> None:
    async with state.db.session() as session:
        assert (await session.execute(text("PRAGMA journal_mode"))).scalar() == "wal"
        assert (await session.execute(text("PRAGMA foreign_keys"))).scalar() == 1


async def test_timestamps_come_back_aware_utc(state: AppState) -> None:
    async with state.db.session() as session:
        session.add(Workspace(name="w"))
        await session.commit()
    async with state.db.session() as session:
        workspace = (await session.execute(select(Workspace))).scalar_one()
        assert workspace.created_at.tzinfo is UTC
        assert workspace.task_md == ""


async def test_deleting_a_workspace_cascades(state: AppState) -> None:
    async with state.db.session() as session:
        workspace = Workspace(name="w")
        session.add(workspace)
        await session.flush()
        session.add(File(workspace_id=workspace.id, kind=FileKind.INPUT, name="a", rel_path="k"))
        await session.commit()
        await session.delete(workspace)
        await session.commit()
        assert (await session.execute(select(File))).first() is None


async def test_script_versions_are_unique_per_workspace(state: AppState) -> None:
    async with state.db.session() as session:
        workspace = Workspace(name="w")
        session.add(workspace)
        await session.flush()
        for _ in range(2):
            session.add(
                Script(workspace_id=workspace.id, version=1, code="", sha256="x", author="user")
            )
        with pytest.raises(IntegrityError):
            await session.commit()
