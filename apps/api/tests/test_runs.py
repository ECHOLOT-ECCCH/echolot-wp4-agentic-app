from __future__ import annotations

import io
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


async def test_full_run_job_writes_outputs_and_report(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    from elexifier_api.jobs import conversions as jobs_conversions
    from elexifier_pipeline.dmlex.validator import validate_xml

    logged_in.apply(client)
    pid, _doc_id = await _parsed_project(client, app_state)

    created = await client.post(f"/api/projects/{pid}/runs", json={"kind": "full"})
    assert created.status_code == 201, created.text
    run = created.json()
    assert run["kind"] == "full" and run["status"] == "queued"
    assert run["stats"] == {"phase": "queued", "done": 0, "total": 102}
    duplicate = await client.post(f"/api/projects/{pid}/runs", json={"kind": "full"})
    assert duplicate.status_code == 422 and "already queued" in duplicate.text

    # run the job body inline (no worker process in tests)
    result = await jobs_conversions.run_full_conversion(run["id"])
    assert result["converted"] == 102 and result["valid"] is True

    detail = (await client.get(f"/api/projects/{pid}/runs/{run['id']}")).json()
    assert detail["status"] == "done" and detail["output_file_id"] == result["xml_file_id"]
    stats = detail["stats"]
    assert stats["converted"] == 102 and stats["phase"] == "done"
    assert [f["format"] for f in stats["output_files"]] == ["xml", "json"]
    report = detail["validation_report"]
    assert report["valid"] is True and report["summary"].startswith("valid DMLex XML")
    assert report["resource_scalars"]["langCode"] == "en" and "timings" in report

    files = (await client.get(f"/api/projects/{pid}/files")).json()
    kinds = {f["id"]: f["kind"] for f in files}
    assert kinds[result["xml_file_id"]] == "output" and kinds[result["json_file_id"]] == "output"
    assert kinds[stats["report_file_id"]] == "artifact"

    # the export downloads and validates on its own
    download = await client.get(f"/api/projects/{pid}/files/{result['xml_file_id']}/download")
    assert download.status_code == 200
    assert download.content.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')
    assert validate_xml(io.BytesIO(download.content)).valid
    exported_json = await client.get(f"/api/projects/{pid}/files/{result['json_file_id']}/download")
    assert len(json.loads(exported_json.content)["entries"]) == 102

    # only entries with findings get rows (here: consistency warnings, no schema errors)
    issues = (await client.get(f"/api/projects/{pid}/runs/{run['id']}/entries")).json()
    assert issues["total"] == stats["with_issues"] > 0 and stats["errors"] == 0
    assert all(
        i["severity"] == "warning" and i["source"] == "consistency"
        for row in issues["items"]
        for i in row["issues"]
    )
    assert all(row["entry_id"] and row["ordinal"] is not None for row in issues["items"])
    listed = (await client.get(f"/api/projects/{pid}/runs")).json()
    assert listed[0]["id"] == run["id"] and listed[0]["status"] == "done"


async def test_full_run_job_records_failures(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    from elexifier_api.jobs import conversions as jobs_conversions

    logged_in.apply(client)
    pid, _doc_id = await _parsed_project(client, app_state)
    async with app_state.db.session() as db:
        # a mapping that loads but cannot be applied: the entry selector is a union
        db.add(
            Mapping(
                project_id=pid,
                version=2,
                spec={
                    "inSelector": "/oxford",
                    "outElement": "lexicographicResource",
                    "children": [{"inSelector": "se | pv", "outElement": "entry"}],
                },
            )
        )
        await db.commit()
    refused = await client.post(f"/api/projects/{pid}/runs", json={"kind": "full"})
    assert refused.status_code == 422 and "cannot be applied" in refused.text

    created = await client.post(
        f"/api/projects/{pid}/runs", json={"kind": "full", "mapping_version": 1}
    )
    run_id = created.json()["id"]
    async with app_state.db.session() as db:
        # break the run before the job starts: the source file disappears
        from sqlalchemy import update

        from elexifier_core.db.models import ConversionRun

        await db.execute(
            update(ConversionRun).where(ConversionRun.id == run_id).values(document_id=None)
        )
        await db.commit()
    with pytest.raises(jobs_conversions.ConversionJobError):
        await jobs_conversions.run_full_conversion(run_id)
