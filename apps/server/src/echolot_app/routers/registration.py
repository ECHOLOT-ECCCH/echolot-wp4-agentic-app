"""Asking for an account (docs/PLAN.md 7.12). With sign-in, the only /api routes that answer
without a session. Closed (404) unless ECHO_ALLOW_REGISTRATION is set."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from echolot_app import registration
from echolot_app.auth import AccountError, normalize_email
from echolot_app.deps import CurrentSettings, DbSession, State, client_address
from echolot_app.mail import send_all
from echolot_app.registration import AccountRequest, RegistrationClosed
from echolot_app.schemas import (
    ConfirmEmailIn,
    ConfirmEmailOut,
    RegisterIn,
    RegisterOut,
    RegistrationInfoOut,
)
from echolot_app.settings import Settings

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = structlog.get_logger(__name__)


def _require_open(settings: Settings) -> None:
    if not settings.allow_registration:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This server takes no requests for accounts")


@router.get("/registration", response_model=RegistrationInfoOut)
async def registration_info(settings: CurrentSettings) -> RegistrationInfoOut:
    """Whether the sign-in page offers "Request an account"."""
    return RegistrationInfoOut(
        enabled=settings.allow_registration, reviewer=settings.registration_reviewer
    )


@router.post("/register", response_model=RegisterOut, status_code=status.HTTP_202_ACCEPTED)
async def register(
    body: RegisterIn, request: Request, background: BackgroundTasks, db: DbSession, state: State
) -> RegisterOut:
    """Record a request and e-mail the link that confirms the address. The answer is the same
    for an address that has an account already: the e-mail tells its owner."""
    settings = state.settings
    _require_open(settings)
    try:
        email = normalize_email(body.email)
    except AccountError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    keys = (f"email:{email}", f"ip:{client_address(request)}")
    limiter = state.registration_limiter
    wait = limiter.retry_after(*keys)
    if wait:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many requests for an account. Try again later.",
            headers={"Retry-After": str(wait)},
        )
    limiter.failed(*keys)  # every attempt counts
    if body.website:
        # the hidden field is filled in: a robot; it gets the usual answer and nothing happens
        log.info("registration.ignored", client=keys[1])
        return RegisterOut()
    try:
        mails = await registration.register(
            db,
            settings,
            AccountRequest(
                email=email,
                name=body.name,
                organisation=body.organisation,
                purpose=body.purpose,
                password=body.password,
            ),
        )
    except RegistrationClosed as exc:
        log.warning("registration.full", limit=settings.registration_max_pending)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "No new requests are taken at the moment. Try again later.",
        ) from exc
    except AccountError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    # sent after the answer (and after the commit), so a slow mail server delays nobody
    background.add_task(send_all, state.mailer, mails)
    log.info("registration.requested", client=keys[1])
    return RegisterOut()


@router.post("/confirm-email", response_model=ConfirmEmailOut)
async def confirm_email(
    body: ConfirmEmailIn, background: BackgroundTasks, db: DbSession, state: State
) -> ConfirmEmailOut:
    """The link from the e-mail: the address is confirmed and the admins are told."""
    _require_open(state.settings)
    try:
        mails = await registration.confirm(db, state.settings, body.token)
    except AccountError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    background.add_task(send_all, state.mailer, mails)
    log.info("registration.confirmed", admins_told=len(mails))
    return ConfirmEmailOut()
