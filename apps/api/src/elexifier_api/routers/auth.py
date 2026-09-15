"""OIDC login, callback and logout (backend-for-frontend)."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from elexifier_api.auth.oidc import OidcError
from elexifier_api.auth.session import (
    OIDC_COOKIE,
    SESSION_COOKIE,
    CookieSpec,
    SessionStore,
    is_safe_next,
    now,
    role_from_oidc_roles,
)
from elexifier_api.deps import CurrentSessionStore, DbSession, State
from elexifier_core.db.models import AuditLog, User

router = APIRouter(prefix="/auth", tags=["auth"])
log = structlog.get_logger(__name__)


def set_cookie(response: Response, spec: CookieSpec) -> None:
    response.set_cookie(
        spec.name,
        spec.value,
        max_age=spec.max_age,
        httponly=spec.http_only,
        secure=spec.secure,
        samesite=spec.same_site,  # type: ignore[arg-type]
        path=spec.path,
    )


def clear_cookies(response: Response, store: SessionStore) -> None:
    for name in store.cleared_cookie_names():
        response.delete_cookie(name, path="/", secure=store.secure, samesite="lax")


@router.get("/login")
async def login(state: State, store: CurrentSessionStore, next: str = "/") -> RedirectResponse:
    target = next if is_safe_next(next) else "/"
    try:
        auth_request = await state.oidc.authorization_request()
    except OidcError as exc:
        log.error("oidc.discovery_failed", error=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Identity provider unavailable"
        ) from exc
    signed = store.sign_oidc_state(
        {
            "state": auth_request.state,
            "nonce": auth_request.nonce,
            "verifier": auth_request.code_verifier,
            "next": target,
        }
    )
    response = RedirectResponse(auth_request.url, status_code=status.HTTP_302_FOUND)
    set_cookie(response, store.oidc_cookie(signed))
    return response


@router.get("/callback")
async def callback(
    request: Request,
    state: State,
    db: DbSession,
    store: CurrentSessionStore,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    if error:
        log.warning("oidc.callback_error", error=error, description=error_description)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Login failed: {error}")
    signed = request.cookies.get(OIDC_COOKIE)
    pending = store.load_oidc_state(signed) if signed else None
    if not pending or not code or request.query_params.get("state") != pending.get("state"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Login state is missing or expired")
    try:
        identity = await state.oidc.exchange_code(code, pending["verifier"], pending["nonce"])
    except OidcError as exc:
        log.warning("oidc.exchange_failed", error=str(exc))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Login could not be completed") from exc

    user = (
        await db.execute(select(User).where(User.oidc_sub == identity.sub))
    ).scalar_one_or_none()
    role = role_from_oidc_roles(identity.roles)
    if user is None:
        user = User(oidc_sub=identity.sub, email=identity.email, name=identity.name, role=role)
        db.add(user)
        await db.flush()
        db.add(AuditLog(user_id=user.id, action="user.created", target=str(user.id)))
    else:
        user.email = identity.email or user.email
        user.name = identity.name or user.name
        user.role = role
    user.last_login_at = now()
    session = await store.create(
        db,
        user,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
        id_token=identity.id_token,
    )
    db.add(AuditLog(user_id=user.id, action="user.login", target=str(session.id)))

    response = RedirectResponse(pending.get("next") or "/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(OIDC_COOKIE, path="/")
    for spec in store.session_cookies(session):
        set_cookie(response, spec)
    return response


@router.post("/logout")
async def logout(
    request: Request, state: State, db: DbSession, store: CurrentSessionStore
) -> Response:
    loaded = await store.load(db, request.cookies.get(SESSION_COOKIE))
    id_token: str | None = None
    if loaded is not None:
        session, user = loaded
        id_token = session.id_token
        db.add(AuditLog(user_id=user.id, action="user.logout", target=str(session.id)))
        await store.delete(db, session)
    end_session = None
    try:
        end_session = await state.oidc.end_session_url(id_token, state.settings.public_url)
    except OidcError:
        end_session = None
    if end_session and id_token:
        response: Response = RedirectResponse(end_session, status_code=status.HTTP_303_SEE_OTHER)
    else:
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_cookies(response, store)
    return response
