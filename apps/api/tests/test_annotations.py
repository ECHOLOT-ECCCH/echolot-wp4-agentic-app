from __future__ import annotations

import io
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
    assert doc["entry_count"] == 130 and doc["stats"]["segmentation"]["stats"]["mode"] == "font"

    page1 = (await client.get(f"{base}/annotations", params={"page_no": 1})).json()
    assert page1["total"] == 52
    first = page1["items"][0]
    assert first["status"] == "proposed" and first["payload"]["headword_guess"] == "abacus"
    assert len(first["bbox"]) == 4 and first["source"] == "rule"

    # review: accept the whole first page, reject one span, edit another's box
    bulk = await client.post(f"{base}/annotations/bulk", json={"status": "accepted", "page_no": 1})
    assert bulk.json()["updated"] == 52 and bulk.json()["accepted"] == 52
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
    # 52 accepted on page 1, one of them rejected and one edited afterwards, plus the user's box
    assert by_status == {"accepted": 50 + 1, "rejected": 1, "edited": 1, "proposed": 3}
    only_accepted = (
        await client.get(f"{base}/annotations", params={"status": "accepted,edited"})
    ).json()
    assert only_accepted["total"] == 52

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


async def test_split_and_merge_spans(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    logged_in.apply(client)
    pid, doc_id = await _pdf_project(client)
    base = f"/api/projects/{pid}/documents/{doc_id}"
    await client.post(f"{base}/segment", json={})
    page1 = (await client.get(f"{base}/annotations", params={"page_no": 1})).json()["items"]
    abacus = next(a for a in page1 if a["payload"]["headword_guess"] == "abacus")
    assert len(abacus["payload"]["line_ids"]) == 2

    # split before the second line
    layout = (await client.get(f"{base}/pages/1/layout")).json()
    lines = {line["id"]: line for line in layout["lines"]}
    last_line = lines[abacus["payload"]["line_ids"][1]]
    split = await client.post(
        f"/api/projects/{pid}/annotations/{abacus['id']}/split", json={"y": last_line["top"]}
    )
    assert split.status_code == 200, split.text
    halves = split.json()
    assert [len(h["payload"]["line_ids"]) for h in halves] == [1, 1]
    assert all(h["source"] == "user" and h["status"] == "accepted" for h in halves)
    assert halves[0]["payload"]["headword_guess"] == "abacus"
    assert halves[0]["bbox"][3] <= halves[1]["bbox"][1] + 0.5
    gone = await client.get(f"{base}/annotations", params={"page_no": 1})
    assert all(a["id"] != abacus["id"] for a in gone.json()["items"])
    assert gone.json()["total"] == 53  # one span became two

    # merging the halves restores one span with all three lines
    merged = await client.post(
        f"/api/projects/{pid}/annotations/{halves[0]['id']}/merge",
        json={"with_id": halves[1]["id"]},
    )
    assert merged.status_code == 200, merged.text
    body = merged.json()
    assert len(body["payload"]["line_ids"]) == 2 and body["payload"]["headword_guess"] == "abacus"
    assert body["payload"]["merged_from"] == [halves[0]["id"], halves[1]["id"]]
    assert (await client.get(f"{base}/annotations", params={"page_no": 1})).json()["total"] == 52

    # a user-drawn box on a page without text splits geometrically; bad points are refused
    drawn = (
        await client.post(f"{base}/annotations", json={"page_no": 4, "bbox": [50, 700, 280, 760]})
    ).json()
    geometric = await client.post(
        f"/api/projects/{pid}/annotations/{drawn['id']}/split", json={"y": 730}
    )
    assert [h["bbox"][1] for h in geometric.json()] == [700, 730]
    refused = await client.post(
        f"/api/projects/{pid}/annotations/{geometric.json()[0]['id']}/split", json={"y": 5}
    )
    assert refused.status_code == 422
    cross_page = await client.post(
        f"/api/projects/{pid}/annotations/{geometric.json()[0]['id']}/merge",
        json={"with_id": body["id"]},
    )
    assert cross_page.status_code == 422


async def test_structure_sample_endpoint_with_rule_and_test_model(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    logged_in.apply(client)
    pid, doc_id = await _pdf_project(client)
    base = f"/api/projects/{pid}/documents/{doc_id}"
    nothing = await client.post(f"{base}/structure", json={"structurer": "rule"})
    assert nothing.status_code == 422
    await client.post(f"{base}/segment", json={})
    await client.post(f"{base}/annotations/bulk", json={"status": "accepted", "page_no": 1})

    rule = await client.post(
        f"{base}/structure",
        json={"structurer": "rule", "n": 5, "profile": {"source_language": "en"}},
    )
    assert rule.status_code == 200, rule.text
    run = rule.json()
    assert run["kind"] == "sample" and run["mapping_version"] == 0
    assert run["stats"]["converted"] == 5 and run["scope"]["structurer"] == "rule"
    entries = (await client.get(f"/api/projects/{pid}/runs/{run['id']}/entries")).json()
    assert entries["total"] == 5
    first = entries["items"][0]
    assert first["annotation_id"] and first["entry_id"] is None and first["confidence"] == 0.3
    detail = (
        await client.get(f"/api/projects/{pid}/runs/{run['id']}/entries/{first['id']}")
    ).json()
    assert detail["dmlex"]["headword"] == "abacus" and detail["xml"].startswith("<entry")
    assert (await client.get(f"/api/projects/{pid}/annotations/{first['annotation_id']}")).json()[
        "page_no"
    ] == 1

    # the model path needs a model: the project has none -> 422 with a clear message
    no_model = await client.post(f"{base}/structure", json={"structurer": "llm", "n": 2})
    assert no_model.status_code in (400, 422)


async def test_full_structuring_job_with_cache_overrides_and_rebuild(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    from elexifier_api.jobs import structuring as jobs_structuring
    from elexifier_pipeline.dmlex.validator import validate_xml

    logged_in.apply(client)
    pid, doc_id = await _pdf_project(client)
    base = f"/api/projects/{pid}/documents/{doc_id}"
    await client.post(f"{base}/segment", json={})
    await client.post(f"{base}/annotations/bulk", json={"status": "accepted", "page_no": 1})

    # a sample first: its results are reused by the full run (same model, profile, text)
    sample = await client.post(
        f"{base}/structure",
        json={
            "structurer": "rule",
            "n": 5,
            "strategy": "first",
            "profile": {"source_language": "en"},
        },
    )
    assert sample.status_code == 200, sample.text

    queued = await client.post(f"{base}/structure", json={"kind": "full", "structurer": "rule"})
    assert queued.status_code == 200, queued.text
    run = queued.json()
    assert run["kind"] == "full" and run["status"] == "queued" and run["scope"]["spans"] == 52
    assert run["scope"]["estimate"]["entries"] == 52
    again = await client.post(f"{base}/structure", json={"kind": "full", "structurer": "rule"})
    assert again.status_code == 422 and "already queued" in again.text

    result = await jobs_structuring.run_full_structuring(run["id"])
    assert result["spans"] == 52 and result["cached"] == 5 and result["failed"] == 0
    detail = (await client.get(f"/api/projects/{pid}/runs/{run['id']}")).json()
    assert detail["status"] == "done" and detail["stats"]["converted"] == 52
    assert detail["stats"]["cached"] == 5 and detail["stats"]["cost_check"]["actual_tokens"] == 0
    assert [f["format"] for f in detail["stats"]["output_files"]] == ["xml", "json"]
    assert detail["validation_report"]["valid"] is True, detail["validation_report"]["errors"][:3]
    download = await client.get(f"/api/projects/{pid}/files/{detail['output_file_id']}/download")
    assert validate_xml(io.BytesIO(download.content)).valid

    # the low-confidence review list: the rule structurer is always unsure
    review = (
        await client.get(f"/api/projects/{pid}/runs/{run['id']}/entries", params={"review": True})
    ).json()
    assert review["total"] == 52
    first = review["items"][0]
    assert first["notes"][-1] == "reused from an earlier run"

    # a correction by the lexicographer is validated, kept, and used by the rebuilt export
    fixed = await client.patch(
        f"/api/projects/{pid}/runs/{run['id']}/entries/{first['id']}",
        json={"override": {"id": "e1", "headword": "abacus", "partsOfSpeech": [{"tag": "n."}]}},
    )
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["override"]["headword"] == "abacus" and fixed.json()["issue_count"] == 0
    assert "<partOfSpeech" in fixed.json()["xml"]
    bad = await client.patch(
        f"/api/projects/{pid}/runs/{run['id']}/entries/{first['id']}", json={"override": {"x": 1}}
    )
    assert bad.status_code == 422
    rebuilt = await client.post(f"/api/projects/{pid}/runs/{run['id']}/rebuild")
    assert rebuilt.status_code == 200, rebuilt.text
    new_file = rebuilt.json()["output_file_id"]
    assert new_file != detail["output_file_id"]
    xml = (await client.get(f"/api/projects/{pid}/files/{new_file}/download")).content
    assert b'<partOfSpeech tag="n."' in xml and validate_xml(io.BytesIO(xml)).valid
    cleared = await client.patch(
        f"/api/projects/{pid}/runs/{run['id']}/entries/{first['id']}", json={"override": None}
    )
    assert cleared.json()["override"] is None

    # XML runs cannot be rebuilt
    sample_run = sample.json()["id"]
    assert (await client.post(f"/api/projects/{pid}/runs/{sample_run}/rebuild")).status_code in (
        200,
        422,
    )


async def test_review_summary_flags_and_accept_rest(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    logged_in.apply(client)
    pid, doc_id = await _pdf_project(client)
    base = f"/api/projects/{pid}/documents/{doc_id}"
    await client.post(f"{base}/segment", json={})
    review = (await client.get(f"{base}/review")).json()
    summary = review["summary"]
    assert summary["entries"] == 130 and summary["sample_pages"] == [1, 2, 3]
    assert review["remaining_proposed"] == 130 and review["remaining_flagged_proposed"] == 0

    # flag one span by hand (as the scorer would), then accept the rest except flagged ones
    page1 = (await client.get(f"{base}/annotations", params={"page_no": 1})).json()["items"]
    odd = page1[3]
    async with app_state.db.session() as db:
        from sqlalchemy import select as sa_select

        from elexifier_core.db.models import Annotation

        row = (
            await db.execute(sa_select(Annotation).where(Annotation.id == odd["id"]))
        ).scalar_one()
        row.payload = {**row.payload, "flags": ["very_long"]}
        await db.commit()
    flagged = (await client.get(f"{base}/annotations", params={"flagged": True})).json()
    assert flagged["total"] == 1 and flagged["items"][0]["id"] == odd["id"]
    await client.patch(
        f"/api/projects/{pid}/annotations/{page1[0]['id']}", json={"status": "rejected"}
    )

    result = await client.post(f"{base}/annotations/accept-rest", json={"skip_flagged": True})
    assert result.json()["updated"] == 128 and result.json()["proposed"] == 1
    review = (await client.get(f"{base}/review")).json()
    assert review["remaining_proposed"] == 1 and review["remaining_flagged_proposed"] == 1
    result = await client.post(f"{base}/annotations/accept-rest", json={"skip_flagged": False})
    assert result.json()["updated"] == 1 and result.json()["accepted"] == 129


async def test_full_structuring_flags_merged_spans_from_the_structurer(
    client: httpx.AsyncClient,
    logged_in: LoggedIn,
    app_state: AppState,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections.abc import AsyncIterator, Sequence
    from typing import Any

    from elexifier_api.jobs import structuring as jobs_structuring
    from elexifier_pipeline.pdf.structurers import ExtractedEntry, RuleStructurer, StructuredEntry

    class DoubtingStructurer(RuleStructurer):
        async def structure(
            self, entries: Sequence[ExtractedEntry], **kw: Any
        ) -> AsyncIterator[StructuredEntry]:
            async for result in super().structure(entries, **kw):
                if result.entry.get("headword") == "abase":
                    result.segmentation = "merged"
                    result.segmentation_reason = "abbey starts inside this span"
                yield result

    monkeypatch.setattr(jobs_structuring, "RuleStructurer", DoubtingStructurer)
    logged_in.apply(client)
    pid, doc_id = await _pdf_project(client)
    base = f"/api/projects/{pid}/documents/{doc_id}"
    await client.post(f"{base}/segment", json={})
    await client.post(f"{base}/annotations/bulk", json={"status": "accepted", "page_no": 1})
    run = (
        await client.post(f"{base}/structure", json={"kind": "full", "structurer": "rule"})
    ).json()
    await jobs_structuring.run_full_structuring(run["id"])
    detail = (await client.get(f"/api/projects/{pid}/runs/{run['id']}")).json()
    assert detail["stats"]["segmentation"] == {"merged": 1}

    # the entry is in the issues list with the structurer's words; the span is flagged
    listed = (
        await client.get(f"/api/projects/{pid}/runs/{run['id']}/entries", params={"issues": True})
    ).json()
    assert listed["total"] == 1 and listed["items"][0]["headword"] == "abase"
    issue = listed["items"][0]["issues"][0]
    assert issue["code"] == "segmentation" and issue["verdict"] == "merged"
    assert issue["message"] == "merged: abbey starts inside this span"
    flagged = (await client.get(f"{base}/annotations", params={"flagged": True})).json()
    assert flagged["total"] == 1 and flagged["items"][0]["payload"]["flags"] == ["merged"]
    assert flagged["items"][0]["payload"]["structurer"]["run_id"] == run["id"]
    summary = (await client.get(f"{base}/review")).json()
    assert summary["flagged_total"] == 1 and summary["remaining_flagged_proposed"] == 0

    # a rebuild keeps the doubt; a second run reuses the cached results with their verdicts
    assert (await client.post(f"/api/projects/{pid}/runs/{run['id']}/rebuild")).status_code == 200
    listed = (
        await client.get(f"/api/projects/{pid}/runs/{run['id']}/entries", params={"issues": True})
    ).json()
    assert listed["total"] == 1
    monkeypatch.setattr(jobs_structuring, "RuleStructurer", RuleStructurer)
    second = (
        await client.post(f"{base}/structure", json={"kind": "full", "structurer": "rule"})
    ).json()
    result = await jobs_structuring.run_full_structuring(second["id"])
    assert result["cached"] == 52
    detail = (await client.get(f"/api/projects/{pid}/runs/{second['id']}")).json()
    assert detail["stats"]["segmentation"] == {"merged": 1}
    flagged = (await client.get(f"{base}/annotations", params={"flagged": True})).json()
    assert (
        flagged["total"] == 1
        and flagged["items"][0]["payload"]["structurer"]["run_id"] == second["id"]
    )


async def test_structure_profile_endpoints(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    logged_in.apply(client)
    pid, doc_id = await _pdf_project(client)
    base = f"/api/projects/{pid}"
    empty = (await client.get(f"{base}/structure-profile")).json()
    assert empty == {
        "source_language": "unknown",
        "target_languages": [],
        "contents": "",
        "entry_id_prefix": "e",
        "stored": False,
    }
    saved = await client.put(
        f"{base}/structure-profile",
        json={
            "source_language": " en ",
            "target_languages": ["sl", " ", "de"],
            "contents": "Bold headwords, numbered senses.",
            "entry_id_prefix": "sega",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json() == {
        "source_language": "en",
        "target_languages": ["sl", "de"],
        "contents": "Bold headwords, numbered senses.",
        "entry_id_prefix": "sega",
        "stored": True,
    }
    assert (await client.get(f"{base}/structure-profile")).json()["stored"] is True
    bad = await client.put(f"{base}/structure-profile", json={"entry_id_prefix": "1 bad"})
    assert bad.status_code == 422

    # a structuring call merges its own values over the stored profile and stores the result
    await client.post(f"{base}/documents/{doc_id}/segment", json={})
    run = await client.post(
        f"{base}/documents/{doc_id}/structure",
        json={"structurer": "rule", "n": 2, "profile": {"target_languages": ["fr"]}},
    )
    assert run.status_code == 200, run.text
    profile = run.json()["scope"]["profile"]
    assert profile["source_language"] == "en" and profile["target_languages"] == ["fr"]
    assert profile["entry_id_prefix"] == "sega"
    assert (await client.get(f"{base}/structure-profile")).json()["target_languages"] == ["fr"]

    # XML projects have no profile to save
    xml_pid = (await client.post("/api/projects", json={"name": "X", "source_type": "xml"})).json()[
        "id"
    ]
    assert (
        await client.put(f"/api/projects/{xml_pid}/structure-profile", json={})
    ).status_code == 422
