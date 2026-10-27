"""Per-run tokens for the runner's I/O endpoints (docs/PLAN.md 7.3)."""

from __future__ import annotations

import hashlib
import hmac
import secrets


def new_token() -> tuple[str, str]:
    """A token and the hash stored for it."""
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(token: str | None, stored: str | None) -> bool:
    if not token or not stored:
        return False
    return hmac.compare_digest(token_hash(token), stored)
