"""Send a tiny completion to verify a credential and model."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from pydantic_ai import Agent
from pydantic_ai.models import Model


@dataclass
class ProbeResult:
    ok: bool
    model_id: str
    latency_ms: int
    message: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    reply: str = ""
    details: dict[str, str] = field(default_factory=dict)


async def probe_model(model: Model, model_id: str, timeout_s: float = 30.0) -> ProbeResult:
    agent = Agent(model, instructions="Reply with the single word OK.")
    started = time.monotonic()
    try:
        result = await agent.run("Say OK.", model_settings={"max_tokens": 5, "timeout": timeout_s})
    except Exception as exc:
        return ProbeResult(
            ok=False,
            model_id=model_id,
            latency_ms=int((time.monotonic() - started) * 1000),
            message=_safe_error(exc),
        )
    usage = result.usage
    return ProbeResult(
        ok=True,
        model_id=model_id,
        latency_ms=int((time.monotonic() - started) * 1000),
        message="The model answered.",
        input_tokens=usage.input_tokens or 0,
        output_tokens=usage.output_tokens or 0,
        reply=str(result.output)[:200],
    )


def _safe_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    # never echo anything that looks like a key
    for marker in ("sk-", "Bearer ", "key="):
        if marker in text:
            head = text.split(marker)[0]
            return f"{head}{marker}***"
    return text[:500]
