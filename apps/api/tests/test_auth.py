from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from elexifier_api.deps import CurrentUser
from elexifier_api.state import AppState
from elexifier_core.db.models import AuditLog, Session, User

from .conftest import ISSUER, FakeIdp, LoggedIn


async def test_me_requires_session(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/me")
    assert resp.status_code == 401


async def test_me_returns_profile(client: httpx.AsyncClient, logged_in: LoggedIn) -> None:
    logged_in.apply(client)
    resp = await client.get("/api/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == logged_in.user.email
    assert body["role"] == "user"
    assert body["id"] == str(logged_in.user.id)


async def test_login_redirects_to_idp_with_pkce(client: httpx.AsyncClient) -> None:
    resp = await client.get("/auth/login", params={"next": "/projects/1"})
    assert resp.status_code == 302
    location = urlparse(resp.headers["location"])
    assert location.geturl().startswith(f"{ISSUER}/protocol/openid-connect/auth?")
    params = parse_qs(location.query)
    assert params["response_type"] == ["code"]
    assert params["client_id"] == ["elexifier-web"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["redirect_uri"] == ["http://testserver/auth/callback"]
    assert "state" in params and "nonce" in params
    assert "elx_oidc" in resp.cookies
    assert "HttpOnly" in resp.headers["set-cookie"]


async def _start_login(client: httpx.AsyncClient, next_path: str = "/") -> tuple[str, str, str]:
    """Start the login and return (state, nonce, signed oidc cookie); leaves the jar empty."""
    client.cookies.clear()
    resp = await client.get("/auth/login", params={"next": next_path})
    params = parse_qs(urlparse(resp.headers["location"]).query)
    oidc_cookie = resp.cookies["elx_oidc"]
    client.cookies.clear()
    return params["state"][0], params["nonce"][0], oidc_cookie


async def _finish_login(
    client: httpx.AsyncClient, state: str, oidc_cookie: str, code: str = "good-code"
) -> httpx.Response:
    client.cookies.clear()
    client.cookies.set("elx_oidc", oidc_cookie)
    resp = await client.get("/auth/callback", params={"code": code, "state": state})
    client.cookies.clear()
    return resp


async def test_callback_creates_user_and_session(
    client: httpx.AsyncClient, fake_idp: FakeIdp, app_state: AppState
) -> None:
    state, nonce, oidc_cookie = await _start_login(client, "/projects/42")
    fake_idp.next_nonce = nonce
    fake_idp.next_claims = {}
    resp = await _finish_login(client, state, oidc_cookie)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/projects/42"
    assert "elx_session" in resp.cookies and "elx_csrf" in resp.cookies
    # PKCE verifier and client secret were sent to the token endpoint
    last = fake_idp.token_requests[-1]
    assert last["grant_type"] == "authorization_code"
    assert last["code_verifier"] and last["client_secret"] == "test-secret"

    client.cookies.set("elx_session", resp.cookies["elx_session"])
    me = await client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["email"] == "dev@example.com"
    assert me.json()["role"] == "user"

    async with app_state.db.session() as db:
        users = (await db.execute(select(User))).scalars().all()
        assert len(users) == 1 and users[0].oidc_sub == "sub-dev"
        actions = {a.action for a in (await db.execute(select(AuditLog))).scalars().all()}
        assert {"user.created", "user.login"} <= actions


async def test_callback_maps_admin_role_and_updates_existing_user(
    client: httpx.AsyncClient, fake_idp: FakeIdp
) -> None:
    for roles, expected in ((["user"], "user"), (["user", "admin"], "admin")):
        state, nonce, oidc_cookie = await _start_login(client)
        fake_idp.next_nonce = nonce
        fake_idp.next_claims = {"realm_access": {"roles": roles}, "name": f"Name {expected}"}
        resp = await _finish_login(client, state, oidc_cookie)
        assert resp.status_code == 303
        client.cookies.set("elx_session", resp.cookies["elx_session"])
        me = await client.get("/api/me")
        assert me.json()["role"] == expected
        assert me.json()["name"] == f"Name {expected}"


async def test_callback_rejects_bad_state_and_bad_code(
    client: httpx.AsyncClient, fake_idp: FakeIdp
) -> None:
    state, nonce, oidc_cookie = await _start_login(client)
    fake_idp.next_nonce = nonce
    bad_state = await _finish_login(client, "tampered", oidc_cookie)
    assert bad_state.status_code == 400
    no_cookie = await client.get("/auth/callback", params={"code": "good-code", "state": state})
    assert no_cookie.status_code == 400
    bad_code = await _finish_login(client, state, oidc_cookie, code="wrong")
    assert bad_code.status_code == 401


async def test_callback_rejects_wrong_nonce_and_audience(
    client: httpx.AsyncClient, fake_idp: FakeIdp
) -> None:
    state, _nonce, oidc_cookie = await _start_login(client)
    fake_idp.next_nonce = "not-the-nonce"
    resp = await _finish_login(client, state, oidc_cookie)
    assert resp.status_code == 401

    state, nonce, oidc_cookie = await _start_login(client)
    fake_idp.next_nonce = nonce
    fake_idp.next_claims = {"aud": "some-other-client"}
    resp = await _finish_login(client, state, oidc_cookie)
    assert resp.status_code == 401
    fake_idp.next_claims = {}


async def test_csrf_double_submit(
    app: FastAPI, client: httpx.AsyncClient, logged_in: LoggedIn
) -> None:
    @app.post("/api/_probe")
    async def probe(user: CurrentUser) -> dict[str, str]:
        return {"user": str(user.id)}

    logged_in.apply(client, csrf_header=False)
    missing = await client.post("/api/_probe")
    assert missing.status_code == 403
    wrong = await client.post("/api/_probe", headers={"X-CSRF-Token": "nope"})
    assert wrong.status_code == 403
    ok = await client.post("/api/_probe", headers=logged_in.headers)
    assert ok.status_code == 200
    assert ok.json() == {"user": str(logged_in.user.id)}
    # GET never needs the header
    get = await client.get("/api/me")
    assert get.status_code == 200


async def test_expired_session_is_rejected(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    async with app_state.db.session() as db:
        row = (await db.execute(select(Session))).scalar_one()
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await db.commit()
    logged_in.apply(client)
    resp = await client.get("/api/me")
    assert resp.status_code == 401


async def test_logout_clears_session(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    logged_in.apply(client)
    resp = await client.post("/auth/logout")
    assert resp.status_code == 204  # no id_token stored for a directly created session
    cookie_header = " ".join(resp.headers.get_list("set-cookie"))
    assert "elx_session=" in cookie_header and "elx_csrf=" in cookie_header
    logged_in.apply(client)
    after = await client.get("/api/me")
    assert after.status_code == 401
    async with app_state.db.session() as db:
        assert (await db.execute(select(Session))).scalars().all() == []


async def test_logout_redirects_to_idp_when_id_token_known(
    client: httpx.AsyncClient, fake_idp: FakeIdp
) -> None:
    state, nonce, oidc_cookie = await _start_login(client)
    fake_idp.next_nonce = nonce
    login = await _finish_login(client, state, oidc_cookie)
    client.cookies.set("elx_session", login.cookies["elx_session"])
    client.cookies.set("elx_csrf", login.cookies["elx_csrf"])
    resp = await client.post("/auth/logout")
    assert resp.status_code == 303
    location = urlparse(resp.headers["location"])
    assert location.path.endswith("/logout")
    params = parse_qs(location.query)
    assert params["client_id"] == ["elexifier-web"]
    assert params["post_logout_redirect_uri"] == ["http://testserver"]
    assert "id_token_hint" in params
