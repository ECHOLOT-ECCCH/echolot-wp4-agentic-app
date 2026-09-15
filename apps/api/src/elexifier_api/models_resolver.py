"""Resolve which model a chat turn uses and with whose key (BYOK)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_agent.models import PROVIDERS, ModelSpec, UnsupportedProvider, build_model
from elexifier_core.crypto import SecretBox
from elexifier_core.db.models import Project, ProviderCredential, User
from elexifier_core.settings import Settings


def parse_model_spec(raw: Any) -> ModelSpec | None:
    if not isinstance(raw, dict):
        return None
    provider = raw.get("provider")
    model_id = raw.get("model_id")
    if not provider or not model_id:
        return None
    credential_id = raw.get("credential_id")
    return ModelSpec(
        provider=str(provider),
        model_id=str(model_id),
        credential_id=str(credential_id) if credential_id else None,
    )


async def resolve_model(
    db: AsyncSession,
    *,
    user: User,
    project: Project,
    requested: Any,
    box: SecretBox,
    settings: Settings,
) -> tuple[ModelSpec, Any]:
    """Return the model spec and an instantiated Pydantic AI model.

    Order: the request's ``model`` field, then the project's default model. The key always comes
    from a credential owned by the current user: the referenced one if it is theirs, otherwise their
    first credential for the same provider.
    """
    spec = parse_model_spec(requested) or parse_model_spec(project.default_model)
    if spec is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Choose a model for this project first (model picker in the chat header)",
        )
    info = PROVIDERS.get(spec.provider)
    if info is None or (info.name == "test" and not settings.elx_enable_test_model):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown provider {spec.provider!r}")

    credential: ProviderCredential | None = None
    if info.needs_api_key or info.needs_base_url:
        if spec.credential_id:
            try:
                cid = uuid.UUID(spec.credential_id)
            except ValueError:
                cid = None
            if cid is not None:
                candidate = await db.get(ProviderCredential, cid)
                if candidate is not None and candidate.user_id == user.id:
                    credential = candidate
        if credential is None:
            credential = (
                await db.execute(
                    select(ProviderCredential)
                    .where(
                        ProviderCredential.user_id == user.id,
                        ProviderCredential.provider == spec.provider,
                    )
                    .order_by(ProviderCredential.created_at)
                    .limit(1)
                )
            ).scalar_one_or_none()
        if credential is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Add your own {info.label} credential in Settings to use {spec.model_id}",
            )
    api_key = (
        box.decrypt(credential.encrypted_key) if credential and credential.encrypted_key else None
    )
    try:
        model = build_model(
            spec.provider,
            spec.model_id,
            api_key,
            credential.base_url if credential else None,
            app_url=settings.public_url,
            allow_test_model=settings.elx_enable_test_model,
        )
    except UnsupportedProvider as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    resolved = ModelSpec(
        provider=spec.provider,
        model_id=spec.model_id,
        credential_id=str(credential.id) if credential else None,
    )
    return resolved, model
