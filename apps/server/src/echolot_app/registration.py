"""Requests for an account (docs/PLAN.md 7.12). A person asks for one, confirms their e-mail
address with a link, and an admin approves or rejects the request. Shared by the API and the
command line; each step returns the e-mails it calls for, and the caller sends them."""

from __future__ import annotations

import asyncio
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from echolot_app.auth import (
    AccountError,
    find_user,
    hash_password,
    normalize_email,
    token_hash,
    validate_password,
)
from echolot_app.db import EmailToken, User, UserStatus, utcnow
from echolot_app.mail import Mail
from echolot_app.settings import Settings

PRODUCT = "ECHOLOT Workbench"
CONFIRM = "confirm"  # EmailToken.purpose
CONFIRM_HOURS = 24
# unconfirmed requests are deleted after this, with what the person entered
UNCONFIRMED_DAYS = 2
MAX_NAME = 200
MAX_PURPOSE = 2000


class RegistrationClosed(Exception):
    """Too many requests are waiting; no new ones for now."""


class RequestStateError(AccountError):
    """The step does not fit where the request stands (approving an unconfirmed one, say)."""


@dataclass(frozen=True)
class AccountRequest:
    """What a person enters to ask for an account."""

    email: str
    name: str
    organisation: str
    purpose: str
    password: str


# --- what people type --------------------------------------------------------------------------


# bidirectional overrides and isolates: they can make text read differently from what it is
_BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩")


def _is_control(char: str) -> bool:
    return unicodedata.category(char) in ("Cc", "Zl", "Zp") or char in _BIDI_CONTROLS


def one_line(value: str, what: str, max_length: int = MAX_NAME) -> str:
    """A name or an organisation: one line of text. It goes into e-mail subjects."""
    value = " ".join(value.split())
    if not value:
        raise AccountError(f"Please fill in your {what}.")
    if any(_is_control(char) for char in value):
        raise AccountError(f"Your {what} cannot contain control characters.")
    if len(value) > max_length:
        raise AccountError(f"Your {what} can have at most {max_length} characters.")
    return value


def free_text(value: str, what: str, max_length: int = MAX_PURPOSE) -> str:
    """Several lines; control characters other than line breaks and tabs are dropped."""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(char for char in value if char in "\n\t" or not _is_control(char)).strip()
    if not value:
        raise AccountError(f"Please fill in {what}.")
    if len(value) > max_length:
        raise AccountError(f"{what.capitalize()} can have at most {max_length} characters.")
    return value


# --- the steps ---------------------------------------------------------------------------------


async def purge_unconfirmed(db: AsyncSession) -> None:
    """Delete requests whose address was never confirmed (their links go with them)."""
    cutoff = utcnow() - timedelta(days=UNCONFIRMED_DAYS)
    await db.execute(
        delete(User).where(User.status == UserStatus.UNCONFIRMED, User.created_at < cutoff)
    )


async def waiting_requests(db: AsyncSession) -> int:
    query = select(func.count()).select_from(User).where(User.status != UserStatus.APPROVED)
    return (await db.scalar(query)) or 0


async def register(db: AsyncSession, settings: Settings, request: AccountRequest) -> list[Mail]:
    """Record a request and e-mail the link that confirms the address.

    The caller answers the same whatever happens here, so nobody learns from it whether an
    address has an account; the e-mail says, and only its owner reads it."""
    email = normalize_email(request.email)
    name = one_line(request.name, "name")
    organisation = one_line(request.organisation, "organisation")
    purpose = free_text(request.purpose, "what you will use the workbench for")
    validate_password(request.password)
    # hashed every time, so an address with an account answers as slowly as a new one
    password_hash = await asyncio.to_thread(hash_password, request.password)
    await purge_unconfirmed(db)
    user = await find_user(db, email)
    if user is None:
        if await waiting_requests(db) >= settings.registration_max_pending:
            raise RegistrationClosed
        user = User(
            email=email,
            name=name,
            password_hash=password_hash,
            is_active=False,
            status=UserStatus.UNCONFIRMED,
            organisation=organisation,
            purpose=purpose,
        )
        db.add(user)
        await db.flush()
        return [await _confirmation(db, settings, user)]
    if user.status == UserStatus.UNCONFIRMED:
        # The first request stands and only its link is sent again: taking this one's password
        # would let someone else's password be confirmed by the owner of the address.
        return [await _confirmation(db, settings, user)]
    if user.status == UserStatus.PENDING:
        return [_still_pending(settings, user)]
    return [_already_registered(settings, user)]


