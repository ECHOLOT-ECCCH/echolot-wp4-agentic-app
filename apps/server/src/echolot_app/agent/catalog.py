"""The models a vendor offers to a key: its live listing, with the suggested models first.

Listings are cached for an hour per vendor, endpoint and key. When a listing fails, the
suggested models are returned with the reason, and any model id can still be typed in.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

import httpx
from pydantic import BaseModel

from echolot_app.agent.models import MISTRAL_BASE_URL, VENDORS

CATALOG_TTL_S = 3600
# models that cannot drive a tool-using chat
_NOT_CHAT = (
    "embed",
    "tts",
    "whisper",
    "dall-e",
    "moderation",
    "transcribe",
    "audio",
    "realtime",
    "image",
    "davinci",
    "babbage",
    "ocr",
)


class ModelInfo(BaseModel):
    id: str
    label: str
    suggested: bool = False


class ModelListing(BaseModel):
    models: list[ModelInfo]
    # why the vendor's own listing is missing, when it is
    error: str | None = None


class CatalogError(Exception):
    pass


class ModelCatalog:
    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self._http = http or httpx.AsyncClient(timeout=15)
        self._cache: dict[str, tuple[float, list[ModelInfo]]] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def list_models(
        self, provider: str, api_key: str | None, base_url: str | None = None
    ) -> ModelListing:
        suggested = [
            ModelInfo(id=s.id, label=s.label, suggested=True) for s in VENDORS[provider].suggested
        ]
        if provider == "test":
            return ModelListing(models=suggested)
        digest = hashlib.sha256((api_key or "").encode()).hexdigest()[:16]
        cache_key = f"{provider}|{base_url or ''}|{digest}"
        cached = self._cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < CATALOG_TTL_S:
            live = cached[1]
        else:
            try:
                live = await self._fetch(provider, api_key, base_url)
            except CatalogError as exc:
                return ModelListing(models=suggested, error=str(exc))
            self._cache[cache_key] = (time.monotonic(), live)
        known = {m.id for m in live}
        # a suggested model the key cannot see is left out (not enabled for it, or retired)
        first = [m for m in suggested if m.id in known]
        rest = sorted(
            (m for m in live if m.id not in {s.id for s in first}), key=lambda m: m.label.lower()
        )
        return ModelListing(models=[*first, *rest])

    async def _fetch(
        self, provider: str, api_key: str | None, base_url: str | None
    ) -> list[ModelInfo]:
        match provider:
            case "anthropic":
                data = await self._get(
                    "https://api.anthropic.com/v1/models?limit=100",
                    {"x-api-key": api_key or "", "anthropic-version": "2023-06-01"},
                )
                return [
                    ModelInfo(id=str(e["id"]), label=str(e.get("display_name") or e["id"]))
                    for e in data.get("data", [])
                    if e.get("id")
                ]
            case "openai":
                return await self._openai_style("https://api.openai.com/v1", api_key)
            case "deepseek":
                return await self._openai_style("https://api.deepseek.com", api_key)
            case "mistral":
                data = await self._get(f"{MISTRAL_BASE_URL}/models", _bearer(api_key))
                return [
                    ModelInfo(id=str(e["id"]), label=str(e.get("name") or e["id"]))
                    for e in data.get("data", [])
                    if e.get("id")
                    and (e.get("capabilities") or {}).get("function_calling", True)
                    and _chat_like(str(e["id"]))
                ]
            case "google":
                data = await self._get(
                    "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                    {"x-goog-api-key": api_key or ""},
                )
                return [
                    ModelInfo(
                        id=str(e["name"]).removeprefix("models/"),
                        label=str(e.get("displayName") or e["name"]),
                    )
                    for e in data.get("models", [])
                    if e.get("name")
                    and "generateContent" in (e.get("supportedGenerationMethods") or [])
                    and _chat_like(str(e["name"]))
                ]
            case "openrouter":
                data = await self._get("https://openrouter.ai/api/v1/models", _bearer(api_key))
                return [
                    ModelInfo(id=str(e["id"]), label=str(e.get("name") or e["id"]))
                    for e in data.get("data", [])
                    if e.get("id") and "tools" in (e.get("supported_parameters") or ["tools"])
                ]
            case "openai_compatible":
                if not base_url:
                    raise CatalogError("no base URL")
                return await self._openai_style(base_url.rstrip("/"), api_key)
        raise CatalogError(f"unknown provider {provider!r}")

    async def _openai_style(self, base_url: str, api_key: str | None) -> list[ModelInfo]:
        data = await self._get(f"{base_url}/models", _bearer(api_key))
        entries = data.get("data") if isinstance(data, dict) else data
        return [
            ModelInfo(id=str(e["id"]), label=str(e["id"]))
            for e in entries or []
            if isinstance(e, dict) and e.get("id") and _chat_like(str(e["id"]))
        ]

    async def _get(self, url: str, headers: dict[str, str]) -> Any:
        try:
            response = await self._http.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise CatalogError(
                f"the model list could not be fetched ({type(exc).__name__})"
            ) from exc
        if response.status_code in (401, 403):
            raise CatalogError("the vendor rejected the key")
        if response.status_code != 200:
            raise CatalogError(f"the vendor answered HTTP {response.status_code} to the model list")
        try:
            return response.json()
        except ValueError as exc:
            raise CatalogError("the vendor's model list is not JSON") from exc


def _bearer(api_key: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _chat_like(model_id: str) -> bool:
    lowered = model_id.lower()
    return not any(word in lowered for word in _NOT_CHAT)
