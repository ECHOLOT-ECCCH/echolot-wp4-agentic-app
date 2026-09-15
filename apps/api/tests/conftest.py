"""API test fixtures: migrated test database, fake identity provider, ASGI client."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from joserfc import jwt
from joserfc.jwk import RSAKey

from elexifier_api.auth.oidc import OidcClient
from elexifier_api.main import build_state, create_app
from elexifier_api.state import AppState
from elexifier_core.db.models import User, UserRole
from elexifier_core.settings import Settings, reset_settings_cache
from elexifier_core.testing import (
    TEST_SECRET_KEY,
    TEST_SESSION_SECRET,
    migrate,
    test_database_url_or_skip,
    truncate_all,
)

ISSUER = "http://idp.test/realms/elexifier"
CLIENT_ID = "elexifier-web"
PUBLIC_URL = "http://testserver"


@pytest.fixture(scope="session")
def test_db_url() -> str:
    url = test_database_url_or_skip()
    migrate(url)
    return url


@pytest.fixture(autouse=True)
def _clean_db(test_db_url: str) -> None:
    truncate_all(test_db_url)


@pytest.fixture
def settings(test_db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("ELX_SECRET_KEY", TEST_SECRET_KEY)
    monkeypatch.setenv("ELX_SESSION_SECRET", TEST_SESSION_SECRET)
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("PUBLIC_URL", PUBLIC_URL)
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("OIDC_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("ELX_ENABLE_TEST_MODEL", "1")
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    return Settings()  # type: ignore[call-arg]


@dataclass
class FakeIdp:
    """Signs ID tokens like Keycloak and serves the token endpoint through a mock transport."""

    key: RSAKey
    jwks: dict[str, Any]
    next_nonce: str = ""
    next_claims: dict[str, Any] = field(default_factory=dict)
    token_requests: list[dict[str, str]] = field(default_factory=list)

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
            "token_endpoint": f"{ISSUER}/protocol/openid-connect/token",
            "end_session_endpoint": f"{ISSUER}/protocol/openid-connect/logout",
            "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs",
        }

    def sign(self, claims: dict[str, Any]) -> str:
        header = {"alg": "RS256", "kid": "test-key"}
        return jwt.encode(header, claims, self.key)

    def id_token(self, nonce: str, **overrides: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "sub-dev",
            "email": "dev@example.com",
            "name": "Dev Lexicographer",
            "realm_access": {"roles": ["user"]},
            "nonce": nonce,
            "iat": now,
            "exp": now + 300,
        }
        claims.update(overrides)
        return self.sign(claims)

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/certs"):
                return httpx.Response(200, json=self.jwks)
            if request.url.path.endswith("/token"):
                form = dict(httpx.QueryParams(request.content.decode()))
                self.token_requests.append(form)
                if form.get("code") != "good-code":
                    return httpx.Response(400, json={"error": "invalid_grant"})
                token = self.id_token(self.next_nonce, **self.next_claims)
                return httpx.Response(
                    200, json={"id_token": token, "access_token": token, "token_type": "Bearer"}
                )
            return httpx.Response(404)

        return httpx.MockTransport(handler)


@pytest.fixture(scope="session")
def fake_idp() -> FakeIdp:
    key = RSAKey.generate_key(2048, parameters={"kid": "test-key", "use": "sig"})
    return FakeIdp(key=key, jwks={"keys": [key.as_dict(private=False)]})


@pytest.fixture
def app_state(settings: Settings, fake_idp: FakeIdp) -> AppState:
    state = build_state(settings)
    state.oidc = OidcClient(
        issuer=ISSUER,
        client_id=CLIENT_ID,
        client_secret="test-secret",
        redirect_uri=f"{PUBLIC_URL}/auth/callback",
        http=httpx.AsyncClient(transport=fake_idp.transport()),
        metadata=fake_idp.metadata,
    )
    return state


@pytest.fixture
async def app(settings: Settings, app_state: AppState) -> AsyncIterator[FastAPI]:
    application = create_app(settings=settings, state=app_state)
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=PUBLIC_URL) as c:
        yield c


@dataclass
class LoggedIn:
    user: User
    session_id: str
    csrf: str

    @property
    def cookies(self) -> dict[str, str]:
        return {"elx_session": self.session_id, "elx_csrf": self.csrf}

    @property
    def headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf}

    def apply(self, client: httpx.AsyncClient, csrf_header: bool = True) -> None:
        """Attach this session to the client (cookie jar + CSRF header)."""
        client.cookies.clear()
        client.headers.pop("X-CSRF-Token", None)
        for name, value in self.cookies.items():
            client.cookies.set(name, value)
        if csrf_header:
            client.headers["X-CSRF-Token"] = self.csrf


async def make_logged_in(
    app_state: AppState, role: str = UserRole.USER.value, **user_fields: Any
) -> LoggedIn:
    """Create a user and a session directly in the database (no identity provider round trip)."""
    async with app_state.db.session() as db:
        user = User(
            oidc_sub=user_fields.pop("oidc_sub", f"sub-{uuid.uuid4()}"),
            email=user_fields.pop("email", f"{uuid.uuid4().hex[:8]}@example.com"),
            name=user_fields.pop("name", "Test User"),
            role=role,
        )
        db.add(user)
        await db.flush()
        session = await app_state.session_store.create(db, user)
        await db.commit()
        return LoggedIn(user=user, session_id=str(session.id), csrf=session.csrf_token)


@pytest.fixture
async def logged_in(app: FastAPI, app_state: AppState) -> LoggedIn:
    return await make_logged_in(app_state)


@pytest.fixture
async def admin(app: FastAPI, app_state: AppState) -> LoggedIn:
    return await make_logged_in(app_state, role=UserRole.ADMIN.value)
