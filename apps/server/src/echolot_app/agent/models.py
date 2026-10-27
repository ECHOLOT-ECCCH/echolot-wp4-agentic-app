"""The agent's model, built from the ``ECHO_MODEL_*`` settings (docs/PLAN.md section 3)."""

from __future__ import annotations

from typing import Literal, get_args

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

from echolot_app.settings import Settings

ProviderName = Literal[
    "openrouter", "openai", "anthropic", "google", "deepseek", "openai_compatible", "test"
]
PROVIDER_NAMES: tuple[str, ...] = get_args(ProviderName)


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
                    api_key=api_key, app_title="ECHOLOT Workbench", app_url=app_url or None
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


def model_from_settings(settings: Settings) -> Model:
    """The configured model; the key is read from the settings here and nowhere else."""
    key = settings.model_api_key.get_secret_value() if settings.model_api_key else None
    return build_model(
        settings.model_provider,
        settings.model_id,
        key,
        settings.model_base_url,
        app_url=settings.public_url,
        allow_test_model=settings.enable_test_model,
    )


def model_label(settings: Settings) -> dict[str, str]:
    return {"provider": settings.model_provider, "model_id": settings.model_id}
