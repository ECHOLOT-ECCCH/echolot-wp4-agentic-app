"""Cookie sessions, CSRF double-submit tokens and the transient OIDC state cookie."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_core.db.models import Session, User, UserRole

SESSION_COOKIE = "elx_session"
CSRF_COOKIE = "elx_csrf"
OIDC_COOKIE = "elx_oidc"
CSRF_HEADER = "X-CSRF-Token"
SESSION_TTL = timedelta(days=7)
SESSION_TOUCH_INTERVAL = timedelta(hours=1)
OIDC_STATE_MAX_AGE_S = 600


def now() -> datetime:
    return datetime.now(UTC)


@dataclass
class CookieSpec:
    name: str
    value: str
    max_age: int | None
    http_only: bool
    secure: bool
    same_site: str = "lax"
    path: str = "/"


class SessionStore:
    def __init__(self, secret: str, secure_cookies: bool) -> None:
        self._serializer = URLSafeTimedSerializer(secret, salt="elexifier-oidc")
        self.secure = secure_cookies

    # --- OIDC transient state ------------------------------------------------------------------

    def sign_oidc_state(self, payload: dict[str, Any]) -> str:
        return self._serializer.dumps(payload)

    def load_oidc_state(self, token: str) -> dict[str, Any] | None:
        try:
            data = self._serializer.loads(token, max_age=OIDC_STATE_MAX_AGE_S)
        except (BadSignature, SignatureExpired):
            return None
        return dict(data) if isinstance(data, dict) else None

    # --- sessions ------------------------------------------------------------------------------

    async def create(
        self,
        db: AsyncSession,
        user: User,
        *,
        user_agent: str | None = None,
        ip_address: str | None = None,
        id_token: str | None = None,
    ) -> Session:
        session = Session(
            user_id=user.id,
            csrf_token=secrets.token_urlsafe(32),
            expires_at=now() + SESSION_TTL,
            last_seen_at=now(),
            user_agent=(user_agent or "")[:512] or None,
            ip_address=(ip_address or "")[:64] or None,
            id_token=id_token,
        )
        db.add(session)
        await db.flush()
        return session

    async def load(
        self, db: AsyncSession, raw_session_id: str | None
    ) -> tuple[Session, User] | None:
        if not raw_session_id:
            return None
        try:
            session_id = uuid.UUID(raw_session_id)
        except ValueError:
            return None
        row = (
            await db.execute(
                select(Session, User)
                .join(User, User.id == Session.user_id)
                .where(Session.id == session_id, Session.expires_at > now())
            )
        ).first()
        if row is None:
            return None
        session, user = row
        if session.last_seen_at is None or now() - session.last_seen_at > SESSION_TOUCH_INTERVAL:
            session.last_seen_at = now()
            session.expires_at = now() + SESSION_TTL  # sliding expiry
        return session, user

    async def delete(self, db: AsyncSession, session: Session) -> None:
        await db.delete(session)

    # --- cookies -----------------------------------------------------------------------------

    def session_cookies(self, session: Session) -> list[CookieSpec]:
        max_age = int(SESSION_TTL.total_seconds())
        return [
            CookieSpec(SESSION_COOKIE, str(session.id), max_age, True, self.secure),
            CookieSpec(CSRF_COOKIE, session.csrf_token, max_age, False, self.secure),
        ]

    def oidc_cookie(self, signed_state: str) -> CookieSpec:
        return CookieSpec(OIDC_COOKIE, signed_state, OIDC_STATE_MAX_AGE_S, True, self.secure)

    @staticmethod
    def cleared_cookie_names() -> list[str]:
        return [SESSION_COOKIE, CSRF_COOKIE, OIDC_COOKIE]


def role_from_oidc_roles(roles: list[str]) -> str:
    return UserRole.ADMIN.value if UserRole.ADMIN.value in roles else UserRole.USER.value


def is_safe_next(path: str | None) -> bool:
    return bool(path) and path.startswith("/") and not path.startswith("//") and "\\" not in path
