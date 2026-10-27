"""Runner test fixtures: a fake app (input, progress, result) and fake services, both behind
one httpx.MockTransport."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from echolot_runner.services import ServiceConfig

TOKEN = "run-token"
APP = "http://app.test"

Handler = Callable[[httpx.Request], httpx.Response]


@dataclass
class FakeWorld:
    records: list[dict[str, Any]]
    services: dict[str, Handler] = field(default_factory=dict)
    progress: list[dict[str, int]] = field(default_factory=list)
    result: bytes | None = None
    input_status: int = 200
    result_status: int = 204

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "app.test":
            if request.headers.get("authorization") != f"Bearer {TOKEN}":
                return httpx.Response(404)
            path = request.url.path
            if path.endswith("/input"):
                body = "".join(
                    json.dumps({"_row": i, "record": r}) + "\n" for i, r in enumerate(self.records)
                )
                return httpx.Response(self.input_status, content=body.encode())
            if path.endswith("/progress"):
                self.progress.append(json.loads(request.content))
                return httpx.Response(204)
            if path.endswith("/result"):
                self.result = request.read()
                return httpx.Response(self.result_status)
        service = self.services.get(request.url.host)
        if service is None:
            return httpx.Response(599)
        return service(request)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def lines(self) -> list[dict[str, Any]]:
        assert self.result is not None
        return [json.loads(line) for line in self.result.decode().splitlines()]


def params(script: str, **extra: Any) -> dict[str, Any]:
    return {
        "run_id": "r1",
        "script": script,
        "input_url": f"{APP}/runner/runs/r1/input",
        "progress_url": f"{APP}/runner/runs/r1/progress",
        "result_url": f"{APP}/runner/runs/r1/result",
        "token": TOKEN,
        **extra,
    }


def service(name: str = "echo", **extra: Any) -> dict[str, ServiceConfig]:
    return {name: ServiceConfig(name=name, url=f"http://{name}.test", backoff_s=0, **extra)}


class InFlight:
    """Counts concurrent calls to a fake service."""

    def __init__(self, delay_s: float = 0.02) -> None:
        self.delay_s = delay_s
        self.current = 0
        self.peak = 0
        self._lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            self.current += 1
            self.peak = max(self.peak, self.current)
        time.sleep(self.delay_s)
        with self._lock:
            self.current -= 1
        return httpx.Response(200, json={"echo": json.loads(request.content or b"null")})


@pytest.fixture
def people() -> list[dict[str, Any]]:
    return [{"name": f"Person {i}"} for i in range(1000)]
