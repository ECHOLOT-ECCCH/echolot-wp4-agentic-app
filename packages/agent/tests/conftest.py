"""Agent test fixtures: a migrated test database with one project and file, local storage."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from elexifier_agent.deps import AgentDeps, ProjectContext, UserContext
from elexifier_core.db.models import Document, Entry, File, Project, ProjectMember, User
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
from elexifier_pipeline.sample import OXFORD_XML
from elexifier_pipeline.xml import index as xml_index


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
    document_id: uuid.UUID | None = None


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
        # a second, analysed file: the Oxford sample with the entry index the parse job builds
        oxford_id = uuid.uuid4()
        oxford_key = make_key(str(project.id), "source", str(oxford_id), "oxford.xml")
        oxford_bytes = OXFORD_XML.read_bytes()
        oxford_stat = await storage.put(oxford_key, oxford_bytes, content_type="application/xml")
        session.add(
            File(
                id=oxford_id,
                project_id=project.id,
                kind="source",
                name="oxford.xml",
                s3_key=oxford_key,
                size=oxford_stat.size,
                sha256=oxford_stat.sha256 or "",
                mime="application/xml",
                created_by=user.id,
            )
        )
        await session.flush()
        profile = xml_index.profile_xml(OXFORD_XML)
        document = Document(
            project_id=project.id,
            source_file_id=oxford_id,
            type="xml",
            status="ready",
            entry_xpath="/oxford/se",
            entry_count=0,
            stats=profile.model_dump(),
        )
        session.add(document)
        await session.flush()
        refs = list(xml_index.index_entries(OXFORD_XML, "se", namespaces=profile.namespaces))
        for ref in refs:
            session.add(
                Entry(
                    document_id=document.id,
                    ordinal=ref.ordinal,
                    headword=ref.headword,
                    source_ref={"start": ref.start, "end": ref.end},
                    snippet=ref.snippet,
                )
            )
        document.entry_count = len(refs)
        await session.commit()
        document_id = document.id
        deps = AgentDeps(
            project=ProjectContext(id=project.id, name=project.name, source_type="xml"),
            user=UserContext(id=user.id, email=user.email, name=user.name, role="owner"),
            session_factory=db.session_factory,
            storage=storage,
            settings=settings,
        )
    try:
        yield Fixture(
            deps=deps,
            db=db,
            storage=storage,
            file_id=file_id,
            project_id=project.id,
            document_id=document_id,
        )
    finally:
        await db.dispose()
