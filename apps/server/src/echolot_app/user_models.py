"""Which model and key the agent uses for a user (docs/PLAN.md 7.11)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic_ai.models import Model
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from echolot_app.agent.models import (
    VENDORS,
    build_model,
    server_model,
    server_model_available,
    vendor_enabled,
)
from echolot_app.crypto import SecretBox, SecretBoxError
from echolot_app.db import ProviderKey, User, utcnow
from echolot_app.settings import Settings


class ModelNotReady(Exception):
    """The user's model cannot be used; the message says what to do."""


@dataclass
class ChosenModel:
    provider: str
    model_id: str
    source: Literal["server", "own"]

    def label(self) -> dict[str, str]:
        """What is recorded with an agent run: never the key."""
        return {"provider": self.provider, "model_id": self.model_id, "key": self.source}


async def user_key(db: AsyncSession, user: User, provider: str) -> ProviderKey | None:
    return (
        await db.execute(
            select(ProviderKey).where(
                ProviderKey.user_id == user.id, ProviderKey.provider == provider
            )
        )
    ).scalar_one_or_none()


def chosen_model(user: User, settings: Settings) -> ChosenModel:
    """The user's choice as stored, or the server's model when there is none."""
    provider = str(user.model.get("provider") or "")
    model_id = str(user.model.get("model_id") or "")
    if provider and model_id:
        return ChosenModel(provider, model_id, "own")
    return ChosenModel(settings.model_provider, settings.model_id, "server")


async def model_for_user(
    db: AsyncSession, user: User, settings: Settings, box: SecretBox, *, touch: bool = True
) -> tuple[ChosenModel, Model]:
    """The model the agent runs for this user, with its key. Raises ModelNotReady.

    ``touch`` records the use on the user's key (off for a mere readiness check)."""
    chosen = chosen_model(user, settings)
    if chosen.source == "server":
        if not server_model_available(settings):
            raise ModelNotReady(
                "No model is set up for you yet. Open Settings, add a key for a model vendor and "
                "choose a model."
            )
        try:
            return chosen, server_model(settings)
        except ValueError as exc:
            raise ModelNotReady(f"The server's model is misconfigured: {exc}") from exc
    vendor = VENDORS.get(chosen.provider)
    if vendor is None or not vendor_enabled(settings, chosen.provider):
        raise ModelNotReady(
            f"The model vendor {chosen.provider!r} is not available here; choose another model."
        )
    key = await user_key(db, user, chosen.provider)
    if key is None:
        raise ModelNotReady(f"Add your {vendor.label} key in Settings to use {chosen.model_id}.")
    try:
        api_key = box.decrypt(key.encrypted_key) if key.encrypted_key else None
    except SecretBoxError as exc:
        raise ModelNotReady(
            f"Your {vendor.label} key can no longer be read (the server's secret changed). "
            "Enter it again in Settings."
        ) from exc
    try:
        model = build_model(
            chosen.provider,
            chosen.model_id,
            api_key,
            key.base_url,
            app_url=settings.public_url,
            allow_test_model=settings.enable_test_model,
        )
    except ValueError as exc:
        raise ModelNotReady(str(exc)) from exc
    if touch:
        key.last_used_at = utcnow()
    return chosen, model
