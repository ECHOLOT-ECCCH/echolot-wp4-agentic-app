import httpx
import pytest

from elexifier_sandbox.runner import main


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main, "RUNNER_TOKEN", "secret")
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://runner") as c:
        yield c


async def test_health(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_run_requires_token_and_is_not_implemented_yet(client: httpx.AsyncClient) -> None:
    body = {"run_id": "r", "project_id": "p", "code": "print(1)"}
    assert (await client.post("/run", json=body)).status_code == 401
    assert (
        await client.post("/run", json=body, headers={"X-Runner-Token": "wrong"})
    ).status_code == 401
    resp = await client.post("/run", json=body, headers={"X-Runner-Token": "secret"})
    assert resp.status_code == 501


async def test_run_validates_contract(client: httpx.AsyncClient) -> None:
    bad = {
        "run_id": "r",
        "project_id": "p",
        "code": "x",
        "inputs": [{"s3_key": "k", "mount_path": "../etc"}],
    }
    resp = await client.post("/run", json=bad, headers={"X-Runner-Token": "secret"})
    assert resp.status_code == 422
