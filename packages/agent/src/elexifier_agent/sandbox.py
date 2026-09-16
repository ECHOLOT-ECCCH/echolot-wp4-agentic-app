"""Client for the sandbox runner (docs/PLAN.md 7.12)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from elexifier_core.sandbox_contract import RunRequest, RunResponse


class RunnerError(Exception):
    """The runner could not be reached or refused the run; the message is for the model."""


@dataclass
class RunnerClient:
    base_url: str
    token: str
    transport: httpx.AsyncBaseTransport | None = None  # tests plug a MockTransport in
    extra_timeout_s: float = 60.0  # on top of the run's own timeout: image start, upload

    async def run(self, request: RunRequest) -> RunResponse:
        timeout = httpx.Timeout(request.limits.timeout_s + self.extra_timeout_s, connect=10.0)
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, transport=self.transport, timeout=timeout
            ) as client:
                response = await client.post(
                    "/run",
                    json=request.model_dump(),
                    headers={"X-Runner-Token": self.token},
                )
        except httpx.TransportError as exc:
            raise RunnerError(
                f"cannot reach the sandbox runner at {self.base_url} ({type(exc).__name__}); "
                "it must be running with the sandbox image built"
            ) from exc
        if response.status_code >= 400:
            raise RunnerError(
                f"the sandbox runner refused the run ({response.status_code}): {_detail(response)}"
            )
        return RunResponse.model_validate(response.json())

    async def health(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, transport=self.transport, timeout=10.0
            ) as client:
                response = await client.get("/healthz")
        except httpx.TransportError as exc:
            return {"status": "unreachable", "error": type(exc).__name__}
        return dict(response.json()) if response.status_code == 200 else {"status": "error"}


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])[:300]
    return str(body)[:300]


__all__ = ["RunnerClient", "RunnerError"]
