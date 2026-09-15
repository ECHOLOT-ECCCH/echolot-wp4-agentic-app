from __future__ import annotations

import httpx
import pytest

from elexifier_agent.catalog import CatalogError, ModelCatalog, curated_registry
from elexifier_agent.models import PROVIDERS, UnsupportedProvider, build_model

OPENROUTER_PAYLOAD = {
    "data": [
        {
            "id": "anthropic/claude-sonnet-5",
            "name": "Anthropic: Claude Sonnet 5",
            "context_length": 200000,
            "architecture": {"input_modalities": ["text", "image"]},
            "supported_parameters": ["tools", "temperature"],
            "pricing": {"prompt": "0.000003", "completion": "0.000015"},
        },
        {
            "id": "some/text-only-model",
            "name": "Text Only",
            "context_length": 32000,
            "architecture": {"input_modalities": ["text"]},
            "supported_parameters": ["temperature"],
            "pricing": {"prompt": "0", "completion": "0"},
        },
    ]
}


def _transport(calls: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        host = request.url.host
        if host == "openrouter.ai":
            return httpx.Response(200, json=OPENROUTER_PAYLOAD)
        if host == "api.openai.com":
            if request.headers.get("authorization") != "Bearer good":
                return httpx.Response(401, json={"error": "bad key"})
            return httpx.Response(
                200, json={"data": [{"id": "gpt-5-mini"}, {"id": "gpt-5"}, {"id": "whisper-1"}]}
            )
        if host == "api.anthropic.com":
            assert request.headers["x-api-key"] == "good"
            return httpx.Response(
                200, json={"data": [{"id": "claude-sonnet-5"}, {"id": "claude-new"}]}
            )
        if host == "generativelanguage.googleapis.com":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/gemini-2.5-pro",
                            "supportedGenerationMethods": ["generateContent"],
                        },
                        {
                            "name": "models/embedding-001",
                            "supportedGenerationMethods": ["embedContent"],
                        },
                        {
                            "name": "models/gemini-x",
                            "displayName": "Gemini X",
                            "inputTokenLimit": 1000,
                            "supportedGenerationMethods": ["generateContent"],
                        },
                    ]
                },
            )
        if host == "vllm.local":
            return httpx.Response(200, json={"data": [{"id": "local/qwen"}]})
        return httpx.Response(500)

    return httpx.MockTransport(handler)


@pytest.fixture
def calls() -> list[httpx.Request]:
    return []


@pytest.fixture
def catalog(calls: list[httpx.Request]) -> ModelCatalog:
    return ModelCatalog(http=httpx.AsyncClient(transport=_transport(calls)))


async def test_openrouter_catalog_parses_capabilities_and_caches(
    catalog: ModelCatalog, calls: list[httpx.Request]
) -> None:
    models = await catalog.list_models("openrouter", "key")
    assert [m.id for m in models] == ["anthropic/claude-sonnet-5", "some/text-only-model"]
    sonnet, text_only = models
    assert sonnet.supports_tools is True and sonnet.supports_vision is True
    assert sonnet.price_in_per_m == 3.0 and sonnet.price_out_per_m == 15.0
    assert sonnet.recommended is True
    assert text_only.supports_tools is False and text_only.supports_vision is False
    await catalog.list_models("openrouter", "key")
    assert len(calls) == 1  # cached
    await catalog.list_models("openrouter", "key", force=True)
    assert len(calls) == 2


async def test_openai_listing_merges_curated_metadata(catalog: ModelCatalog) -> None:
    models = await catalog.list_models("openai", "good")
    ids = [m.id for m in models]
    assert ids[:2] == ["gpt-5", "gpt-5-mini"]  # recommended first, then alphabetical
    assert "whisper-1" in ids
    by_id = {m.id: m for m in models}
    assert by_id["gpt-5"].recommended is True
    assert by_id["whisper-1"].supports_tools is None
    with pytest.raises(CatalogError, match="rejected the API key"):
        await catalog.list_models("openai", "bad")


async def test_anthropic_google_and_compatible(catalog: ModelCatalog) -> None:
    anthropic = await catalog.list_models("anthropic", "good")
    assert {m.id for m in anthropic} == {"claude-sonnet-5", "claude-new"}
    assert next(m for m in anthropic if m.id == "claude-new").supports_vision is True

    google = await catalog.list_models("google", "good")
    assert [m.id for m in google] == ["gemini-2.5-pro", "gemini-x"]
    assert google[1].context_length == 1000

    compatible = await catalog.list_models(
        "openai_compatible", None, "http://vllm.local/v1", {"supports_vision": False}
    )
    assert compatible[0].id == "local/qwen" and compatible[0].supports_vision is False
    with pytest.raises(CatalogError):
        await catalog.list_models("openai_compatible", None, None)


async def test_test_provider_and_unknown(catalog: ModelCatalog) -> None:
    assert (await catalog.list_models("test", None))[0].id == "test"
    with pytest.raises(CatalogError):
        await catalog.list_models("nope", None)


def test_curated_registry_is_well_formed() -> None:
    registry = curated_registry()
    assert set(registry) <= set(PROVIDERS)
    assert any(m.recommended for m in registry["anthropic"])


def test_build_model_variants() -> None:
    assert build_model("openrouter", "x/y", "k").model_name == "x/y"
    assert build_model("openai", "gpt-5", "k").model_name == "gpt-5"
    assert build_model("anthropic", "claude-sonnet-5", "k").model_name == "claude-sonnet-5"
    assert build_model("google", "gemini-2.5-pro", "k").model_name == "gemini-2.5-pro"
    assert build_model("deepseek", "deepseek-chat", "k").model_name == "deepseek-chat"
    assert build_model("openai_compatible", "m", None, "http://vllm.local/v1").model_name == "m"
    with pytest.raises(UnsupportedProvider):
        build_model("openai_compatible", "m", None, None)
    with pytest.raises(UnsupportedProvider):
        build_model("test", "test", None)
    assert build_model("test", "test", None, allow_test_model=True).model_name == "test"
    with pytest.raises(UnsupportedProvider):
        build_model("unknown", "m", "k")
