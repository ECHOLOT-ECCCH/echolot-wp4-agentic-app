"""Send a tiny request to check that a key and a model work."""

from __future__ import annotations

import re
import time

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models import Model


class ProbeResult(BaseModel):
    ok: bool
    model_id: str
    latency_ms: int
    message: str


async def probe_model(model: Model, model_id: str, timeout_s: float = 30.0) -> ProbeResult:
    agent = Agent(model, instructions="Reply with the single word OK.")
    started = time.monotonic()
    try:
        await agent.run("Say OK.", model_settings={"max_tokens": 16, "timeout": timeout_s})
    except Exception as exc:
        return ProbeResult(
            ok=False,
            model_id=model_id,
            latency_ms=int((time.monotonic() - started) * 1000),
            message=safe_error(exc),
        )
    return ProbeResult(
        ok=True,
        model_id=model_id,
        latency_ms=int((time.monotonic() - started) * 1000),
        message="The model answered.",
    )


_KEYISH = re.compile(r"(sk-[\w\-]{4,}|AIza[\w\-]{8,}|Bearer\s+\S+|key=\S+|[A-Za-z0-9_\-]{32,})")


def safe_error(exc: BaseException) -> str:
    """The error for the user, with anything that looks like a key masked."""
    return _KEYISH.sub("***", f"{type(exc).__name__}: {exc}")[:500]