async def _confirmation(db: AsyncSession, settings: Settings, user: User) -> Mail:
    """A new confirmation link for the user; an earlier one stops working."""
    await db.execute(
        delete(EmailToken).where(EmailToken.user_id == user.id, EmailToken.purpose == CONFIRM)
    )
    token = secrets.token_urlsafe(32)
    db.add(
        EmailToken(
            token_hash=token_hash(token),
            user_id=user.id,
            purpose=CONFIRM,
            expires_at=utcnow() + timedelta(hours=CONFIRM_HOURS),
        )
    )
    await db.flush()
    site = settings.site_address
    return Mail(
        to=user.email,
        subject=f"Confirm your e-mail address for the {PRODUCT}",
        body=(
            f"Hello {user.name},\n\n"
            f"someone, probably you, asked for an account on the {PRODUCT} at {site} "
            "with this e-mail address.\n\n"
            f"To confirm the address and send the request for review, open this link within "
            f"{CONFIRM_HOURS} hours:\n\n"
            f"{site}/confirm-email?token={token}\n\n"
            f"{settings.registration_reviewer} reviews each request. You will get another "
            "e-mail when yours is approved.\n\n"
            "If you did not ask for an account, ignore this e-mail: the request is deleted "
            f"after {UNCONFIRMED_DAYS} days.\n"
        ),
    )


def _still_pending(settings: Settings, user: User) -> Mail:
    return Mail(
        to=user.email,
        subject=f"Your request for a {PRODUCT} account",
        body=(
            f"Hello {user.name},\n\n"
            f"your request for an account on the {PRODUCT} at {settings.site_address} is "
            f"waiting for review. {settings.registration_reviewer} reviews each request, and "
            "you will get an e-mail when yours is approved.\n\n"
            "If you did not ask again, ignore this e-mail.\n"
        ),
    )


def _already_registered(settings: Settings, user: User) -> Mail:
    site = settings.site_address
    return Mail(
        to=user.email,
        subject=f"Your {PRODUCT} account",
        body=(
            f"Hello {user.name},\n\n"
            f"someone asked for a new account on the {PRODUCT} with this e-mail address, but "
            f"you already have one. Sign in at {site}/login. If you have forgotten your "
            "password, ask an administrator to reset it.\n\n"
            "If you did not ask, ignore this e-mail.\n"
        ),
    )


async def confirm(db: AsyncSession, settings: Settings, token: str) -> list[Mail]:
    """Confirm the address behind a link: the request goes to the admins for review."""
    invalid = AccountError(
        "This link does not work any more: it was used already or has expired. "
        "If your address is not confirmed yet, ask for an account again."
    )
    row = await db.get(EmailToken, token_hash(token))
    if row is None or row.purpose != CONFIRM or row.expires_at < utcnow():
        raise invalid
    user = await db.get(User, row.user_id)
    await db.delete(row)
    if user is None or user.status != UserStatus.UNCONFIRMED:
        raise invalid
    user.status = UserStatus.PENDING
    user.email_confirmed_at = utcnow()
    await db.flush()
    admins = (
        await db.execute(
            select(User).where(User.is_admin, User.is_active, User.status == UserStatus.APPROVED)
        )
    ).scalars()
    site = settings.site_address
    body = (
        f"{user.name} <{user.email}> from {user.organisation} asks for an account on the "
        f"{PRODUCT} and has confirmed the e-mail address.\n\n"
        "What they will use it for:\n\n"
        f"{user.purpose}\n\n"
        f"Approve or reject the request under Settings → Users: {site}/settings?tab=users\n"
    )
    subject = f"Account request: {user.name} ({user.organisation})"
    return [Mail(to=admin.email, subject=subject, body=body) for admin in admins]


async def approve(db: AsyncSession, settings: Settings, user: User) -> Mail:
    """Let the person sign in, and tell them."""
    if user.status != UserStatus.PENDING:
        raise RequestStateError(
            "Only a request whose e-mail address is confirmed can be approved."
            if user.status == UserStatus.UNCONFIRMED
            else "This account is already approved."
        )
    user.status = UserStatus.APPROVED
    user.is_active = True
    user.approved_at = utcnow()
    await db.flush()
    site = settings.site_address
    return Mail(
        to=user.email,
        subject=f"Your {PRODUCT} account is approved",
        body=(
            f"Hello {user.name},\n\n"
            f"your account on the {PRODUCT} is approved. Sign in at {site}/login with this "
            "e-mail address and the password you chose.\n"
        ),
    )


async def reject(db: AsyncSession, settings: Settings, user: User) -> Mail | None:
    """Delete the request with what the person entered. Only a confirmed address is told: an
    unconfirmed one may not be theirs."""
    if user.status == UserStatus.APPROVED:
        raise RequestStateError("This is an account, not a request: disable it instead.")
    mail = None
    if user.status == UserStatus.PENDING:
        mail = Mail(
            to=user.email,
            subject=f"Your request for a {PRODUCT} account",
            body=(
                f"Hello {user.name},\n\n"
                f"your request for an account on the {PRODUCT} at {settings.site_address} "
                "was not approved, and what you entered has been deleted.\n"
            ),
        )
    await db.delete(user)
    await db.flush()
    return mail
