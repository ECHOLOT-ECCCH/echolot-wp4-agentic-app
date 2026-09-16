"""The HTTP surface of the runner: token, contract validation, health, and a run through the
fake docker client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from elexifier_core.settings import get_settings
from elexifier_core.storage import LocalStorage
from elexifier_sandbox.runner import main

from .conftest import FakeContainer, FakeDocker


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Any):
    settings = get_settings().model_copy(
        update={"sandbox_runner_token": "secret", "sandbox_image": "img:test"}
    )
    monkeypatch.setattr(main, "runner_settings", lambda: settings)
    monkeypatch.setattr(main, "runner_storage", lambda: LocalStorage(tmp_path / "storage"))
    fake = FakeDocker(FakeContainer(options={}, stdout=b"42\n"))
    monkeypatch.setattr(main, "docker_client", lambda: fake)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://runner") as c:
        yield c


async def test_health_reports_daemon_and_image(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok" and body["docker"] == "ok" and body["image"] == "present"
    assert body["image_name"] == "img:test"


async def test_run_requires_the_token(client: httpx.AsyncClient) -> None:
    body = {"run_id": "r", "project_id": "p", "code": "print(1)"}
    assert (await client.post("/run", json=body)).status_code == 401
    assert (
        await client.post("/run", json=body, headers={"X-Runner-Token": "wrong"})
    ).status_code == 401


async def test_run_executes_through_the_client(client: httpx.AsyncClient) -> None:
    body = {"run_id": "r", "project_id": "p", "code": "print(42)"}
    resp = await client.post("/run", json=body, headers={"X-Runner-Token": "secret"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["stdout"] == "42\n" and resp.json()["exit_code"] == 0
    assert resp.json()["outputs"] == [] and resp.json()["killed"] is False


async def test_run_validates_contract(client: httpx.AsyncClient) -> None:
    bad = {
        "run_id": "r",
        "project_id": "p",
        "code": "x",
        "inputs": [{"s3_key": "k", "mount_path": "../etc"}],
    }
    resp = await client.post("/run", json=bad, headers={"X-Runner-Token": "secret"})
    assert resp.status_code == 422
    ruby = {"run_id": "r", "project_id": "p", "code": "x", "language": "ruby"}
    resp = await client.post("/run", json=ruby, headers={"X-Runner-Token": "secret"})
    assert resp.status_code == 422 and "unsupported language" in resp.text
