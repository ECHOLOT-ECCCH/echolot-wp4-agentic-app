"""Bring your own key: vendors, stored keys, the model choice, catalogs (docs/PLAN.md 7.11)."""

from __future__ import annotations

import asyncio
import stat
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from pydantic import SecretStr
from pydantic_ai.models.anthropic import AnthropicModel
from sqlalchemy import select

from echolot_app.agent.catalog import ModelCatalog
from echolot_app.agent.probe import safe_error
from echolot_app.crypto import SecretBox, SecretBoxError, key_hint
from echolot_app.db import AgentRun, ProviderKey, User
from echolot_app.state import AppState
from echolot_app.user_models import ModelNotReady, model_for_user

from .conftest import Workspace, add_user, api_client, sign_in
from .test_chat import chat_body, new_conversation, user_message

SECRET = "sk-ant-api03-THISISNOTAREALKEY-abcd1234"


async def test_vendors_are_listed_with_the_users_key_state(client: httpx.AsyncClient) -> None:
    vendors = {v["name"]: v for v in (await client.get("/api/vendors")).json()}
    assert list(vendors) == [
        "anthropic",
        "openai",
        "google",
        "mistral",
        "openrouter",
        "deepseek",
        "test",  # the test settings enable it
    ]
    assert "openai_compatible" not in vendors  # off unless ECHO_ALLOW_CUSTOM_ENDPOINTS
    anthropic = vendors["anthropic"]
    assert anthropic["needs_key"] and not anthropic["has_key"]
    assert anthropic["key_url"].startswith("https://")
    assert anthropic["suggested"][0]["id"] == "claude-sonnet-5-5"


async def test_a_saved_key_is_encrypted_and_never_returned(
    client: httpx.AsyncClient, state: AppState
) -> None:
    missing = await client.put("/api/me/keys/anthropic", json={})
    assert missing.status_code == 422
    saved = await client.put("/api/me/keys/anthropic", json={"api_key": f"  {SECRET} "})
    assert saved.status_code == 200
    assert SECRET not in saved.text
    assert saved.json()["has_key"] and saved.json()["key_hint"] == "…1234"
    listed = await client.get("/api/vendors")
    assert SECRET not in listed.text
    async with state.db.session() as db:
        [row] = (await db.execute(select(ProviderKey))).scalars()
    assert SECRET not in row.encrypted_key
    assert state.secret_box.decrypt(row.encrypted_key) == SECRET

    # an empty key keeps the stored one
    again = await client.put("/api/me/keys/anthropic", json={"api_key": ""})
    assert again.status_code == 200 and again.json()["key_hint"] == "…1234"
    assert (await client.delete("/api/me/keys/anthropic")).status_code == 204
    assert (await client.delete("/api/me/keys/anthropic")).status_code == 404
    assert (await client.put("/api/me/keys/nonesuch", json={"api_key": "x"})).status_code == 404


async def test_keys_are_private_to_their_owner(
    client: httpx.AsyncClient, app: FastAPI, state: AppState
) -> None:
    await client.put("/api/me/keys/openai", json={"api_key": "sk-proj-0123456789abcdef"})
    await add_user(state, "bob@example.org")
    async with api_client(app) as bob:
        await sign_in(bob, "bob@example.org")
        vendors = {v["name"]: v for v in (await bob.get("/api/vendors")).json()}
        assert not vendors["openai"]["has_key"]
        assert (await bob.delete("/api/me/keys/openai")).status_code == 404


async def test_custom_endpoints_only_when_allowed(
    client: httpx.AsyncClient, state: AppState
) -> None:
    body = {"base_url": "http://10.0.0.5:8000/v1"}
    assert (await client.put("/api/me/keys/openai_compatible", json=body)).status_code == 404
    state.settings.allow_custom_endpoints = True
    try:
        no_url = await client.put("/api/me/keys/openai_compatible", json={"api_key": "x"})
        assert no_url.status_code == 422
        saved = await client.put("/api/me/keys/openai_compatible", json=body)
        assert saved.status_code == 200
        assert saved.json()["base_url"] == "http://10.0.0.5:8000/v1"
        assert saved.json()["has_key"] and saved.json()["key_hint"] == ""
    finally:
        state.settings.allow_custom_endpoints = False


async def test_the_model_choice_and_its_readiness(client: httpx.AsyncClient) -> None:
    default = (await client.get("/api/me/model")).json()
    assert default["source"] == "server" and default["ready"]
    assert default["server_model"] == {"provider": "test", "model_id": "test"}

    chosen = await client.put(
        "/api/me/model", json={"provider": "anthropic", "model_id": "claude-opus-5-5"}
    )
    assert chosen.status_code == 200
    body = chosen.json()
    assert (body["provider"], body["model_id"], body["source"]) == (
        "anthropic",
        "claude-opus-5-5",
        "own",
    )
    assert not body["ready"] and "Add your Anthropic (Claude) key" in body["problem"]

    await client.put("/api/me/keys/anthropic", json={"api_key": SECRET})
    assert (await client.get("/api/me/model")).json()["ready"]

    no_model = await client.put("/api/me/model", json={"provider": "anthropic", "model_id": " "})
    assert no_model.status_code == 422
    back = await client.put("/api/me/model", json={"provider": None})
    assert back.json()["source"] == "server"


