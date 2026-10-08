"""Export a project with everything in it, remove it, import it under another owner: the same
rows and stored objects come back with their ids, every "who" column points at the new owner."""

from __future__ import annotations

import contextlib
import io
import uuid

import httpx
from sqlalchemy import func, select, text

from elexifier_core.db.models import (
    Annotation,
    ConversionRun,
    ConvertedEntry,
    Document,
    File,
    PdfPage,
    PipelineStep,
    Project,
    ProjectMember,
)
from elexifier_core.portability import PortabilityError, export_project, import_project
from elexifier_core.storage import NotFoundError

from .conftest import AppState, LoggedIn, make_logged_in
from .test_annotations import _pdf_project
from .test_steps import _artifact


async def _counts(app_state: AppState, pid: str) -> dict[str, int]:
    async with app_state.db.session() as db:
        doc_ids = select(Document.id).where(Document.project_id == uuid.UUID(pid))
        run_ids = select(ConversionRun.id).where(ConversionRun.project_id == uuid.UUID(pid))
        return {
            "files": await db.scalar(
                select(func.count()).select_from(File).where(File.project_id == uuid.UUID(pid))
            )
            or 0,
            "pages": await db.scalar(
                select(func.count()).select_from(PdfPage).where(PdfPage.document_id.in_(doc_ids))
            )
            or 0,
            "spans": await db.scalar(
                select(func.count())
                .select_from(Annotation)
                .where(Annotation.document_id.in_(doc_ids))
            )
            or 0,
            "runs": await db.scalar(
                select(func.count())
                .select_from(ConversionRun)
                .where(ConversionRun.project_id == uuid.UUID(pid))
            )
            or 0,
            "converted": await db.scalar(
                select(func.count())
                .select_from(ConvertedEntry)
                .where(ConvertedEntry.run_id.in_(run_ids))
            )
            or 0,
            "steps": await db.scalar(
                select(func.count())
                .select_from(PipelineStep)
                .where(PipelineStep.project_id == uuid.UUID(pid))
            )
            or 0,
            "messages": (
                await db.execute(
                    text(
                        "select count(*) from messages m join conversations c "
                        "on c.id = m.conversation_id where c.project_id = :p"
                    ),
                    {"p": pid},
                )
            ).scalar_one(),
        }


async def test_export_then_import_round_trip(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    logged_in.apply(client)
    pid, doc_id = await _pdf_project(client)
    base = f"/api/projects/{pid}"
    # a project with a bit of everything: spans, a decision, a structured sample, a step, a chat
    await client.post(f"{base}/documents/{doc_id}/segment", json={})
    await client.post(
        f"{base}/documents/{doc_id}/annotations/bulk", json={"status": "accepted", "page_no": 1}
    )
    run = await client.post(
        f"{base}/documents/{doc_id}/structure",
        json={"structurer": "rule", "n": 3, "profile": {"source_language": "en"}},
    )
    assert run.status_code == 200, run.text
    code_id = await _artifact(app_state, pid, "code-x.py", "text/x-python", b"print(1)")
    await client.post(f"{base}/steps", json={"name": "A step", "code_artifact_id": code_id})
    cid = (await client.post(f"{base}/conversations", json={"title": "Chat"})).json()["id"]
    await client.put(
        f"{base}/conversations/{cid}/messages",
        json={
            "messages": [{"id": "m1", "role": "user", "parts": [{"type": "text", "text": "hi"}]}]
        },
    )
    await client.patch(base, json={"default_model": {"provider": "test", "model_id": "test"}})
    before = await _counts(app_state, pid)
    assert before["spans"] == 130 and before["pages"] == 4 and before["converted"] == 3
    assert before["steps"] == 1 and before["messages"] == 1 and before["files"] >= 2
    async with app_state.db.session() as db:
        layout_key = (
            await db.execute(
                select(PdfPage.layout_s3_key)
                .where(PdfPage.document_id == uuid.UUID(doc_id))
                .limit(1)
            )
        ).scalar_one()
        source_key = (
            await db.execute(
                select(File.s3_key).where(File.project_id == uuid.UUID(pid), File.kind == "source")
            )
        ).scalar_one()

    # export
    archive = io.BytesIO()
    async with app_state.db.session() as db:
        manifest = await export_project(db, app_state.storage, uuid.UUID(pid), archive)
    assert manifest["tables"]["annotations"] == 130 and manifest["tables"]["pdf_pages"] == 4
    assert manifest["tables"]["converted_entries"] == 3 and manifest["tables"]["messages"] == 1
    assert manifest["missing_objects"] == []
    keys = {o["key"] for o in manifest["objects"]}
    assert source_key in keys and layout_key in keys and code_id in " ".join(keys)

    # the project disappears, stored objects included
    assert (await client.delete(base)).status_code == 204
    async with app_state.db.session() as db:
        for key in keys:
            with contextlib.suppress(NotFoundError):
                await app_state.storage.delete(key)
    assert (await client.get(base)).status_code == 404

    # import under another user, who has never seen it
    other = await make_logged_in(app_state, email="colleague@example.org", name="Colleague")
    archive.seek(0)
    async with app_state.db.session() as db:
        result = await import_project(db, app_state.storage, archive, "colleague@example.org")
        await db.commit()
    assert result["project_id"] == pid and result["objects"] == len(keys)
    assert await _counts(app_state, pid) == before
    async with app_state.db.session() as db:
        project = await db.get(Project, uuid.UUID(pid))
        assert project is not None and project.owner_id == other.user.id
        assert project.default_model is None  # the model choice does not travel
        member = await db.get(ProjectMember, (uuid.UUID(pid), other.user.id))
        assert member is not None and member.role == "owner"
        who = (
            (
                await db.execute(
                    select(ConversionRun.triggered_by).where(
                        ConversionRun.project_id == uuid.UUID(pid)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert set(who) == {other.user.id}
        created = (
            (await db.execute(select(File.created_by).where(File.project_id == uuid.UUID(pid))))
            .scalars()
            .all()
        )
        assert set(created) <= {other.user.id, None}
    # the new owner can use it through the API, with the stored objects back in place
    other.apply(client)
    assert (await client.get(base)).json()["my_role"] == "owner"
    listed = (
        await client.get(f"{base}/documents/{doc_id}/annotations", params={"page_no": 1})
    ).json()
    assert listed["total"] == 52 and listed["items"][0]["status"] == "accepted"
    layout = await client.get(f"{base}/documents/{doc_id}/pages/1/layout")
    assert layout.status_code == 200 and len(layout.json()["lines"]) > 50
    runs = (await client.get(f"{base}/runs")).json()
    assert len(runs) == 1 and runs[0]["stats"]["converted"] == 3
    # the original owner lost it
    logged_in.apply(client)
    assert (await client.get(base)).status_code == 404

    # importing again is refused, and so is an unknown owner
    archive.seek(0)
    async with app_state.db.session() as db:
        try:
            await import_project(db, app_state.storage, archive, "colleague@example.org")
            raise AssertionError("expected a refusal")
        except PortabilityError as exc:
            assert "already exists" in str(exc)
    archive.seek(0)
    async with app_state.db.session() as db:
        try:
            await import_project(db, app_state.storage, archive, "nobody@example.org")
            raise AssertionError("expected a refusal")
        except PortabilityError as exc:
            assert "must log in once" in str(exc)
