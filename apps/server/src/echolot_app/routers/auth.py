"""Sign in, sign out, and the signed-in user's own account (docs/PLAN.md 7.11)."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status

from echolot_app.auth import (
    SESSION_COOKIE,
    AccountError,
    check_password,
    create_session,
    end_session,
    find_user,
    set_password,
)
from echolot_app.deps import CurrentSettings, CurrentUser, DbSession, State
from echolot_app.schemas import LoginIn, PasswordChange, UserOut
from echolot_app.settings import Settings

router = APIRouter(prefix="/api", tags=["auth"])
log = structlog.get_logger(__name__)


def _set_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )


def _client(request: Request) -> str:
    # behind a proxy, uvicorn's --proxy-headers puts the forwarded address here
    return request.client.host if request.client else "unknown"


@router.post("/auth/login", response_model=UserOut)
async def login(
    body: LoginIn, request: Request, response: Response, db: DbSession, state: State
) -> UserOut:
    email = body.email.strip().lower()
    keys = (f"email:{email}", f"ip:{_client(request)}")
    limiter = state.login_limiter
    wait = limiter.retry_after(*keys)
    if wait:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many failed sign-ins. Try again later.",
            headers={"Retry-After": str(wait)},
        )
    user = await find_user(db, email)
    ok = await check_password(body.password, user.password_hash if user else None)
    if user is None or not ok or not user.is_active:
        limiter.failed(*keys)
        log.info("auth.login_failed", client=keys[1])
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong e-mail address or password")
    limiter.succeeded(keys[0])
    token = await create_session(db, user, state.settings.session_days)
    _set_cookie(response, token, state.settings)
    log.info("auth.login", user_id=str(user.id))
    return UserOut.model_validate(user)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, db: DbSession, settings: CurrentSettings) -> Response:
    await end_session(db, request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=settings.secure_cookies, httponly=True, samesite="lax"
    )
    return response


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChange, db: DbSession, user: CurrentUser, settings: CurrentSettings
) -> Response:
    """Change one's own password; every session ends, this browser gets a new one."""
    if not await check_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The current password is wrong")
    try:
        await set_password(db, user, body.new_password)
    except AccountError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _set_cookie(response, await create_session(db, user, settings.session_days), settings)
    log.info("auth.password_changed", user_id=str(user.id))
    return response