async def test_the_users_key_drives_the_model_and_the_servers_key_never_stands_in(
    client: httpx.AsyncClient, state: AppState
) -> None:
    await client.put("/api/me/model", json={"provider": "anthropic", "model_id": "claude-x"})
    settings = state.settings.model_copy(
        update={"model_provider": "anthropic", "model_api_key": SecretStr("sk-ant-SERVERKEY")}
    )
    async with state.db.session() as db:
        user = (await db.execute(select(User))).scalar_one()
        with pytest.raises(ModelNotReady):
            await model_for_user(db, user, settings, state.secret_box)
    await client.put("/api/me/keys/anthropic", json={"api_key": SECRET})
    async with state.db.session() as db:
        user = (await db.execute(select(User))).scalar_one()
        chosen, model = await model_for_user(db, user, settings, state.secret_box)
        await db.commit()
        assert chosen.label() == {"provider": "anthropic", "model_id": "claude-x", "key": "own"}
        assert isinstance(model, AnthropicModel)
        assert model.client.api_key == SECRET  # type: ignore[attr-defined]
        key = (await db.execute(select(ProviderKey))).scalar_one()
        assert key.last_used_at is not None


async def test_a_chat_turn_runs_on_the_users_own_choice(
    client: httpx.AsyncClient, workspace: Workspace, state: AppState
) -> None:
    await client.put("/api/me/keys/test", json={})
    await client.put("/api/me/model", json={"provider": "test", "model_id": "test"})
    cid = await new_conversation(client, workspace)
    response = await client.post(
        f"/api/workspaces/{workspace.id}/conversations/{cid}/chat",
        json=chat_body(user_message("hello")),
    )
    assert response.status_code == 200, response.text
    run: AgentRun | None = None
    for _ in range(50):
        async with state.db.session() as db:
            run = (await db.execute(select(AgentRun))).scalar_one()
        if run.status == "done":
            break
        await asyncio.sleep(0.05)
    assert run is not None
    assert run.model == {"provider": "test", "model_id": "test", "key": "own"}


async def test_listing_and_testing_a_vendor(client: httpx.AsyncClient) -> None:
    without_key = (await client.get("/api/vendors/anthropic/models")).json()
    assert without_key["models"][0]["id"] == "claude-sonnet-5-5"
    assert "Add your Anthropic (Claude) key" in without_key["error"]
    await client.put("/api/me/keys/test", json={})
    listing = (await client.get("/api/vendors/test/models")).json()
    assert listing == {
        "models": [{"id": "test", "label": "Test model (canned answers)", "suggested": True}],
        "error": None,
    }
    probe = await client.post("/api/me/keys/test/test", json={})
    assert probe.status_code == 200 and probe.json()["ok"], probe.text
    no_key = await client.post("/api/me/keys/openai/test", json={})
    assert no_key.status_code == 409


# --- catalogs ----------------------------------------------------------------------------------


def _catalog(handler: httpx.MockTransport) -> ModelCatalog:
    return ModelCatalog(httpx.AsyncClient(transport=handler))


async def test_catalog_puts_the_suggested_models_first_and_drops_non_chat_ones() -> None:
    seen: list[httpx.Request] = []

    def openai(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        ids = ["text-embedding-3-large", "gpt-5", "whisper-1", "a-new-model", "gpt-5-mini"]
        return httpx.Response(200, json={"data": [{"id": i} for i in ids]})

    catalog = _catalog(httpx.MockTransport(openai))
    listing = await catalog.list_models("openai", "sk-1")
    assert [m.id for m in listing.models] == ["gpt-5", "gpt-5-mini", "a-new-model"]
    assert [m.suggested for m in listing.models] == [True, True, False]
    assert seen[0].headers["authorization"] == "Bearer sk-1"
    await catalog.list_models("openai", "sk-1")
    assert len(seen) == 1  # cached
    await catalog.list_models("openai", "sk-2")
    assert len(seen) == 2  # per key


async def test_catalog_failure_falls_back_to_the_suggestions() -> None:
    catalog = _catalog(httpx.MockTransport(lambda r: httpx.Response(401)))
    listing = await catalog.list_models("anthropic", "bad")
    assert listing.error == "the vendor rejected the key"
    assert [m.id for m in listing.models][:2] == ["claude-sonnet-5-5", "claude-opus-5-5"]

    def anthropic(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "good"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "claude-haiku-5-5", "display_name": "Claude Haiku 5.5"},
                    {"id": "claude-sonnet-5-5", "display_name": "Claude Sonnet 5.5"},
                ]
            },
        )

    listing = await _catalog(httpx.MockTransport(anthropic)).list_models("anthropic", "good")
    assert listing.error is None
    assert [m.id for m in listing.models] == ["claude-sonnet-5-5", "claude-haiku-5-5"]


def test_errors_shown_to_users_mask_keys() -> None:
    error = RuntimeError("401 for key sk-ant-api03-abcdefgh and Bearer sk-or-v1-zzzzzzzz")
    text = safe_error(error)
    assert "abcdefgh" not in text and "zzzzzzzz" not in text
    assert text.startswith("RuntimeError: 401 for key ***")


# --- encryption at rest ------------------------------------------------------------------------


def test_secret_box_creates_its_key_file_privately_and_rotates(tmp_path: Path) -> None:
    key_file = tmp_path / "data" / "secret.key"
    box = SecretBox.from_setting(None, key_file)
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    token = box.encrypt("sk-secret")
    assert SecretBox.from_setting(None, key_file).decrypt(token) == "sk-secret"

    old = key_file.read_text().strip()
    new = Fernet.generate_key().decode()
    rotated = SecretBox.from_setting(f"{new},{old}", tmp_path / "unused")
    assert rotated.decrypt(token) == "sk-secret"
    assert SecretBox([new]).decrypt(rotated.rotate(token)) == "sk-secret"
    with pytest.raises(SecretBoxError):
        SecretBox([new]).decrypt(token)
    with pytest.raises(SecretBoxError):
        SecretBox(["not a key"])


def test_key_hints_show_only_the_end() -> None:
    assert key_hint("sk-0123456789") == "…6789"
    assert key_hint("short") == "…"
