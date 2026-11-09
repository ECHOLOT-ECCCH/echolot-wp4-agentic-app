"""Accounts and sessions (docs/PLAN.md 7.11): password hashing, session tokens, login throttling
and the user operations shared by the API and the command line."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from echolot_app.db import User, UserSession, Workspace, utcnow

SESSION_COOKIE = "echolot_session"
# unsafe requests to /api must carry this header: a page on another site cannot add it without
# a CORS preflight, which this app never grants (docs/PLAN.md 7.11)
CSRF_HEADER = "X-Echolot-Request"
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256

# scrypt with OWASP's N=2^15, r=8, p=3 (32 MiB, about 0.1 s): stdlib only, memory-hard
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**15, 8, 3
_SCRYPT_MAXMEM = 64 * 1024 * 1024


class AccountError(ValueError):
    """A user operation that cannot be done, with a message fit for the person asking."""


# --- passwords ---------------------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM,
        dklen=32,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = _unb64(digest)
        actual = hashlib.scrypt(
            password.encode(),
            salt=_unb64(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=_SCRYPT_MAXMEM,
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# verified against when the e-mail is unknown, so a miss takes as long as a wrong password
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


async def check_password(password: str, stored: str | None) -> bool:
    """Verify in a thread: scrypt is deliberately slow and must not block the event loop."""
    return await asyncio.to_thread(verify_password, password, stored or _DUMMY_HASH) and bool(
        stored
    )


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AccountError(f"The password needs at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise AccountError(f"The password can have at most {MAX_PASSWORD_LENGTH} characters.")


def generate_password() -> str:
    """A temporary password an admin hands to a new user (about 100 bits)."""
    return secrets.token_urlsafe(13)


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@") or len(email) > 320:
        raise AccountError("That is not an e-mail address.")
    return email


# --- sessions ----------------------------------------------------------------------------------


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_session(db: AsyncSession, user: User, days: int) -> str:
    """A new session for the user; returns the token for the cookie (stored only as a hash)."""
    token = secrets.token_urlsafe(32)
    now = utcnow()
    db.add(
        UserSession(
            token_hash=token_hash(token),
            user_id=user.id,
            expires_at=now + timedelta(days=days),
            last_seen_at=now,
        )
    )
    user.last_login_at = now
    # expired sessions of anyone are cleaned up on the way
    await db.execute(delete(UserSession).where(UserSession.expires_at < now))
    await db.flush()
    return token


# a session's expiry moves on with use, written at most this often
_TOUCH_EVERY = timedelta(hours=1)


async def user_for_token(db: AsyncSession, token: str | None, days: int) -> User | None:
    """The active user a session token belongs to, extending the session's life; else None."""
    if not token:
        return None
    session = await db.get(UserSession, token_hash(token))
    now = utcnow()
    if session is None or session.expires_at < now:
        return None
    user = await db.get(User, session.user_id)
    if user is None or not user.is_active:
        return None
    if now - session.last_seen_at > _TOUCH_EVERY:
        session.last_seen_at = now
        session.expires_at = now + timedelta(days=days)
        await db.flush()
    return user


async def end_session(db: AsyncSession, token: str | None) -> None:
    if token:
        await db.execute(delete(UserSession).where(UserSession.token_hash == token_hash(token)))


async def end_all_sessions(
    db: AsyncSession, user_id: uuid.UUID, keep_token: str | None = None
) -> None:
    query = delete(UserSession).where(UserSession.user_id == user_id)
    if keep_token:
        query = query.where(UserSession.token_hash != token_hash(keep_token))
    await db.execute(query)


# --- login throttling --------------------------------------------------------------------------


class LoginLimiter:
    """Counts failed logins per e-mail address and per client address in a sliding window.

    In memory: the app is one process (PLAN section 3). A restart forgets the counts."""

    def __init__(self, max_failures: int, window_s: float) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _recent(self, key: str, now: float) -> deque[float]:
        stamps = self._failures[key]
        while stamps and now - stamps[0] > self.window_s:
            stamps.popleft()
        if not stamps:
            del self._failures[key]
            return deque()
        return stamps

    def retry_after(self, *keys: str) -> int:
        """Seconds until a login for these keys is allowed again; 0 when it is allowed now."""
        now = time.monotonic()
        wait = 0.0
        for key in keys:
            stamps = self._recent(key, now)
            if len(stamps) >= self.max_failures:
                wait = max(wait, self.window_s - (now - stamps[0]))
        return int(wait) + 1 if wait > 0 else 0

    def failed(self, *keys: str) -> None:
        now = time.monotonic()
        for key in keys:
            self._failures[key].append(now)

    def succeeded(self, key: str) -> None:
        self._failures.pop(key, None)


# --- user operations (API and command line) ----------------------------------------------------


async def find_user(db: AsyncSession, email: str) -> User | None:
    return (
        await db.execute(select(User).where(User.email == email.strip().lower()))
    ).scalar_one_or_none()


async def create_user(
    db: AsyncSession, email: str, name: str, password: str, *, is_admin: bool = False
) -> User:
    """Create an account. The first admin also adopts the workspaces made before login."""
    email = normalize_email(email)
    validate_password(password)
    if await find_user(db, email):
        raise AccountError(f"There is already an account for {email}.")
    user = User(
        email=email,
        name=name.strip(),
        password_hash=await asyncio.to_thread(hash_password, password),
        is_admin=is_admin,
    )
    db.add(user)
    await db.flush()
    if is_admin:
        admins = await db.scalar(select(func.count()).select_from(User).where(User.is_admin))
        if admins == 1:
            await db.execute(
                update(Workspace).where(Workspace.owner_id.is_(None)).values(owner_id=user.id)
            )
    return user


async def set_password(db: AsyncSession, user: User, password: str) -> None:
    """Set a new password and sign the user out everywhere."""
    validate_password(password)
    user.password_hash = await asyncio.to_thread(hash_password, password)
    await end_all_sessions(db, user.id)
    await db.flush()


async def active_admins(db: AsyncSession) -> int:
    return (
        await db.scalar(select(func.count()).select_from(User).where(User.is_admin, User.is_active))
    ) or 0
