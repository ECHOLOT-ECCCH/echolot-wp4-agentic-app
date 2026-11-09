"""Model vendors, the user's own keys and the user's model choice (docs/PLAN.md 7.11).

A stored key never leaves the server: responses carry only a hint of its last characters."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from echolot_app.agent.catalog import ModelInfo, ModelListing
from echolot_app.agent.models import (
    VENDORS,
    UnsupportedProvider,
    Vendor,
    build_model,
    server_model_available,
    vendor_enabled,
)
from echolot_app.agent.probe import ProbeResult, probe_model
from echolot_app.crypto import SecretBoxError, key_hint
from echolot_app.db import ProviderKey, User
from echolot_app.deps import CurrentSecretBox, CurrentSettings, CurrentUser, DbSession, State
from echolot_app.schemas import (
    KeyIn,
    ModelChoiceIn,
    ModelChoiceOut,
    ProbeIn,
    ServerModelOut,
    SuggestedModelOut,
    VendorOut,
)
from echolot_app.settings import Settings
from echolot_app.user_models import ModelNotReady, chosen_model, model_for_user, user_key

router = APIRouter(prefix="/api", tags=["models"])
log = structlog.get_logger(__name__)


def _vendor(settings: Settings, provider: str) -> Vendor:
    if not vendor_enabled(settings, provider):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown model vendor")
    return VENDORS[provider]


def _vendor_out(vendor: Vendor, key: ProviderKey | None) -> VendorOut:
    return VendorOut(
        name=vendor.name,
        label=vendor.label,
        needs_key=vendor.needs_key,
        needs_base_url=vendor.needs_base_url,
        key_url=vendor.key_url,
        suggested=[SuggestedModelOut(id=s.id, label=s.label) for s in vendor.suggested],
        has_key=key is not None,
        key_hint=key.key_hint if key else "",
        base_url=key.base_url if key else None,
        key_updated_at=key.updated_at if key else None,
        key_last_used_at=key.last_used_at if key else None,
    )


@router.get("/vendors", response_model=list[VendorOut])
async def list_vendors(
    db: DbSession, user: CurrentUser, settings: CurrentSettings
) -> list[VendorOut]:
    keys = {
        k.provider: k
        for k in (
            await db.execute(select(ProviderKey).where(ProviderKey.user_id == user.id))
        ).scalars()
    }
    return [
        _vendor_out(v, keys.get(v.name))
        for v in VENDORS.values()
        if vendor_enabled(settings, v.name)
    ]


@router.put("/me/keys/{provider}", response_model=VendorOut)
async def save_key(
    provider: str,
    body: KeyIn,
    db: DbSession,
    user: CurrentUser,
    settings: CurrentSettings,
    box: CurrentSecretBox,
) -> VendorOut:
    """Store or replace the user's key for a vendor. An empty key keeps the stored one (to
    change only the base URL)."""
    vendor = _vendor(settings, provider)
    api_key = (body.api_key or "").strip()
    base_url = (body.base_url or "").strip().rstrip("/") or None
    existing = await user_key(db, user, provider)
    if vendor.needs_base_url:
        if not base_url or not base_url.startswith(("http://", "https://")):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter the endpoint's http(s) base URL"
            )
    else:
        base_url = None
    if vendor.needs_key and not api_key and existing is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter the API key")
    key = existing or ProviderKey(user_id=user.id, provider=provider)
    if api_key or existing is None:
        key.encrypted_key = box.encrypt(api_key) if api_key else ""
        key.key_hint = key_hint(api_key) if api_key else ""
    key.base_url = base_url
    if existing is None:
        db.add(key)
    await db.flush()
    await db.refresh(key)
    log.info("keys.saved", user_id=str(user.id), provider=provider)
    return _vendor_out(vendor, key)


@router.delete("/me/keys/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_key(
    provider: str, db: DbSession, user: CurrentUser, settings: CurrentSettings
) -> Response:
    _vendor(settings, provider)
    key = await user_key(db, user, provider)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No key stored for this vendor")
    await db.delete(key)
    log.info("keys.deleted", user_id=str(user.id), provider=provider)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _stored_key(
    db: DbSession, user: User, provider: str, box: CurrentSecretBox
) -> tuple[ProviderKey | None, str | None]:
    key = await user_key(db, user, provider)
    if key is None or not key.encrypted_key:
        return key, None
    try:
        return key, box.decrypt(key.encrypted_key)
    except SecretBoxError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The stored key can no longer be read (the server's secret changed); enter it again",
        ) from exc


@router.get("/vendors/{provider}/models", response_model=ModelListing)
async def list_vendor_models(
    provider: str,
    db: DbSession,
    user: CurrentUser,
    settings: CurrentSettings,
    box: CurrentSecretBox,
    state: State,
) -> ModelListing:
    """The vendor's models for the user's key; the suggested ones alone without a key."""
    vendor = _vendor(settings, provider)
    key, api_key = await _stored_key(db, user, provider, box)
    if key is None and provider != "test":
        return ModelListing(
            models=[ModelInfo(id=s.id, label=s.label, suggested=True) for s in vendor.suggested],
            error=f"Add your {vendor.label} key to see all its models",
        )
    return await state.catalog.list_models(provider, api_key, key.base_url if key else None)


@router.post("/me/keys/{provider}/test", response_model=ProbeResult)
async def test_key(
    provider: str,
    body: ProbeIn,
    db: DbSession,
    user: CurrentUser,
    settings: CurrentSettings,
    box: CurrentSecretBox,
) -> ProbeResult:
    """Send one tiny request with the stored key and the given (or first suggested) model."""
    vendor = _vendor(settings, provider)
    key, api_key = await _stored_key(db, user, provider, box)
    if key is None and vendor.needs_key:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Add your {vendor.label} key first")
    model_id = (body.model_id or "").strip() or (vendor.suggested[0].id if vendor.suggested else "")
    if not model_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose a model to test")
    try:
        model = build_model(
            provider,
            model_id,
            api_key,
            key.base_url if key else None,
            app_url=settings.public_url,
            allow_test_model=settings.enable_test_model,
        )
    except UnsupportedProvider as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    result = await probe_model(model, model_id)
    log.info("keys.tested", user_id=str(user.id), provider=provider, ok=result.ok)
    return result


async def _choice_out(
    db: DbSession, user: User, settings: Settings, box: CurrentSecretBox
) -> ModelChoiceOut:
    chosen = chosen_model(user, settings)
    problem = None
    try:
        await model_for_user(db, user, settings, box, touch=False)
    except ModelNotReady as exc:
        problem = str(exc)
    return ModelChoiceOut(
        provider=chosen.provider,
        model_id=chosen.model_id,
        source=chosen.source,
        server_model=ServerModelOut(provider=settings.model_provider, model_id=settings.model_id)
        if server_model_available(settings)
        else None,
        ready=problem is None,
        problem=problem,
    )


@router.get("/me/model", response_model=ModelChoiceOut)
async def get_model_choice(
    db: DbSession, user: CurrentUser, settings: CurrentSettings, box: CurrentSecretBox
) -> ModelChoiceOut:
    return await _choice_out(db, user, settings, box)


@router.put("/me/model", response_model=ModelChoiceOut)
async def set_model_choice(
    body: ModelChoiceIn,
    db: DbSession,
    user: CurrentUser,
    settings: CurrentSettings,
    box: CurrentSecretBox,
) -> ModelChoiceOut:
    """Choose the agent's model: a vendor and a model id, or no vendor for the server's."""
    if body.provider is None:
        user.model = {}
    else:
        _vendor(settings, body.provider)
        model_id = (body.model_id or "").strip()
        if not model_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose a model")
        user.model = {"provider": body.provider, "model_id": model_id}
    await db.flush()
    return await _choice_out(db, user, settings, box)
