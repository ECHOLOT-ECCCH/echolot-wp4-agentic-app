from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from elexifier_api.jobs import pdf as jobs_pdf
from elexifier_api.jobs.runtime import reset_job_runtime
from elexifier_api.state import AppState
from elexifier_pipeline.sample import COLUMNS_PDF

from .conftest import LoggedIn


@pytest.fixture(autouse=True)
def _job_runtime(settings) -> Iterator[None]:
    reset_job_runtime()
    yield
    reset_job_runtime()


async def _pdf_project(client: httpx.AsyncClient) -> tuple[str, str]:
    pid = (await client.post("/api/projects", json={"name": "PDF", "source_type": "pdf"})).json()[
        "id"
    ]
    resp = await client.post(
        f"/api/projects/{pid}/files",
        params={"name": "columns.pdf"},
        content=COLUMNS_PDF.read_bytes(),
        headers={"Content-Type": "application/pdf"},
    )
    assert resp.status_code == 201, resp.text
    doc = (await client.get(f"/api/projects/{pid}/documents")).json()[0]
    await jobs_pdf.extract_layout(doc["id"])
    return pid, doc["id"]


async def test_segment_review_and_edit_annotations(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    logged_in.apply(client)
    pid, doc_id = await _pdf_project(client)
    base = f"/api/projects/{pid}/documents/{doc_id}"

    empty = (await client.get(f"{base}/annotations")).json()
    assert empty["total"] == 0

    segmented = await client.post(f"{base}/segment", json={"segmenter": "rule"})
    assert segmented.status_code == 200, segmented.text
    body = segmented.json()
    assert body["queued"] is False and body["stats"]["spans"] == 130 and body["set_id"]
    doc = (await client.get(base)).json()
    assert doc["entry_count"] == 130 and doc["stats"]["segmentation"]["stats"]["mode"] == "bold"

    page1 = (await client.get(f"{base}/annotations", params={"page_no": 1})).json()
    assert page1["total"] == 58
    first = page1["items"][0]
    assert first["status"] == "proposed" and first["payload"]["headword_guess"] == "abacus"
    assert len(first["bbox"]) == 4 and first["source"] == "rule"

    # review: accept the whole first page, reject one span, edit another's box
    bulk = await client.post(f"{base}/annotations/bulk", json={"status": "accepted", "page_no": 1})
    assert bulk.json()["updated"] == 58 and bulk.json()["accepted"] == 58
    rejected = await client.patch(
        f"/api/projects/{pid}/annotations/{page1['items'][1]['id']}", json={"status": "rejected"}
    )
    assert rejected.json()["status"] == "rejected"
    moved = await client.patch(
        f"/api/projects/{pid}/annotations/{first['id']}",
        json={"bbox": [first["bbox"][0], first["bbox"][1], first["bbox"][2], first["bbox"][3] + 5]},
    )
    assert moved.json()["status"] == "edited"
    assert moved.json()["payload"]["original_bbox"] == first["bbox"]
    doc = (await client.get(base)).json()
    assert doc["entry_count"] == 129  # one rejected

    # the lexicographer draws a box of their own
    created = await client.post(
        f"{base}/annotations", json={"page_no": 2, "bbox": [50, 100, 280, 130], "label": "entry"}
    )
    assert created.status_code == 201
    own = created.json()
    assert own["source"] == "user" and own["status"] == "accepted" and own["entry_span_id"]
    too_far = await client.post(f"{base}/annotations", json={"page_no": 99, "bbox": [0, 0, 1, 1]})
    assert too_far.status_code == 422

    # re-segmenting replaces proposals only: decisions and the user's box survive
    again = await client.post(f"{base}/segment", json={"params": {"mode": "size"}})
    assert again.json()["stats"]["spans"] == 3
    statuses = (await client.get(f"{base}/annotations", params={"limit": 2000})).json()
    by_status: dict[str, int] = {}
    for item in statuses["items"]:
        by_status[item["status"]] = by_status.get(item["status"], 0) + 1
    # 58 accepted on page 1, one of them rejected and one edited afterwards, plus the user's box
    assert by_status == {"accepted": 56 + 1, "rejected": 1, "edited": 1, "proposed": 3}
    only_accepted = (
        await client.get(f"{base}/annotations", params={"status": "accepted,edited"})
    ).json()
    assert only_accepted["total"] == 58

    deleted = await client.delete(f"/api/projects/{pid}/annotations/{own['id']}")
    assert deleted.status_code == 204
    assert (await client.delete(f"/api/projects/{pid}/annotations/{own['id']}")).status_code == 404

    bad = await client.post(f"{base}/segment", json={"params": {"mode": "magic"}})
    assert bad.status_code == 422

    # the worker path produces the same result
    result = await jobs_pdf.segment_document_job(doc_id, "rule", {"mode": "bold"})
    assert result["spans"] == 130


async def test_annotations_need_a_pdf_document(
    client: httpx.AsyncClient, logged_in: LoggedIn
) -> None:
    from elexifier_pipeline.sample import OXFORD_XML

    logged_in.apply(client)
    pid = (await client.post("/api/projects", json={"name": "X", "source_type": "xml"})).json()[
        "id"
    ]
    await client.post(
        f"/api/projects/{pid}/files", params={"name": "oxford.xml"}, content=OXFORD_XML.read_bytes()
    )
    doc = (await client.get(f"/api/projects/{pid}/documents")).json()[0]
    resp = await client.get(f"/api/projects/{pid}/documents/{doc['id']}/annotations")
    assert resp.status_code == 422
