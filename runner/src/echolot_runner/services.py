"""Services a script may call: configuration from ``services.toml`` and the HTTP client behind
``ctx.service(name)`` (docs/PLAN.md 7.1, 7.7). Keys come from the environment and are only ever
put in request headers; they never appear in errors or logs."""

from __future__ import annotations

import os
import threading
import time
import tomllib
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

BODY_EXCERPT = 500


class ServiceError(Exception):
    """A service call failed after its retries."""

    def __init__(self, service: str, status: int | None, message: str) -> None:
        self.service = service
        self.status = status
        self.message = message
        where = f"{status} from {service}" if status is not None else f"{service} unreachable"
        super().__init__(f"{where}: {message}" if message else where)


class ServicesConfigError(Exception):
    """The services configuration is missing or wrong."""


@dataclass(frozen=True)
class ServiceConfig:
    name: str
    url: str
    auth_header: str | None = None
    key_env: str | None = None
    concurrency: int = 4
    # fewer calls at a time for requests with a query parameter value, "<name>=<value>": n,
    # e.g. {"algorithm=llm_based": 2}; they also count against concurrency
    concurrency_for: dict[str, int] = field(default_factory=dict)
    timeout_s: float = 60.0
    retry_statuses: tuple[int, ...] = (502, 503, 504)
    retries: int = 3
    backoff_s: float = 1.0  # doubled after each attempt
    headers: dict[str, str] = field(default_factory=dict)  # fixed, non-secret headers

    @classmethod
    def from_dict(cls, name: str, raw: dict[str, Any]) -> ServiceConfig:
        if not isinstance(raw.get("url"), str) or not raw["url"]:
            raise ServicesConfigError(f"service {name!r} needs a url")
        known = {f for f in cls.__dataclass_fields__ if f != "name"}
        unknown = set(raw) - known
        if unknown:
            raise ServicesConfigError(f"service {name!r}: unknown keys {sorted(unknown)}")
        values = dict(raw)
        if "retry_statuses" in values:
            values["retry_statuses"] = tuple(int(s) for s in values["retry_statuses"])
        limits = values.get("concurrency_for", {})
        if not isinstance(limits, dict) or not all(
            isinstance(key, str)
            and key.partition("=")[0]
            and key.partition("=")[1]
            and isinstance(n, int)
            and n >= 1
            for key, n in limits.items()
        ):
            raise ServicesConfigError(
                f'service {name!r}: concurrency_for maps "<parameter>=<value>" to a number of '
                'calls, e.g. { "algorithm=llm_based" = 2 }'
            )
        return cls(name=name, **values)

    def key(self) -> str | None:
        if not self.key_env:
            return None
        value = os.environ.get(self.key_env)
        if not value:
            raise ServicesConfigError(
                f"service {self.name!r} needs its key in the environment variable {self.key_env}"
            )
        return value


def load_services(path: Path | str | None = None) -> dict[str, ServiceConfig]:
    """Read ``services.toml`` (``RUNNER_SERVICES_FILE``, default ``./services.toml``). A missing
    file means no services."""
    source = Path(path or os.environ.get("RUNNER_SERVICES_FILE", "services.toml"))
    if not source.is_file():
        return {}
    try:
        raw = tomllib.loads(source.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ServicesConfigError(f"{source}: {exc}") from exc
    return {
        name: ServiceConfig.from_dict(name, table)
        for name, table in raw.items()
        if isinstance(table, dict)
    }


class ServiceClient:
    """Calls one service: base URL, auth header, timeout, retries with backoff, and at most
    ``concurrency`` calls at a time across all of a run's threads."""

    def __init__(self, config: ServiceConfig, http: httpx.Client) -> None:
        self.config = config
        self._http = http
        self._slots = threading.BoundedSemaphore(max(1, config.concurrency))
        self._slots_for = {
            tuple(key.split("=", 1)): threading.BoundedSemaphore(n)
            for key, n in config.concurrency_for.items()
        }
        headers = dict(config.headers)
        key = config.key()
        if key is not None and config.auth_header:
            headers[config.auth_header] = key
        self._headers = headers

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, json: Any = None, params: dict[str, Any] | None = None) -> Any:
        return self.request("POST", path, json=json, params=params)

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = self.config.url.rstrip("/") + "/" + path.lstrip("/")
        query = httpx.URL(url).copy_merge_params(kwargs.get("params") or {}).params
        slots = [  # the narrower limits first, always in the same order, then the service's
            *(s for (k, v), s in self._slots_for.items() if v in query.get_list(k)),
            self._slots,
        ]
        attempts = max(1, self.config.retries)
        last: ServiceError | None = None
        for attempt in range(attempts):
            if attempt:
                time.sleep(self.config.backoff_s * 2 ** (attempt - 1))
            try:
                with ExitStack() as held:
                    for slot in slots:
                        held.enter_context(slot)
                    response = self._http.request(
                        method, url, headers=self._headers, timeout=self.config.timeout_s, **kwargs
                    )
            except httpx.TimeoutException:
                last = ServiceError(
                    self.config.name, None, f"timed out after {self.config.timeout_s:g} s"
                )
                continue
            except httpx.TransportError as exc:
                last = ServiceError(self.config.name, None, type(exc).__name__)
                continue
            if response.status_code in self.config.retry_statuses:
                last = ServiceError(self.config.name, response.status_code, _excerpt(response))
                continue
            if response.status_code >= 400:
                raise ServiceError(self.config.name, response.status_code, _excerpt(response))
            try:
                return response.json()
            except ValueError as exc:
                raise ServiceError(
                    self.config.name, response.status_code, "the reply is not JSON"
                ) from exc
        assert last is not None
        raise ServiceError(
            self.config.name, last.status, f"{last.message} (after {attempts} attempts)"
        )


def _excerpt(response: httpx.Response) -> str:
    text = " ".join(response.text.split())
    return text if len(text) <= BODY_EXCERPT else text[: BODY_EXCERPT - 1] + "…"
