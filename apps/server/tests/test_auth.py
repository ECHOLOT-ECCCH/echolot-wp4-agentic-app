"""Accounts, sessions, the CSRF header and workspace ownership (docs/PLAN.md 7.11)."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from sqlalchemy import select, update

from echolot_app import auth
from echolot_app.auth import (
    LoginLimiter,
    create_user,
    hash_password,
    verify_password,
)
from echolot_app.cli import main as cli_main
from echolot_app.db import User, UserSession, Workspace, utcnow
from echolot_app.main import create_app
from echolot_app.settings import Settings
from echolot_app.state import AppState

from .conftest import PASSWORD, add_user, api_client, sign_in, upload

# --- passwords and the limiter -----------------------------------------------------------------


def test_password_hashes_verify_and_differ_per_salt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "_SCRYPT_N", 2**15)  # the production cost, once
    first, second = hash_password("a long passphrase"), hash_password("a long passphrase")
    assert first.startswith("scrypt$32768$8$3$")
    assert first != second
    assert verify_password("a long passphrase", first)
    assert not verify_password("a long passphrasE", first)
    assert not verify_password("x", "not-a-hash")


def test_login_limiter_blocks_after_the_failures_then_forgets() -> None:
    limiter = LoginLimiter(max_failures=2, window_s=0.2)
    limiter.failed("email:a", "ip:1")
    assert limiter.retry_after("email:a", "ip:1") == 0
    limiter.failed("email:a", "ip:1")
    assert limiter.retry_after("email:a") > 0
    assert limiter.retry_after("ip:1") > 0
    assert limiter.retry_after("email:b", "ip:2") == 0
    limiter.succeeded("email:a")
    assert limiter.retry_after("email:a") == 0


# --- signing in and out ------------------------------------------------------------------------


async def test_login_sets_a_strict_session_cookie(
    anonymous: httpx.AsyncClient, state: AppState
) -> None:
    await add_user(state, "ana@example.org")
    assert (await anonymous.get("/api/me")).status_code == 401
    response = await anonymous.post(
        "/api/auth/login", json={"email": " Ana@Example.org ", "password": PASSWORD}
    )
    assert response.status_code == 200
    assert response.json()["email"] == "ana@example.org"
    assert "password_hash" not in response.json()
    cookie = response.headers["set-cookie"].lower()
    assert "echolot_session=" in cookie
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert "secure" not in cookie  # public_url is http in tests
    me = await anonymous.get("/api/me")
    assert me.status_code == 200 and me.json()["email"] == "ana@example.org"
    async with state.db.session() as db:
        [stored] = (await db.execute(select(UserSession))).scalars()
    token = anonymous.cookies["echolot_session"]
    assert stored.token_hash != token and len(stored.token_hash) == 64

    assert (await anonymous.post("/api/auth/logout")).status_code == 204
    assert (await anonymous.get("/api/me")).status_code == 401
    # the old token is dead on the server, not just forgotten by the browser
    anonymous.cookies.set("echolot_session", token)
    assert (await anonymous.get("/api/me")).status_code == 401


async def test_cookie_is_secure_behind_https(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        data_dir=tmp_path / "data",
        public_url="https://workbench.example.org",
    )
    app = create_app(settings)
    async with LifespanManager(app):
        await add_user(app.state.app, "ana@example.org")
        async with api_client(app) as client:
            response = await client.post(
                "/api/auth/login", json={"email": "ana@example.org", "password": PASSWORD}
            )
    assert "secure" in response.headers["set-cookie"].lower()


async def test_wrong_password_unknown_user_and_disabled_user_look_the_same(
    anonymous: httpx.AsyncClient, state: AppState
) -> None:
    uid = await add_user(state, "ana@example.org")
    async with state.db.session() as db:
        await db.execute(update(User).where(User.id == uid).values(is_active=False))
        await db.commit()
    await add_user(state, "bob@example.org")
    for email, password in [
        ("bob@example.org", "wrong password!"),
        ("nobody@example.org", PASSWORD),
        ("ana@example.org", PASSWORD),
    ]:
        response = await anonymous.post(
            "/api/auth/login", json={"email": email, "password": password}
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Wrong e-mail address or password"


async def test_repeated_failures_are_throttled(
    anonymous: httpx.AsyncClient, state: AppState
) -> None:
    await add_user(state, "ana@example.org")
    state.login_limiter.max_failures = 3
    for _ in range(3):
        bad = {"email": "ana@example.org", "password": "nope nope nope"}
        assert (await anonymous.post("/api/auth/login", json=bad)).status_code == 401
    good = {"email": "ana@example.org", "password": PASSWORD}
    blocked = await anonymous.post("/api/auth/login", json=good)
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0


async def test_an_expired_session_is_refused(client: httpx.AsyncClient, state: AppState) -> None:
    assert (await client.get("/api/me")).status_code == 200
    async with state.db.session() as db:
        await db.execute(update(UserSession).values(expires_at=utcnow() - timedelta(seconds=1)))
        await db.commit()
    assert (await client.get("/api/me")).status_code == 401


async def test_disabling_a_user_ends_their_sessions(
    client: httpx.AsyncClient, app: FastAPI, state: AppState
) -> None:
    uid = await add_user(state, "bob@example.org")
    async with api_client(app) as bob:
        await sign_in(bob, "bob@example.org")
        assert (await bob.get("/api/me")).status_code == 200
        response = await client.patch(f"/api/admin/users/{uid}", json={"is_active": False})
        assert response.status_code == 200 and response.json()["is_active"] is False
        assert (await bob.get("/api/me")).status_code == 401


async def test_changing_the_password(client: httpx.AsyncClient, app: FastAPI) -> None:
    async with api_client(app) as other_browser:
        await sign_in(other_browser, "ana@example.org")
        wrong = await client.post(
            "/api/me/password",
            json={"current_password": "not it at all", "new_password": "a brand new secret"},
        )
        assert wrong.status_code == 400
        short = await client.post(
            "/api/me/password", json={"current_password": PASSWORD, "new_password": "short"}
        )
        assert short.status_code == 422
        assert "12 characters" in short.json()["detail"]
        ok = await client.post(
            "/api/me/password",
            json={"current_password": PASSWORD, "new_password": "a brand new secret"},
        )
        assert ok.status_code == 204
        # this browser carries on with a fresh session; the other one is signed out
        assert (await client.get("/api/me")).status_code == 200
        assert (await other_browser.get("/api/me")).status_code == 401
    async with api_client(app) as fresh:
        await sign_in(fresh, "ana@example.org", "a brand new secret")


# --- the CSRF header ---------------------------------------------------------------------------


async def test_unsafe_requests_without_the_header_are_refused(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    cookies = client.cookies
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver", cookies=cookies
    ) as bare:
        assert (await bare.get("/api/workspaces")).status_code == 200  # reads are fine
        refused = await bare.post("/api/workspaces", json={"name": "x"})
        assert refused.status_code == 403
        assert "X-Echolot-Request" in refused.json()["detail"]
        login = await bare.post(
            "/api/auth/login", json={"email": "ana@example.org", "password": PASSWORD}
        )
        assert login.status_code == 403
        assert (await bare.get("/healthz")).status_code == 200


# --- ownership ---------------------------------------------------------------------------------


def _workspace_routes(app: FastAPI) -> list[tuple[str, str]]:
    return [
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/workspaces/{workspace_id}")
        for method in operations
    ]


def _fill(path: str, workspace_id: str) -> str:
    any_id = str(uuid.uuid4())
    return (
        path.replace("{workspace_id}", workspace_id)
        .replace("{version}", "1")
        .replace("{row}", "0")
        .replace("{file_id}", any_id)
        .replace("{run_id}", any_id)
        .replace("{conversation_id}", any_id)
    )


async def test_every_workspace_route_is_closed_to_others_and_to_strangers(
    client: httpx.AsyncClient, app: FastAPI, anonymous: httpx.AsyncClient, state: AppState
) -> None:
    await add_user(state, "bob@example.org")
    async with api_client(app) as bob:
        await sign_in(bob, "bob@example.org")
        bobs = (await bob.post("/api/workspaces", json={"name": "Bob's"})).json()["id"]
    routes = _workspace_routes(app)
    assert len(routes) > 20, routes  # files, scripts, runs, chat, task, the workspace itself
    for method, path in routes:
        url = _fill(path, bobs)
        as_ana = await client.request(method, url, json={})
        assert as_ana.status_code == 404, (method, path, as_ana.status_code, as_ana.text)
        as_nobody = await anonymous.request(method, url, json={})
        assert as_nobody.status_code == 401, (method, path, as_nobody.status_code)
    assert [w["name"] for w in (await client.get("/api/workspaces")).json()] == []


async def test_own_workspace_with_someone_elses_ids_finds_nothing(
    client: httpx.AsyncClient, app: FastAPI, state: AppState
) -> None:
    await add_user(state, "bob@example.org")
    async with api_client(app) as bob:
        await sign_in(bob, "bob@example.org")
        bobs = (await bob.post("/api/workspaces", json={"name": "Bob's"})).json()["id"]
        fid = await upload(bob, uuid.UUID(bobs), "p.csv", b"name\nAna\n")
        cid = (await bob.post(f"/api/workspaces/{bobs}/conversations", json={})).json()["id"]
    mine = (await client.post("/api/workspaces", json={"name": "Mine"})).json()["id"]
    for url in (
        f"/api/workspaces/{mine}/files/{fid}",
        f"/api/workspaces/{mine}/files/{fid}/records",
        f"/api/workspaces/{mine}/files/{fid}/download",
        f"/api/workspaces/{mine}/conversations/{cid}/messages",
    ):
        assert (await client.get(url)).status_code == 404, url
    run = await client.post(f"/api/workspaces/{mine}/runs", json={"file_id": fid})
    assert run.status_code == 422 and "no file" in run.json()["detail"], run.text


async def test_services_need_a_sign_in(anonymous: httpx.AsyncClient) -> None:
    assert (await anonymous.get("/api/services")).status_code == 401


# --- admin -------------------------------------------------------------------------------------


async def test_admin_creates_and_resets_accounts(client: httpx.AsyncClient, app: FastAPI) -> None:
    created = await client.post(
        "/api/admin/users", json={"email": "Carla@Example.org", "name": "Carla"}
    )
    assert created.status_code == 201
    body = created.json()
    assert body["user"]["email"] == "carla@example.org" and not body["user"]["is_admin"]
    temporary = body["password"]
    assert len(temporary) >= 12
    duplicate = await client.post("/api/admin/users", json={"email": "carla@example.org"})
    assert duplicate.status_code == 422

    async with api_client(app) as carla:
        await sign_in(carla, "carla@example.org", temporary)
        assert (await carla.get("/api/admin/users")).status_code == 403
        assert (await carla.post("/api/admin/users", json={"email": "x@y.z"})).status_code == 403
        reset = await client.post(f"/api/admin/users/{body['user']['id']}/reset-password")
        assert reset.status_code == 200
        assert (await carla.get("/api/me")).status_code == 401
        await sign_in(carla, "carla@example.org", reset.json()["password"])

    emails = [u["email"] for u in (await client.get("/api/admin/users")).json()]
    assert emails == ["ana@example.org", "carla@example.org"]


async def test_the_last_admin_stays(client: httpx.AsyncClient, state: AppState) -> None:
    me = (await client.get("/api/me")).json()
    for change in ({"is_admin": False}, {"is_active": False}):
        response = await client.patch(f"/api/admin/users/{me['id']}", json=change)
        assert response.status_code == 409
    other = await add_user(state, "bob@example.org", admin=True)
    # even with a second admin, nobody demotes or disables themselves
    response = await client.patch(f"/api/admin/users/{me['id']}", json={"is_admin": False})
    assert response.status_code == 409
    response = await client.patch(f"/api/admin/users/{other}", json={"is_admin": False})
    assert response.status_code == 200 and response.json()["is_admin"] is False


# --- databases from before login ---------------------------------------------------------------


async def test_an_old_database_gains_owners_and_the_first_admin_adopts_its_workspaces(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    with sqlite3.connect(data / "app.db") as old:  # the M3 schema of workspaces
        old.execute(
            "CREATE TABLE workspaces (id CHAR(32) PRIMARY KEY, name VARCHAR(200) NOT NULL, "
            "description TEXT, task_md TEXT NOT NULL, created_at DATETIME NOT NULL, "
            "updated_at DATETIME NOT NULL)"
        )
        old.execute(
            "INSERT INTO workspaces VALUES (?, 'Old', NULL, '', '2026-10-27 10:00:00', "
            "'2026-10-27 10:00:00')",
            (uuid.uuid4().hex,),
        )
    settings = Settings(_env_file=None, data_dir=data)  # type: ignore[call-arg]
    app = create_app(settings)
    async with LifespanManager(app):
        state: AppState = app.state.app
        async with state.db.session() as db:
            await create_user(db, "plain@example.org", "", PASSWORD)
            await db.flush()
            [old_workspace] = (await db.execute(select(Workspace))).scalars()
            assert old_workspace.owner_id is None  # a plain user adopts nothing
            admin = await create_user(db, "boss@example.org", "", PASSWORD, is_admin=True)
            await db.commit()
        async with state.db.session() as db:
            [old_workspace] = (await db.execute(select(Workspace))).scalars()
            assert old_workspace.owner_id == admin.id


def test_the_command_line_manages_accounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from echolot_app.settings import get_settings

    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    try:
        assert cli_main(["create-user", "Ana@example.org", "--name", "Ana", "--admin"]) == 0
        out = capsys.readouterr().out
        assert "Created admin ana@example.org" in out
        temporary = out.split("Temporary password: ")[1].strip()
        assert len(temporary) >= 12
        assert cli_main(["create-user", "ana@example.org"]) == 1
        assert cli_main(["disable-user", "ana@example.org"]) == 0
        assert cli_main(["list-users"]) == 0
        assert "ana@example.org\tAna\tadmin,disabled" in capsys.readouterr().out
        assert cli_main(["set-password", "nobody@example.org"]) == 1
    finally:
        get_settings.cache_clear()
