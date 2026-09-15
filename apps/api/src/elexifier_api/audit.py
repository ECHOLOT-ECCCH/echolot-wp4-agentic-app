"""Audit log helper."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_core.db.models import AuditLog


def record(
    db: AsyncSession,
    user_id: uuid.UUID | None,
    action: str,
    target: uuid.UUID | str | None = None,
    **meta: Any,
) -> None:
    db.add(AuditLog(user_id=user_id, action=action, target=str(target or ""), meta=meta))
