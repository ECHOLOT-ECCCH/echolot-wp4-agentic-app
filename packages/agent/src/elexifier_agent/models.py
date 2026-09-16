"""Model registry: turn a stored credential plus a model id into a Pydantic AI model (PLAN 7.4)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

from pydantic import BaseModel
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

ProviderName = Literal[
    "openrouter", "openai", "anthropic", "google", "deepseek", "openai_compatible", "test"
]
PROVIDER_NAMES: tuple[str, ...] = get_args(ProviderName)


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    label: str
    needs_api_key: bool
    needs_base_url: bool
    docs_url: str
    default_probe_model: str | None


PROVIDERS: dict[str, ProviderInfo] = {
    "openrouter": ProviderInfo(
        "openrouter", "OpenRouter", True, False, "https://openrouter.ai/keys", "openai/gpt-5-mini"
    ),
    "openai": ProviderInfo(
        "openai", "OpenAI", True, False, "https://platform.openai.com/api-keys", "gpt-5-mini"
    ),
    "anthropic": ProviderInfo(
        "anthropic", "Anthropic", True, False, "https://console.anthropic.com/", "claude-sonnet-5"
    ),
    "google": ProviderInfo(
        "google", "Google AI", True, False, "https://aistudio.google.com/apikey", None
    ),
    "deepseek": ProviderInfo(
        "deepseek", "DeepSeek", True, False, "https://platform.deepseek.com/", "deepseek-chat"
    ),
    "openai_compatible": ProviderInfo(
        "openai_compatible",
        "OpenAI-compatible endpoint (vLLM, Ollama, LM Studio, ...)",
        False,
        True,
        "",
        None,
    ),
    "test": ProviderInfo("test", "Test model (development only)", False, False, "", "test"),
}


class ModelInfo(BaseModel):
    id: str
    label: str
    provider: str
    supports_tools: bool | None = None  # None = unknown
    supports_vision: bool | None = None
    context_length: int | None = None
    price_in_per_m: float | None = None  # USD per million input tokens
    price_out_per_m: float | None = None
    recommended: bool = False


class ModelSpec(BaseModel):
    """What a project or a request selects: which credential and which model id."""

    provider: str
    model_id: str
    credential_id: str | None = None


class UnsupportedProvider(ValueError):
    pass


def build_model(
    provider: str,
    model_id: str,
    api_key: str | None,
    base_url: str | None = None,
    *,
    app_url: str = "",
    allow_test_model: bool = False,
) -> Model:
    """Instantiate a Pydantic AI model. The API key is only held by the returned object."""
    match provider:
        case "openrouter":
            return OpenRouterModel(
                model_id,
                provider=OpenRouterProvider(
                    api_key=api_key, app_title="Elexifier", app_url=app_url or None
                ),
            )
        case "openai":
            return OpenAIResponsesModel(model_id, provider=OpenAIProvider(api_key=api_key))
        case "anthropic":
            # Long tool-heavy conversations resend the whole history every request; prompt
            # caching (system prompt, tool definitions, moving breakpoint) cuts that cost.
            return AnthropicModel(
                model_id,
                provider=AnthropicProvider(api_key=api_key),
                settings=AnthropicModelSettings(
                    anthropic_cache=True,
                    anthropic_cache_instructions=True,
                    anthropic_cache_tool_definitions=True,
                ),
            )
        case "google":
            return GoogleModel(model_id, provider=GoogleProvider(api_key=api_key))
        case "deepseek":
            return OpenAIChatModel(model_id, provider=DeepSeekProvider(api_key=api_key))
        case "openai_compatible":
            if not base_url:
                raise UnsupportedProvider("openai_compatible requires a base_url")
            return OpenAIChatModel(
                model_id,
                provider=OpenAIProvider(base_url=base_url, api_key=api_key or "not-needed"),
            )
        case "test":
            if not allow_test_model:
                raise UnsupportedProvider("the test provider is disabled")
            return TestModel()
    raise UnsupportedProvider(f"unknown provider {provider!r}")
