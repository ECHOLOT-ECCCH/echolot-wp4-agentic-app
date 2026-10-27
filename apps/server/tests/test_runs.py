"""Runs end to end with the LocalExecutor: the runner CLI as a subprocess talks to the app over
real HTTP and calls a fake service (docs/PLAN.md M1.4)."""

from __future__ import annotations

import asyncio
import json
import uuid

from echolot_app.db import Database
from echolot_app.viewing import Viewing, describe_viewing

from .conftest import UPPER, Live


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
