"""Agent test fixtures: a migrated test database with one project and file, local storage."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from elexifier_agent.deps import AgentDeps, ProjectContext, UserContext
from elexifier_core.db.models import File, Project, ProjectMember, User
from elexifier_core.db.session import Database
from elexifier_core.settings import Settings, reset_settings_cache
from elexifier_core.storage import LocalStorage, make_key
from elexifier_core.testing import (
    TEST_SECRET_KEY,
    TEST_SESSION_SECRET,
    migrate,
    test_database_url_or_skip,
    truncate_all_async,
)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELX_SECRET_KEY", TEST_SECRET_KEY)
    monkeypatch.setenv("ELX_SESSION_SECRET", TEST_SESSION_SECRET)
    monkeypatch.setenv("ELX_ENABLE_TEST_MODEL", "1")
    reset_settings_cache()


@pytest.fixture(scope="session")
def test_db_url() -> str:
    url = test_database_url_or_skip()
    migrate(url)
    return url


@dataclass
class Fixture:
    deps: AgentDeps
    db: Database
    storage: LocalStorage
    file_id: uuid.UUID
    project_id: uuid.UUID


@pytest.fixture
async def fixture(test_db_url: str, tmp_path: Path) -> AsyncIterator[Fixture]:
    await truncate_all_async(test_db_url)
    db = Database(url=test_db_url)
    storage = LocalStorage(tmp_path / "storage")
    settings = Settings(database_url=test_db_url)  # type: ignore[call-arg]
    async with db.session() as session:
        user = User(oidc_sub="sub-agent", email="agent@example.com", name="Ada")
        session.add(user)
        await session.flush()
        project = Project(owner_id=user.id, name="Test dictionary", source_type="xml")
        session.add(project)
        await session.flush()
        session.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
        file_id = uuid.uuid4()
        key = make_key(str(project.id), "source", str(file_id), "dict.xml")
        body = (
            b"<dictionary>\n"
            + b"".join(
                f"  <entry id='e{i}'><form><orth>word{i}</orth></form></entry>\n".encode()
                for i in range(200)
            )
            + b"</dictionary>\n"
        )
        stat = await storage.put(key, body, content_type="application/xml")
        session.add(
            File(
                id=file_id,
                project_id=project.id,
                kind="source",
                name="dict.xml",
                s3_key=key,
                size=stat.size,
                sha256=stat.sha256 or "",
                mime="application/xml",
                created_by=user.id,
            )
        )
        await session.commit()
        deps = AgentDeps(
            project=ProjectContext(id=project.id, name=project.name, source_type="xml"),
            user=UserContext(id=user.id, email=user.email, name=user.name, role="owner"),
            session_factory=db.session_factory,
            storage=storage,
            settings=settings,
        )
    try:
        yield Fixture(deps=deps, db=db, storage=storage, file_id=file_id, project_id=project.id)
    finally:
        await db.dispose()
