"""Runs end to end with the LocalExecutor: the runner CLI as a subprocess talks to the app over
real HTTP and calls a fake service (docs/PLAN.md M1.4)."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import uuid

import pytest

from echolot_app.db import Database
from echolot_app.viewing import Viewing, describe_viewing

from .conftest import UPPER, Live


async def test_sample_and_full_run_rows_export_and_retry(live: Live) -> None:
    await live.script(UPPER)
    base = f"/api/workspaces/{live.workspace_id}/runs"

    sample = await live.run(limit=10, purpose="try ten")
    assert sample["status"] in ("queued", "running")
    assert sample["progress"]["total"] == 10
    assert sample["services"] == ["upper"] and sample["script_version"] == 1
    done = await live.wait(sample["id"])
    assert done["status"] == "done", done
    assert done["counts"] == {"total": 10, "ok": 10, "error": 0}
    assert done["started_at"] and done["finished_at"]

    full = await live.wait((await live.run(concurrency=8))["id"])
    assert full["status"] == "done"
    assert full["counts"] == {"total": 50, "ok": 49, "error": 1}

    page = (
        await live.client.get(f"{base}/{full['id']}/rows", params={"offset": 16, "limit": 3})
    ).json()
    assert page["total"] == 50 and page["input_columns"] == ["name", "born"]
    assert page["output_columns"] == ["upper", "length"]
    first, failed, _third = page["rows"]
    assert first["input"] == {"name": "person 16", "born": "1916"}
    assert first["output"] == {"upper": "PERSON 16", "length": 9}
    assert failed["row"] == 17 and failed["status"] == "error"
    assert failed["error"] == "ServiceError: 502 from upper: upstream failed (after 2 attempts)"
    errors = (await live.client.get(f"{base}/{full['id']}/rows", params={"status": "error"})).json()
    assert errors["total"] == 1 and errors["rows"][0]["input"]["name"] == "boom"

    exported = await live.client.get(f"{base}/{full['id']}/export")
    assert exported.headers["content-type"].startswith("text/csv")
    table = list(csv.reader(io.StringIO(exported.text)))
    assert table[0] == ["name", "born", "upper", "length", "status", "error"]
    assert table[1] == ["person 0", "1900", "PERSON 0", "8", "ok", ""]
    assert table[18][:2] == ["boom", "1917"] and table[18][4] == "error"
    assert len(table) == 51
    jsonl = await live.client.get(f"{base}/{full['id']}/export", params={"format": "jsonl"})
    assert json.loads(jsonl.text.splitlines()[0])["output"]["upper"] == "PERSON 0"

    retry = await live.client.post(f"{base}/{full['id']}/retry-failed")
    assert retry.status_code == 201
    assert retry.json()["selected_rows"] == 1 and retry.json()["parent_run_id"] == full["id"]
    retried = await live.wait(retry.json()["id"])
    assert retried["counts"] == {"total": 1, "ok": 0, "error": 1}
    rows = (await live.client.get(f"{base}/{retried['id']}/rows")).json()["rows"]
    assert [r["row"] for r in rows] == [17]

    listed = (await live.client.get(base)).json()
    assert [r["id"] for r in listed] == [retried["id"], full["id"], sample["id"]]
    results = (
        await live.client.get(
            f"/api/workspaces/{live.workspace_id}/files", params={"kind": "result"}
        )
    ).json()
    assert len(results) == 3 and results[0]["meta"]["format"] == "jsonl"


@pytest.mark.skipif(
    bool(os.environ.get("ECHO_TEST_PREFECT_API_URL")),
    reason="reads the LocalExecutor's params file for the token",
)
async def test_the_runner_endpoints_need_the_run_token(live: Live) -> None:
    await live.script(UPPER)
    run = await live.wait((await live.run(limit=2))["id"])
    url = f"/runner/runs/{run['id']}/input"
    assert (await live.client.get(url)).status_code == 404
    assert (
        await live.client.get(url, headers={"Authorization": "Bearer wrong"})
    ).status_code == 404
    params = json.loads((live.settings.data_dir / "runner" / run["id"] / "params.json").read_text())
    # the right token stops working once the run is finished
    finished = await live.client.get(url, headers={"Authorization": f"Bearer {params['token']}"})
    assert finished.status_code == 404


async def test_cancel_a_slow_run(live: Live) -> None:
    await live.script(
        "import time\n\ndef process(record, ctx):\n    time.sleep(0.5)\n    return {}\n"
    )
    run = await live.run(concurrency=1)
    for _ in range(100):  # wait until the runner has started
        current = (
            await live.client.get(f"/api/workspaces/{live.workspace_id}/runs/{run['id']}")
        ).json()
        if current["status"] == "running":
            break
        await asyncio.sleep(0.05)
    cancelled = await live.client.post(
        f"/api/workspaces/{live.workspace_id}/runs/{run['id']}/cancel"
    )
    assert cancelled.status_code == 200
    final = await live.wait(run["id"])
    assert final["status"] == "cancelled"
    assert final["result_file_id"] is None


async def test_run_level_failures_are_reported(live: Live) -> None:
    await live.script(
        "def setup(ctx):\n    raise RuntimeError('lookup table missing')\n\n"
        "def process(record, ctx):\n    return {}\n"
    )
    failed = await live.wait((await live.run(limit=3))["id"])
    assert failed["status"] == "failed"
    assert failed["error"] == "setup failed: RuntimeError: lookup table missing"
    rows = (
        await live.client.get(f"/api/workspaces/{live.workspace_id}/runs/{failed['id']}/rows")
    ).json()
    assert rows["total"] == 0 and rows["run_status"] == "failed"
    exported = await live.client.get(
        f"/api/workspaces/{live.workspace_id}/runs/{failed['id']}/export"
    )
    assert exported.status_code == 409


async def test_runs_need_a_script_and_records(live: Live) -> None:
    response = await live.client.post(
        f"/api/workspaces/{live.workspace_id}/runs", json={"file_id": live.file_id}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "there is no script to run yet"
    await live.script(UPPER)
    txt = (
        await live.client.post(
            f"/api/workspaces/{live.workspace_id}/files", params={"name": "n.txt"}, content=b"x"
        )
    ).json()["id"]
    response = await live.client.post(
        f"/api/workspaces/{live.workspace_id}/runs", json={"file_id": txt}
    )
    assert response.status_code == 422
    assert "has no records" in response.json()["detail"]


async def test_viewing_names_an_open_run(live: Live) -> None:
    await live.script(UPPER)
    run = await live.wait((await live.run(limit=2))["id"])
    db = Database(live.settings)  # the app's own database, through a second engine
    try:
        async with db.session() as session:
            described = await describe_viewing(
                session,
                uuid.UUID(live.workspace_id),
                Viewing.model_validate({"kind": "run", "runId": run["id"], "row": 1}),
            )
    finally:
        await db.dispose()
    assert described is not None
    assert described[0] == f"row 1 of the results of run {run['id']} (done, script v1, on p.csv)"
