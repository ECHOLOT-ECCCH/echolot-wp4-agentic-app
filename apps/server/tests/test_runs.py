"""Runs end to end with the LocalExecutor: the runner CLI as a subprocess talks to the app over
real HTTP and calls a fake service (docs/PLAN.md M1.4)."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import socket
import threading
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

from echolot_app.db import Database
from echolot_app.main import create_app
from echolot_app.settings import Settings
from echolot_app.viewing import Viewing, describe_viewing

UPPER = """
def process(record, ctx):
    reply = ctx.service("upper").post("/", json={"name": record["name"]})
    return {"upper": reply["name"], "length": len(reply["name"])}
"""


class FakeService(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:
        name = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["name"]
        if name == "boom":
            self.send_response(502)
            self.end_headers()
            self.wfile.write(b"upstream failed")
            return
        body = json.dumps({"name": name.upper()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def fake_service() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeService)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()


@dataclass
class Live:
    client: httpx.AsyncClient
    settings: Settings
    workspace_id: str
    file_id: str

    async def run(self, **body: Any) -> dict[str, Any]:
        response = await self.client.post(
            f"/api/workspaces/{self.workspace_id}/runs", json={"file_id": self.file_id, **body}
        )
        assert response.status_code == 201, response.text
        return response.json()

    async def wait(self, run_id: str, timeout_s: float = 60) -> dict[str, Any]:
        run: dict[str, Any] = {}
        for _ in range(int(timeout_s / 0.1)):
            run = (
                await self.client.get(f"/api/workspaces/{self.workspace_id}/runs/{run_id}")
            ).json()
            if run["status"] in ("done", "failed", "cancelled"):
                return run
            await asyncio.sleep(0.1)
        raise AssertionError(f"run {run_id} did not finish: {run}")

    async def script(self, code: str) -> None:
        response = await self.client.post(
            f"/api/workspaces/{self.workspace_id}/scripts", json={"code": code}
        )
        assert response.status_code == 201, response.text


@pytest.fixture
async def live(tmp_path: Path, fake_service: str) -> AsyncIterator[Live]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    services = tmp_path / "services.toml"
    services.write_text(
        f'[upper]\nurl = "{fake_service}"\nretries = 2\nbackoff_s = 0\nretry_statuses = [502]\n',
        encoding="utf-8",
    )
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        data_dir=tmp_path / "data",
        model_provider="test",
        model_id="test",
        enable_test_model=True,
        public_url=f"http://127.0.0.1:{port}",
        local_services_file=services,
        run_poll_interval_s=0.1,
        log_level="WARNING",
    )
    server = uvicorn.Server(uvicorn.Config(create_app(settings), log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    for _ in range(500):  # uvicorn exposes no event for "started"
        if server.started:
            break
        await asyncio.sleep(0.02)
    async with httpx.AsyncClient(base_url=settings.public_url, timeout=30) as client:
        wid = (await client.post("/api/workspaces", json={"name": "Runs"})).json()["id"]
        names = [f"person {i}" for i in range(50)]
        names[17] = "boom"
        data = ("name,born\n" + "".join(f"{n},{1900 + i}\n" for i, n in enumerate(names))).encode()
        fid = (
            await client.post(
                f"/api/workspaces/{wid}/files", params={"name": "p.csv"}, content=data
            )
        ).json()["id"]
        yield Live(client=client, settings=settings, workspace_id=wid, file_id=fid)
    server.should_exit = True
    await task


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
