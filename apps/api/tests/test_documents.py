from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy import select

from elexifier_api.jobs import documents as jobs_documents
from elexifier_api.jobs.runtime import reset_job_runtime
from elexifier_api.state import AppState
from elexifier_core.db.models import Document, Entry
from elexifier_pipeline.sample import COLUMNS_PDF, OXFORD_MAPPING, OXFORD_XML, SAMPLE_PDF

from .conftest import LoggedIn


@pytest.fixture(autouse=True)
def _job_runtime(settings) -> Iterator[None]:
    reset_job_runtime()
    yield
    reset_job_runtime()


async def _upload(client: httpx.AsyncClient, pid: str, path, name: str | None = None) -> dict:
    resp = await client.post(
        f"/api/projects/{pid}/files", params={"name": name or path.name}, content=path.read_bytes()
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_xml_upload_creates_document_and_job_indexes_entries(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    logged_in.apply(client)
    pid = (await client.post("/api/projects", json={"name": "Docs", "source_type": "xml"})).json()[
        "id"
    ]
    file = await _upload(client, pid, OXFORD_XML)

    docs = (await client.get(f"/api/projects/{pid}/documents")).json()
    assert len(docs) == 1
    doc = docs[0]
    assert (
        doc["status"] == "queued"
        and doc["file_name"] == "oxford.xml"
        and doc["source_file_id"] == file["id"]
    )
    async with app_state.db.session() as db:
        stored = (await db.execute(select(Document))).scalar_one()
        assert stored.job_id is not None  # deferred to the queue

    # run the job body inline (no worker process in tests)
    result = await jobs_documents.parse_document(doc["id"])
    assert result["entries"] == 102 and result["entry_xpath"] == "/oxford/se"

    detail = (await client.get(f"/api/projects/{pid}/documents/{doc['id']}")).json()
    assert detail["status"] == "ready" and detail["entry_count"] == 102
    assert detail["stats"]["root_tag"] == "oxford"
    assert detail["stats"]["entry_candidates"][0]["tag"] == "se"

    page = (
        await client.get(f"/api/projects/{pid}/documents/{doc['id']}/entries", params={"limit": 3})
    ).json()
    assert page["total"] == 102 and [e["headword"] for e in page["items"]] == ["azyme", "b", "BA"]
    search = (
        await client.get(
            f"/api/projects/{pid}/documents/{doc['id']}/entries", params={"q": "baby boom"}
        )
    ).json()
    assert [e["headword"] for e in search["items"]] == ["baby boom", "baby boomer"]

    entry = (await client.get(f"/api/projects/{pid}/entries/{page['items'][1]['id']}")).json()
    assert entry["headword"] == "b"
    assert entry["source_xml"].startswith("<se>") and entry["source_xml"].endswith("</se>")
    assert "<hw>b</hw>" in entry["source_xml"]


async def test_reparse_with_explicit_xpaths_and_errors(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    logged_in.apply(client)
    pid = (await client.post("/api/projects", json={"name": "Docs", "source_type": "xml"})).json()[
        "id"
    ]
    await _upload(client, pid, OXFORD_XML)
    doc = (await client.get(f"/api/projects/{pid}/documents")).json()[0]
    await jobs_documents.parse_document(doc["id"])

    # re-parse with a headword XPath and a different entry element
    resp = await client.post(
        f"/api/projects/{pid}/documents/{doc['id']}/reparse",
        json={"entry_xpath": "//tr", "headword_xpath": "string(.)"},
    )
    assert resp.status_code == 200 and resp.json()["status"] == "queued"
    result = await jobs_documents.parse_document(doc["id"])
    assert result["entries"] > 400
    async with app_state.db.session() as db:
        count = len((await db.execute(select(Entry))).scalars().all())
        assert count == result["entries"]  # old entries were replaced

    # an unusable entry XPath ends in an error status with a message
    await client.post(
        f"/api/projects/{pid}/documents/{doc['id']}/reparse", json={"entry_xpath": "//se[1]"}
    )
    with pytest.raises(Exception, match="plain element name"):
        await jobs_documents.parse_document(doc["id"])
    failed = (await client.get(f"/api/projects/{pid}/documents/{doc['id']}")).json()
    assert failed["status"] == "error" and "plain element name" in failed["error"]


async def test_only_sources_matching_the_project_type_are_analysed(
    client: httpx.AsyncClient, logged_in: LoggedIn
) -> None:
    logged_in.apply(client)
    pid = (await client.post("/api/projects", json={"name": "Docs", "source_type": "xml"})).json()[
        "id"
    ]
    pdf = await _upload(client, pid, SAMPLE_PDF)
    assert (await client.get(f"/api/projects/{pid}/documents")).json() == []
    # an explicit request still works for a PDF in an XML project (layout job)
    resp = await client.post(f"/api/projects/{pid}/documents", json={"file_id": pdf["id"]})
    assert resp.status_code == 201 and resp.json()["type"] == "pdf"
    text = await _upload(client, pid, OXFORD_MAPPING, name="notes.txt")
    resp = await client.post(f"/api/projects/{pid}/documents", json={"file_id": text["id"]})
    assert resp.status_code == 422
    # PDF projects do not auto-parse XML uploads either
    pdf_pid = (await client.post("/api/projects", json={"name": "P", "source_type": "pdf"})).json()[
        "id"
    ]
    await _upload(client, pdf_pid, OXFORD_XML)
    assert (await client.get(f"/api/projects/{pdf_pid}/documents")).json() == []


async def test_pdf_upload_runs_the_layout_job_and_serves_pages(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    from elexifier_api.jobs import pdf as jobs_pdf

    logged_in.apply(client)
    pid = (await client.post("/api/projects", json={"name": "PDF", "source_type": "pdf"})).json()[
        "id"
    ]
    file = await _upload(client, pid, COLUMNS_PDF)
    docs = (await client.get(f"/api/projects/{pid}/documents")).json()
    assert len(docs) == 1 and docs[0]["type"] == "pdf" and docs[0]["status"] == "queued"
    doc = docs[0]
    assert doc["source_file_id"] == file["id"]

    result = await jobs_pdf.extract_layout(doc["id"])
    assert result == {"document_id": doc["id"], "pages": 4, "scanned_pages": 1}

    detail = (await client.get(f"/api/projects/{pid}/documents/{doc['id']}")).json()
    assert detail["status"] == "ready" and detail["entry_count"] == 0
    assert detail["stats"]["page_count"] == 4 and detail["stats"]["scanned_pages"] == 1

    pages = (await client.get(f"/api/projects/{pid}/documents/{doc['id']}/pages")).json()
    assert [p["page_no"] for p in pages] == [1, 2, 3, 4]
    assert (
        pages[0]["width"] == 595.0 and pages[0]["line_count"] > 100 and not pages[0]["is_scanned"]
    )
    assert pages[3]["is_scanned"] and pages[3]["line_count"] == 0

    layout = await client.get(f"/api/projects/{pid}/documents/{doc['id']}/pages/1/layout")
    assert layout.status_code == 200
    body = layout.json()
    assert body["page"] == 1 and len(body["columns"]) == 2
    assert body["lines"][1]["words"][0]["text"] == "abacus" and body["lines"][1]["words"][0]["bold"]
    assert (
        await client.get(f"/api/projects/{pid}/documents/{doc['id']}/pages/9/layout")
    ).status_code == 404

    image = await client.get(
        f"/api/projects/{pid}/documents/{doc['id']}/pages/1/image", params={"dpi": 36}
    )
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert image.content[:8] == b"\x89PNG\r\n\x1a\n"
    again = await client.get(
        f"/api/projects/{pid}/documents/{doc['id']}/pages/1/image", params={"dpi": 36}
    )
    assert again.content == image.content  # served from storage the second time

    # re-running the job replaces the pages instead of duplicating them
    await jobs_pdf.extract_layout(doc["id"])
    assert len((await client.get(f"/api/projects/{pid}/documents/{doc['id']}/pages")).json()) == 4
