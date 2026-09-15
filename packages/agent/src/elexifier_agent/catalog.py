"""Model catalogs per provider: live listings merged with a small curated registry."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

from elexifier_agent.models import ModelInfo

CATALOG_TTL_S = 3600
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_REGISTRY_PATH = Path(__file__).with_name("model_registry.json")


class CatalogError(Exception):
    pass


def curated_registry() -> dict[str, list[ModelInfo]]:
    raw = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    return {
        provider: [ModelInfo(provider=provider, **entry) for entry in entries]
        for provider, entries in raw.items()
    }


class ModelCatalog:
    """Lists models for a credential. Results are cached per provider + endpoint for an hour."""

    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self._http = http or httpx.AsyncClient(timeout=15)
        self._cache: dict[str, tuple[float, list[ModelInfo]]] = {}
        self._curated = curated_registry()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def list_models(
        self,
        provider: str,
        api_key: str | None,
        base_url: str | None = None,
        declared_capabilities: dict[str, Any] | None = None,
        *,
        force: bool = False,
    ) -> list[ModelInfo]:
        key_digest = hashlib.sha256((api_key or "").encode()).hexdigest()[:16]
        cache_key = f"{provider}|{base_url or ''}|{key_digest}"
        cached = self._cache.get(cache_key)
        if cached and not force and time.monotonic() - cached[0] < CATALOG_TTL_S:
            return cached[1]
        models = await self._fetch(provider, api_key, base_url, declared_capabilities or {})
        self._cache[cache_key] = (time.monotonic(), models)
        return models

    async def _fetch(
        self, provider: str, api_key: str | None, base_url: str | None, declared: dict[str, Any]
    ) -> list[ModelInfo]:
        match provider:
            case "openrouter":
                return await self._openrouter()
            case "openai":
                ids = await self._openai_compatible_ids("https://api.openai.com/v1", api_key)
                return self._merge(provider, ids)
            case "anthropic":
                return self._merge(provider, await self._anthropic_ids(api_key))
            case "google":
                return await self._google(api_key)
            case "deepseek":
                ids = await self._openai_compatible_ids("https://api.deepseek.com", api_key)
                return self._merge(provider, ids)
            case "openai_compatible":
                if not base_url:
                    raise CatalogError("openai_compatible requires a base_url")
                try:
                    ids = await self._openai_compatible_ids(base_url.rstrip("/"), api_key)
                except CatalogError:
                    ids = []
                return [
                    ModelInfo(
                        id=i,
                        label=i,
                        provider=provider,
                        supports_tools=declared.get("supports_tools"),
                        supports_vision=declared.get("supports_vision"),
                        context_length=declared.get("context_length"),
                    )
                    for i in ids
                ]
            case "test":
                return [
                    ModelInfo(
                        id="test",
                        label="Test model (canned answers)",
                        provider="test",
                        supports_tools=True,
                        supports_vision=False,
                        context_length=100_000,
                        recommended=True,
                    )
                ]
        raise CatalogError(f"unknown provider {provider!r}")

    def _merge(self, provider: str, live_ids: list[str]) -> list[ModelInfo]:
        curated = {m.id: m for m in self._curated.get(provider, [])}
        result: list[ModelInfo] = []
        for model_id in live_ids:
            known = curated.get(model_id)
            result.append(
                known
                if known
                else ModelInfo(id=model_id, label=model_id, provider=provider, **_guess(model_id))
            )
        # curated models that the listing did not return (for example not yet enabled) are omitted
        result.sort(key=lambda m: (not m.recommended, m.label.lower()))
        return result

    async def _get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        try:
            resp = await self._http.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise CatalogError(f"cannot reach {url}: {exc}") from exc
        if resp.status_code == 401 or resp.status_code == 403:
            raise CatalogError("the provider rejected the API key")
        if resp.status_code != 200:
            raise CatalogError(f"provider returned HTTP {resp.status_code} for model listing")
        try:
            return resp.json()
        except ValueError as exc:
            raise CatalogError("provider returned a non-JSON model listing") from exc

    async def _openrouter(self) -> list[ModelInfo]:
        data = await self._get_json(OPENROUTER_MODELS_URL)
        curated = {m.id: m for m in self._curated.get("openrouter", [])}
        models: list[ModelInfo] = []
        for entry in data.get("data", []):
            model_id = str(entry.get("id", ""))
            if not model_id:
                continue
            arch = entry.get("architecture") or {}
            modalities = [str(m) for m in (arch.get("input_modalities") or [])]
            params = [str(p) for p in (entry.get("supported_parameters") or [])]
            pricing = entry.get("pricing") or {}
            models.append(
                ModelInfo(
                    id=model_id,
                    label=str(entry.get("name") or model_id),
                    provider="openrouter",
                    supports_tools="tools" in params if params else None,
                    supports_vision="image" in modalities if modalities else None,
                    context_length=_int_or_none(entry.get("context_length")),
                    price_in_per_m=_per_million(pricing.get("prompt")),
                    price_out_per_m=_per_million(pricing.get("completion")),
                    recommended=model_id in curated,
                )
            )
        models.sort(key=lambda m: (not m.recommended, m.label.lower()))
        return models

    async def _openai_compatible_ids(self, base_url: str, api_key: str | None) -> list[str]:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        data = await self._get_json(f"{base_url}/models", headers=headers)
        entries = data.get("data") if isinstance(data, dict) else data
        return sorted(
            str(e.get("id")) for e in (entries or []) if isinstance(e, dict) and e.get("id")
        )

    async def _anthropic_ids(self, api_key: str | None) -> list[str]:
        headers = {"x-api-key": api_key or "", "anthropic-version": "2023-06-01"}
        data = await self._get_json("https://api.anthropic.com/v1/models?limit=100", headers)
        return [str(e["id"]) for e in data.get("data", []) if e.get("id")]

    async def _google(self, api_key: str | None) -> list[ModelInfo]:
        base = "https://generativelanguage.googleapis.com/v1beta/models"
        data = await self._get_json(f"{base}?pageSize=200&key={api_key or ''}")
        curated = {m.id: m for m in self._curated.get("google", [])}
        models: list[ModelInfo] = []
        for entry in data.get("models", []):
            methods = entry.get("supportedGenerationMethods") or []
            if "generateContent" not in methods:
                continue
            model_id = str(entry.get("name", "")).removeprefix("models/")
            known = curated.get(model_id)
            if known:
                models.append(known)
                continue
            models.append(
                ModelInfo(
                    id=model_id,
                    label=str(entry.get("displayName") or model_id),
                    provider="google",
                    supports_tools=True,
                    supports_vision="gemini" in model_id,
                    context_length=_int_or_none(entry.get("inputTokenLimit")),
                )
            )
        models.sort(key=lambda m: (not m.recommended, m.label.lower()))
        return models


def _guess(model_id: str) -> dict[str, Any]:
    lowered = model_id.lower()
    vision_families = ("gpt-4o", "gpt-4.1", "gpt-5", "o3", "o4", "claude", "gemini", "pixtral")
    return {
        "supports_tools": None
        if any(w in lowered for w in ("embedding", "tts", "whisper"))
        else True,
        "supports_vision": True if any(w in lowered for w in vision_families) else None,
    }


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _per_million(value: Any) -> float | None:
    try:
        return round(float(value) * 1_000_000, 4) if value is not None else None
    except (TypeError, ValueError):
        return None
