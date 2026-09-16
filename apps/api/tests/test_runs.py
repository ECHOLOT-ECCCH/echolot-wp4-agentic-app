from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest

from elexifier_api.jobs import documents as jobs_documents
from elexifier_api.jobs.runtime import reset_job_runtime
from elexifier_api.state import AppState
from elexifier_core.db.models import Mapping
from elexifier_pipeline.sample import OXFORD_MAPPING, OXFORD_XML

from .conftest import LoggedIn


@pytest.fixture(autouse=True)
def _job_runtime(settings) -> Iterator[None]:
    reset_job_runtime()
    yield
    reset_job_runtime()


async def _parsed_project(client: httpx.AsyncClient, app_state: AppState) -> tuple[str, str]:
    pid = (await client.post("/api/projects", json={"name": "Runs", "source_type": "xml"})).json()[
        "id"
    ]
    resp = await client.post(
        f"/api/projects/{pid}/files", params={"name": "oxford.xml"}, content=OXFORD_XML.read_bytes()
    )
    assert resp.status_code == 201, resp.text
    doc = (await client.get(f"/api/projects/{pid}/documents")).json()[0]
    await jobs_documents.parse_document(doc["id"])
    async with app_state.db.session() as db:
        db.add(
            Mapping(
                project_id=pid,
                version=1,
                spec=json.loads(OXFORD_MAPPING.read_text(encoding="utf-8")),
                note="owner mapping",
            )
        )
        await db.commit()
    return pid, doc["id"]


async def test_sample_run_routes(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    logged_in.apply(client)
    pid, doc_id = await _parsed_project(client, app_state)
    assert (await client.get(f"/api/projects/{pid}/runs")).json() == []

    created = await client.post(
        f"/api/projects/{pid}/runs", json={"kind": "sample", "ordinals": [0, 1, 2]}
    )
    assert created.status_code == 201, created.text
    run = created.json()
    assert run["kind"] == "sample" and run["status"] == "done" and run["mapping_version"] == 1
    assert run["stats"]["converted"] == 3 and run["document_id"] == doc_id
    assert run["validation_report"]["resource_scalars"]["langCode"] == "en"

    listed = (await client.get(f"/api/projects/{pid}/runs")).json()
    assert [r["id"] for r in listed] == [run["id"]]
    detail = (await client.get(f"/api/projects/{pid}/runs/{run['id']}")).json()
    assert detail["scope"]["ordinals"] == [0, 1, 2]

    page = (await client.get(f"/api/projects/{pid}/runs/{run['id']}/entries")).json()
    assert page["total"] == 3 and [e["headword"] for e in page["items"]] == ["azyme", "b", "BA"]
    first = page["items"][0]
    one = (await client.get(f"/api/projects/{pid}/runs/{run['id']}/entries/{first['id']}")).json()
    assert one["dmlex"]["headword"] == "azyme" and one["xml"].startswith("<entry")
    assert one["mapping_version"] == 1

    # the converted output of a source entry, latest run by default
    by_entry = await client.get(f"/api/projects/{pid}/entries/{first['entry_id']}/converted")
    assert by_entry.status_code == 200 and by_entry.json()[0]["id"] == first["id"]
    with_run = await client.get(
        f"/api/projects/{pid}/entries/{first['entry_id']}/converted", params={"run_id": run["id"]}
    )
    assert with_run.json()[0]["dmlex"] == one["dmlex"]
    other_entries = (
        await client.get(
            f"/api/projects/{pid}/documents/{doc_id}/entries", params={"offset": 40, "limit": 5}
        )
    ).json()
    unconverted = other_entries["items"][0]
    assert (
        await client.get(f"/api/projects/{pid}/entries/{unconverted['id']}/converted")
    ).json() == []

    only_issues = (
        await client.get(f"/api/projects/{pid}/runs/{run['id']}/entries", params={"issues": True})
    ).json()
    assert only_issues["total"] == sum(1 for e in page["items"] if e["issue_count"])

    # validation errors
    assert (
        await client.post(f"/api/projects/{pid}/runs", json={"mapping_version": 9})
    ).status_code == 422
    assert (
        await client.post(f"/api/projects/{pid}/runs", json={"ordinals": [99999]})
    ).status_code == 422
    assert (await client.get(f"/api/projects/{pid}/runs/{doc_id}")).status_code == 404
