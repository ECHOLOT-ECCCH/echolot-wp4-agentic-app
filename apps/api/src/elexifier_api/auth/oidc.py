"""OpenID Connect relying party against Keycloak (or any OIDC provider).

Authorization code flow with PKCE. Discovery metadata and JWKS are fetched lazily and cached.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import urlencode

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet, KeySetSerialization

METADATA_TTL_S = 3600
JWKS_TTL_S = 3600


class OidcError(Exception):
    pass


@dataclass
class OidcIdentity:
    sub: str
    email: str
    name: str
    roles: list[str] = field(default_factory=list)
    id_token: str = ""
    claims: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuthorizationRequest:
    url: str
    state: str
    nonce: str
    code_verifier: str


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class OidcClient:
    def __init__(
        self,
        issuer: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        http: httpx.AsyncClient | None = None,
        scope: str = "openid email profile",
        metadata: dict[str, Any] | None = None,
        jwks: dict[str, Any] | None = None,
        leeway_s: int = 60,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scope = scope
        self._http = http or httpx.AsyncClient(timeout=10)
        self._metadata = metadata
        self._metadata_at = time.monotonic() if metadata else 0.0
        self._jwks = jwks
        self._jwks_at = time.monotonic() if jwks else 0.0
        self._leeway = leeway_s
        self._algorithms = ["RS256", "ES256", "PS256"]

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- discovery -----------------------------------------------------------------------

    async def metadata(self) -> dict[str, Any]:
        if self._metadata is None or time.monotonic() - self._metadata_at > METADATA_TTL_S:
            url = f"{self.issuer}/.well-known/openid-configuration"
            try:
                resp = await self._http.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise OidcError(f"cannot fetch OIDC discovery document from {url}: {exc}") from exc
            self._metadata = resp.json()
            self._metadata_at = time.monotonic()
        return self._metadata

    async def jwks(self, force: bool = False) -> dict[str, Any]:
        if force or self._jwks is None or time.monotonic() - self._jwks_at > JWKS_TTL_S:
            meta = await self.metadata()
            try:
                resp = await self._http.get(meta["jwks_uri"])
                resp.raise_for_status()
            except (httpx.HTTPError, KeyError) as exc:
                raise OidcError(f"cannot fetch JWKS: {exc}") from exc
            self._jwks = resp.json()
            self._jwks_at = time.monotonic()
        return self._jwks

    # --- authorization code flow -------------------------------------------------------------

    async def authorization_request(self) -> AuthorizationRequest:
        meta = await self.metadata()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        challenge = _b64url(hashlib.sha256(code_verifier.encode("ascii")).digest())
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "scope": self.scope,
            "redirect_uri": self.redirect_uri,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        url = f"{meta['authorization_endpoint']}?{urlencode(params)}"
        return AuthorizationRequest(url=url, state=state, nonce=nonce, code_verifier=code_verifier)

    async def exchange_code(self, code: str, code_verifier: str, nonce: str) -> OidcIdentity:
        meta = await self.metadata()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code_verifier": code_verifier,
        }
        try:
            resp = await self._http.post(meta["token_endpoint"], data=data)
        except httpx.HTTPError as exc:
            raise OidcError(f"token endpoint unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise OidcError(f"token exchange failed ({resp.status_code}): {resp.text[:300]}")
        tokens = resp.json()
        id_token = tokens.get("id_token")
        if not id_token:
            raise OidcError("token response has no id_token")
        claims = await self.verify_id_token(id_token, nonce)
        roles = _roles_from_claims(claims)
        if not roles and tokens.get("access_token"):
            # Fall back to the access token, where Keycloak always puts realm roles.
            try:
                access_claims = await self._decode(tokens["access_token"], verify_aud=False)
                roles = _roles_from_claims(access_claims)
            except OidcError:
                roles = []
        return OidcIdentity(
            sub=str(claims["sub"]),
            email=str(claims.get("email") or claims.get("preferred_username") or ""),
            name=str(claims.get("name") or claims.get("preferred_username") or ""),
            roles=roles,
            id_token=id_token,
            claims=dict(claims),
        )

    async def verify_id_token(self, id_token: str, nonce: str) -> dict[str, Any]:
        claims = await self._decode(id_token, verify_aud=True)
        if claims.get("nonce") != nonce:
            raise OidcError("id_token nonce mismatch")
        return claims

    async def _decode(self, token: str, verify_aud: bool) -> dict[str, Any]:
        options: dict[str, Any] = {
            "iss": {"essential": True, "value": self.issuer},
            "exp": {"essential": True},
            "sub": {"essential": True},
        }
        if verify_aud:
            options["aud"] = {"essential": True, "value": self.client_id}
        registry = jwt.JWTClaimsRegistry(leeway=self._leeway, **options)
        for attempt in (0, 1):
            jwks = cast(KeySetSerialization, await self.jwks(force=attempt == 1))
            key_set = KeySet.import_key_set(jwks)
            try:
                decoded = jwt.decode(token, key_set, algorithms=self._algorithms)
                registry.validate(decoded.claims)
                return dict(decoded.claims)
            except (JoseError, ValueError) as exc:
                message = str(exc).lower()
                if attempt == 0 and ("kid" in message or "key" in message):
                    continue  # key rotation: refresh JWKS once
                raise OidcError(f"invalid token: {exc}") from exc
        raise OidcError("invalid token: no matching key")

    async def end_session_url(
        self, id_token: str | None, post_logout_redirect_uri: str
    ) -> str | None:
        meta = await self.metadata()
        endpoint = meta.get("end_session_endpoint")
        if not endpoint:
            return None
        params = {"post_logout_redirect_uri": post_logout_redirect_uri, "client_id": self.client_id}
        if id_token:
            params["id_token_hint"] = id_token
        return f"{endpoint}?{urlencode(params)}"


def _roles_from_claims(claims: dict[str, Any]) -> list[str]:
    realm_access = claims.get("realm_access")
    if isinstance(realm_access, dict):
        roles = realm_access.get("roles")
        if isinstance(roles, list):
            return [str(r) for r in roles]
    roles = claims.get("roles")
    if isinstance(roles, list):
        return [str(r) for r in roles]
    return []
