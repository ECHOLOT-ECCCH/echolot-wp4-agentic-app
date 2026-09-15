from __future__ import annotations

import httpx
from sqlalchemy import select

from elexifier_agent.catalog import ModelCatalog
from elexifier_api.state import AppState
from elexifier_core.db.models import ProviderCredential, UsageRecord

from .conftest import LoggedIn


def _mock_catalog() -> ModelCatalog:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "openrouter.ai":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "anthropic/claude-sonnet-5",
                            "name": "Claude Sonnet 5",
                            "context_length": 200000,
                            "architecture": {"input_modalities": ["text", "image"]},
                            "supported_parameters": ["tools"],
                            "pricing": {"prompt": "0.000003", "completion": "0.000015"},
                        }
                    ]
                },
            )
        return httpx.Response(500)

    return ModelCatalog(http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_credential_lifecycle_and_encryption(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    app_state.catalog = _mock_catalog()
    logged_in.apply(client)

    providers = await client.get("/api/providers")
    names = {p["name"] for p in providers.json()}
    assert {"openrouter", "openai", "anthropic", "openai_compatible", "test"} <= names

    created = await client.post(
        "/api/me/credentials",
        json={
            "provider": "openrouter",
            "label": "My OpenRouter",
            "api_key": "sk-or-v1-abcdef123456",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["key_hint"] == "***3456"
    assert "api_key" not in body and "encrypted_key" not in body

    async with app_state.db.session() as db:
        row = (await db.execute(select(ProviderCredential))).scalar_one()
        assert row.encrypted_key != "sk-or-v1-abcdef123456"
        assert "sk-or" not in row.encrypted_key
        assert app_state.secret_box.decrypt(row.encrypted_key) == "sk-or-v1-abcdef123456"

    listed = await client.get("/api/me/credentials")
    assert [c["label"] for c in listed.json()] == ["My OpenRouter"]

    duplicate = await client.post(
        "/api/me/credentials",
        json={"provider": "openrouter", "label": "My OpenRouter", "api_key": "sk-or-other"},
    )
    assert duplicate.status_code == 409

    models = await client.get("/api/models", params={"credential_id": body["id"]})
    assert models.status_code == 200
    assert models.json()[0]["id"] == "anthropic/claude-sonnet-5"
    assert models.json()[0]["supports_vision"] is True

    deleted = await client.delete(f"/api/me/credentials/{body['id']}")
    assert deleted.status_code == 204
    assert (await client.get("/api/me/credentials")).json() == []
    assert (
        await client.get("/api/models", params={"credential_id": body["id"]})
    ).status_code == 404


async def test_credential_validation(client: httpx.AsyncClient, logged_in: LoggedIn) -> None:
    logged_in.apply(client)
    assert (
        await client.post(
            "/api/me/credentials", json={"provider": "nope", "label": "x", "api_key": "k"}
        )
    ).status_code == 422
    assert (
        await client.post("/api/me/credentials", json={"provider": "openai", "label": "x"})
    ).status_code == 422
    assert (
        await client.post(
            "/api/me/credentials", json={"provider": "openai_compatible", "label": "x"}
        )
    ).status_code == 422
    assert (
        await client.post(
            "/api/me/credentials",
            json={"provider": "openai_compatible", "label": "x", "base_url": "ftp://nope"},
        )
    ).status_code == 422
    ok = await client.post(
        "/api/me/credentials",
        json={
            "provider": "openai_compatible",
            "label": "local",
            "base_url": "http://localhost:11434/v1",
        },
    )
    assert ok.status_code == 201 and ok.json()["key_hint"] == ""


async def test_credentials_are_private(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    from .conftest import make_logged_in

    logged_in.apply(client)
    created = await client.post("/api/me/credentials", json={"provider": "test", "label": "t"})
    cid = created.json()["id"]
    other = await make_logged_in(app_state)
    other.apply(client)
    assert (await client.get("/api/me/credentials")).json() == []
    assert (await client.get("/api/models", params={"credential_id": cid})).status_code == 404
    assert (await client.delete(f"/api/me/credentials/{cid}")).status_code == 404
    assert (await client.post(f"/api/me/credentials/{cid}/test", json={})).status_code == 404


async def test_probe_with_test_provider_records_usage(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    logged_in.apply(client)
    created = await client.post("/api/me/credentials", json={"provider": "test", "label": "t"})
    cid = created.json()["id"]
    models = await client.get("/api/models", params={"credential_id": cid})
    assert models.json()[0]["id"] == "test"

    probe = await client.post(f"/api/me/credentials/{cid}/test", json={})
    assert probe.status_code == 200, probe.text
    assert probe.json()["ok"] is True
    assert probe.json()["model_id"] == "test"

    async with app_state.db.session() as db:
        usage = (await db.execute(select(UsageRecord))).scalar_one()
        assert usage.purpose == "probe" and usage.model == "test"
        credential = (await db.execute(select(ProviderCredential))).scalar_one()
        assert credential.last_used_at is not None
