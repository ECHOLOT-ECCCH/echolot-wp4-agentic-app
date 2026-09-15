"""Bring-your-own-key provider credentials and model catalogs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from elexifier_agent.catalog import CatalogError
from elexifier_agent.models import PROVIDERS, ModelInfo, UnsupportedProvider, build_model
from elexifier_agent.probe import probe_model
from elexifier_api import audit
from elexifier_api.deps import CurrentSecretBox, CurrentSettings, CurrentUser, DbSession, State
from elexifier_api.schemas import (
    CredentialCreate,
    CredentialOut,
    ProbeOut,
    ProbeRequest,
    ProviderOut,
)
from elexifier_core.crypto import mask_secret
from elexifier_core.db.models import ProviderCredential, UsageRecord

router = APIRouter(prefix="/api", tags=["credentials"])


def _providers(settings_enable_test: bool) -> list[ProviderOut]:
    return [
        ProviderOut(
            name=p.name,
            label=p.label,
            needs_api_key=p.needs_api_key,
            needs_base_url=p.needs_base_url,
            docs_url=p.docs_url,
        )
        for p in PROVIDERS.values()
        if p.name != "test" or settings_enable_test
    ]


def _out(credential: ProviderCredential, key_hint: str) -> CredentialOut:
    return CredentialOut(
        id=credential.id,
        provider=credential.provider,
        label=credential.label,
        base_url=credential.base_url,
        key_hint=key_hint,
        declared_capabilities=credential.declared_capabilities,
        last_used_at=credential.last_used_at,
        created_at=credential.created_at,
    )


async def _owned_credential(
    db: DbSession, user_id: uuid.UUID, credential_id: uuid.UUID
) -> ProviderCredential:
    credential = await db.get(ProviderCredential, credential_id)
    if credential is None or credential.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Credential not found")
    return credential


@router.get("/providers", response_model=list[ProviderOut])
async def list_providers(settings: CurrentSettings, _user: CurrentUser) -> list[ProviderOut]:
    return _providers(settings.elx_enable_test_model)


@router.get("/me/credentials", response_model=list[CredentialOut])
async def list_credentials(
    db: DbSession, user: CurrentUser, box: CurrentSecretBox
) -> list[CredentialOut]:
    rows = (
        await db.execute(
            select(ProviderCredential)
            .where(ProviderCredential.user_id == user.id)
            .order_by(ProviderCredential.created_at)
        )
    ).scalars()
    return [
        _out(c, mask_secret(box.decrypt(c.encrypted_key)) if c.encrypted_key else "") for c in rows
    ]


@router.post("/me/credentials", response_model=CredentialOut, status_code=status.HTTP_201_CREATED)
async def create_credential(
    body: CredentialCreate,
    db: DbSession,
    user: CurrentUser,
    box: CurrentSecretBox,
    settings: CurrentSettings,
) -> CredentialOut:
    info = PROVIDERS.get(body.provider)
    if info is None or (info.name == "test" and not settings.elx_enable_test_model):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown provider")
    api_key = (body.api_key or "").strip()
    base_url = (body.base_url or "").strip() or None
    if info.needs_api_key and not api_key:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "An API key is required")
    if info.needs_base_url and not base_url:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A base URL is required")
    if base_url and not base_url.startswith(("http://", "https://")):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Base URL must be http(s)")
    duplicate = (
        await db.execute(
            select(ProviderCredential.id).where(
                ProviderCredential.user_id == user.id,
                ProviderCredential.label == body.label.strip(),
            )
        )
    ).scalar_one_or_none()
    if duplicate:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "You already have a credential with that label"
        )
    credential = ProviderCredential(
        user_id=user.id,
        provider=info.name,
        label=body.label.strip(),
        base_url=base_url,
        encrypted_key=box.encrypt(api_key) if api_key else "",
        declared_capabilities=body.declared_capabilities,
    )
    db.add(credential)
    audit.record(db, user.id, "credential.created", credential.id, provider=info.name)
    await db.flush()
    await db.refresh(credential)
    return _out(credential, mask_secret(api_key))


@router.delete("/me/credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(credential_id: uuid.UUID, db: DbSession, user: CurrentUser) -> Response:
    credential = await _owned_credential(db, user.id, credential_id)
    await db.delete(credential)
    audit.record(db, user.id, "credential.deleted", credential_id, provider=credential.provider)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/models", response_model=list[ModelInfo])
async def list_models(
    credential_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    box: CurrentSecretBox,
    state: State,
    refresh: bool = False,
) -> list[ModelInfo]:
    credential = await _owned_credential(db, user.id, credential_id)
    api_key = box.decrypt(credential.encrypted_key) if credential.encrypted_key else None
    try:
        return await state.catalog.list_models(
            credential.provider,
            api_key,
            credential.base_url,
            credential.declared_capabilities,
            force=refresh,
        )
    except CatalogError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.post("/me/credentials/{credential_id}/test", response_model=ProbeOut)
async def test_credential(
    credential_id: uuid.UUID,
    body: ProbeRequest,
    db: DbSession,
    user: CurrentUser,
    box: CurrentSecretBox,
    settings: CurrentSettings,
    state: State,
) -> ProbeOut:
    credential = await _owned_credential(db, user.id, credential_id)
    api_key = box.decrypt(credential.encrypted_key) if credential.encrypted_key else None
    model_id = (body.model_id or "").strip() or PROVIDERS[credential.provider].default_probe_model
    if not model_id:
        try:
            models = await state.catalog.list_models(
                credential.provider, api_key, credential.base_url, credential.declared_capabilities
            )
        except CatalogError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        if not models:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose a model to test")
        model_id = models[0].id
    try:
        model = build_model(
            credential.provider,
            model_id,
            api_key,
            credential.base_url,
            app_url=settings.public_url,
            allow_test_model=settings.elx_enable_test_model,
        )
    except UnsupportedProvider as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    result = await probe_model(model, model_id)
    if result.ok:
        credential.last_used_at = datetime.now(UTC)
        db.add(
            UsageRecord(
                user_id=user.id,
                provider=credential.provider,
                model=model_id,
                purpose="probe",
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
        )
    audit.record(db, user.id, "credential.tested", credential.id, model=model_id, ok=result.ok)
    return ProbeOut(
        ok=result.ok,
        model_id=result.model_id,
        latency_ms=result.latency_ms,
        message=result.message,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        reply=result.reply,
    )
