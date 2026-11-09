"""Model vendors and the agent's model (docs/PLAN.md 7.11).

Every user picks a vendor and a model. The key is either the user's own (stored encrypted) or,
for "Server default", the one in the ``ECHO_MODEL_*`` settings.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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

MISTRAL_BASE_URL = "https://api.mistral.ai/v1"


@dataclass(frozen=True)
class Suggested:
    id: str
    label: str


@dataclass(frozen=True)
class Vendor:
    name: str
    label: str
    needs_key: bool = True
    needs_base_url: bool = False
    key_url: str = ""
    # shown first and offered when the vendor's own model listing is unavailable
    suggested: tuple[Suggested, ...] = field(default=())


VENDORS: dict[str, Vendor] = {
    v.name: v
    for v in (
        Vendor(
            "anthropic",
            "Anthropic (Claude)",
            key_url="https://console.anthropic.com/settings/keys",
            suggested=(
                Suggested("claude-sonnet-5-5", "Claude Sonnet 5.5"),
                Suggested("claude-opus-5-5", "Claude Opus 5.5"),
                Suggested("claude-haiku-5-5", "Claude Haiku 5.5"),
            ),
        ),
        Vendor(
            "openai",
            "OpenAI",
            key_url="https://platform.openai.com/api-keys",
            suggested=(Suggested("gpt-5", "GPT-5"), Suggested("gpt-5-mini", "GPT-5 mini")),
        ),
        Vendor(
            "google",
            "Google Gemini",
            key_url="https://aistudio.google.com/apikey",
            suggested=(
                Suggested("gemini-2.5-pro", "Gemini 2.5 Pro"),
                Suggested("gemini-2.5-flash", "Gemini 2.5 Flash"),
            ),
        ),
        Vendor(
            "mistral",
            "Mistral AI",
            key_url="https://console.mistral.ai/api-keys",
            suggested=(
                Suggested("mistral-large-latest", "Mistral Large"),
                Suggested("mistral-medium-latest", "Mistral Medium"),
                Suggested("mistral-small-latest", "Mistral Small"),
            ),
        ),
        Vendor(
            "openrouter",
            "OpenRouter (many vendors, one key)",
            key_url="https://openrouter.ai/keys",
            suggested=(
                Suggested("anthropic/claude-sonnet-5.5", "Claude Sonnet 5.5"),
                Suggested("openai/gpt-5", "GPT-5"),
                Suggested("google/gemini-2.5-pro", "Gemini 2.5 Pro"),
                Suggested("mistralai/mistral-large", "Mistral Large"),
            ),
        ),
        Vendor(
            "deepseek",
            "DeepSeek",
            key_url="https://platform.deepseek.com/api_keys",
            suggested=(Suggested("deepseek-chat", "DeepSeek Chat"),),
        ),
        Vendor(
            "openai_compatible",
            "OpenAI-compatible endpoint (vLLM, Ollama, LM Studio, …)",
            needs_key=False,
            needs_base_url=True,
        ),
        Vendor(
            "test",
            "Test model (development only)",
            needs_key=False,
            suggested=(Suggested("test", "Test model (canned answers)"),),
        ),
    )
}


class UnsupportedProvider(ValueError):
    pass


def vendor_enabled(settings: Settings, name: str) -> bool:
    """Whether users may pick this vendor on this server."""
    if name not in VENDORS:
        return False
    if name == "test":
        return settings.enable_test_model
    if name == "openai_compatible":
        # a user-supplied URL makes the server call addresses of the user's choosing
        return settings.allow_custom_endpoints
    return True


def build_model(
    provider: str,
    model_id: str,
    api_key: str | None,
    base_url: str | None = None,
    *,
    app_url: str = "",
    allow_test_model: bool = False,
) -> Model:
    """Instantiate a Pydantic AI model. The API key is only held by the returned object.

    Vendors that need a key get it explicitly: none may fall back to a key in this process's
    environment (the SDKs read e.g. OPENAI_API_KEY when given None)."""
    vendor = VENDORS.get(provider)
    if vendor is None:
        raise UnsupportedProvider(f"unknown provider {provider!r}")
    if vendor.needs_key and not api_key:
        raise UnsupportedProvider(f"{vendor.label} needs an API key")
    if not model_id.strip():
        raise UnsupportedProvider("no model chosen")
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
        case "mistral":
            # Mistral's chat API is OpenAI-compatible, tools included; no extra SDK needed
            return OpenAIChatModel(
                model_id, provider=OpenAIProvider(base_url=MISTRAL_BASE_URL, api_key=api_key)
            )
        case "deepseek":
            return OpenAIChatModel(model_id, provider=DeepSeekProvider(api_key=api_key))
        case "openai_compatible":
            if not base_url:
                raise UnsupportedProvider("an OpenAI-compatible endpoint needs a base URL")
            return OpenAIChatModel(
                model_id,
                provider=OpenAIProvider(base_url=base_url, api_key=api_key or "not-needed"),
            )
        case "test":
            if not allow_test_model:
                raise UnsupportedProvider("the test provider is disabled")
            return TestModel()
    raise UnsupportedProvider(f"unknown provider {provider!r}")


def server_model_available(settings: Settings) -> bool:
    """Whether the ``ECHO_MODEL_*`` model can be offered to users as "Server default"."""
    vendor = VENDORS.get(settings.model_provider)
    if vendor is None:
        return False
    if settings.model_provider == "test":
        return settings.enable_test_model
    if vendor.needs_base_url and not settings.model_base_url:
        return False
    return not vendor.needs_key or settings.model_api_key is not None


def server_model(settings: Settings) -> Model:
    """The server's model; its key is read from the settings here and nowhere else."""
    key = settings.model_api_key.get_secret_value() if settings.model_api_key else None
    return build_model(
        settings.model_provider,
        settings.model_id,
        key,
        settings.model_base_url,
        app_url=settings.public_url,
        allow_test_model=settings.enable_test_model,
    )
