"""User accounts, managed by admins (docs/PLAN.md 7.11). There is no self sign-up."""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from echolot_app.auth import (
    AccountError,
    active_admins,
    create_user,
    end_all_sessions,
    generate_password,
    set_password,
)
from echolot_app.db import User
from echolot_app.deps import AdminUser, DbSession
from echolot_app.schemas import UserCreate, UserOut, UserUpdate, UserWithPassword

router = APIRouter(prefix="/api/admin/users", tags=["admin"])
log = structlog.get_logger(__name__)


async def _user(db: DbSession, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.get("", response_model=list[UserOut])
async def list_users(db: DbSession, _admin: AdminUser) -> list[UserOut]:
    rows = (await db.execute(select(User).order_by(User.email))).scalars()
    return [UserOut.model_validate(u) for u in rows]


@router.post("", response_model=UserWithPassword, status_code=status.HTTP_201_CREATED)
async def add_user(body: UserCreate, db: DbSession, admin: AdminUser) -> UserWithPassword:
    generated = None if body.password else generate_password()
    try:
        user = await create_user(
            db, body.email, body.name, body.password or generated or "", is_admin=body.is_admin
        )
    except AccountError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    log.info("admin.user_created", by=str(admin.id), user_id=str(user.id), admin=user.is_admin)
    return UserWithPassword(user=UserOut.model_validate(user), password=generated)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID, body: UserUpdate, db: DbSession, admin: AdminUser
) -> UserOut:
    user = await _user(db, user_id)
    losing_admin = (body.is_admin is False and user.is_admin) or (
        body.is_active is False and user.is_admin and user.is_active
    )
    if losing_admin and await active_admins(db) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "There must be at least one active admin")
    if user.id == admin.id and (body.is_active is False or body.is_admin is False):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "You cannot disable yourself or remove your own admin role"
        )
    if body.name is not None:
        user.name = body.name.strip()
    if body.is_admin is not None:
        user.is_admin = body.is_admin
    if body.is_active is not None:
        user.is_active = body.is_active
        if not body.is_active:
            await end_all_sessions(db, user.id)
    await db.flush()
    await db.refresh(user)
    log.info("admin.user_updated", by=str(admin.id), user_id=str(user.id))
    return UserOut.model_validate(user)


@router.post("/{user_id}/reset-password", response_model=UserWithPassword)
async def reset_password(user_id: uuid.UUID, db: DbSession, admin: AdminUser) -> UserWithPassword:
    """A new temporary password, shown once; the user is signed out everywhere."""
    user = await _user(db, user_id)
    password = generate_password()
    await set_password(db, user, password)
    await db.refresh(user)
    log.info("admin.password_reset", by=str(admin.id), user_id=str(user.id))
    return UserWithPassword(user=UserOut.model_validate(user), password=password)
