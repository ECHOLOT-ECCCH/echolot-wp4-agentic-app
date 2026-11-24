"""``ctx``: what a script's ``setup`` and ``process`` receive (docs/PLAN.md 7.1), and the
``ServiceError`` a script may catch. Standard library only: this module is all of the runner a
script's worker process loads besides worker.py and protocol.py (docs/DECISIONS.md 2026-11-24)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any, Protocol

_script_log = logging.getLogger("echolot_runner.script")


class ServiceError(Exception):
    """A service call failed after its retries."""

    def __init__(self, service: str, status: int | None, message: str) -> None:
        self.service = service
        self.status = status
        self.message = message
        where = f"{status} from {service}" if status is not None else f"{service} unreachable"
        super().__init__(f"{where}: {message}" if message else where)


class Client(Protocol):
    def get(self, path: str, params: dict[str, Any] | None = None) -> Any: ...

    def post(self, path: str, json: Any = None, params: dict[str, Any] | None = None) -> Any: ...


class Context:
    def __init__(
        self,
        clients: Mapping[str, Client],
        params: dict[str, Any],
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._clients = clients
        self._log = log or _script_log.info
        self.params = params  # the run's script_params
        self.ServiceError = ServiceError  # so a script can catch it without importing

    def service(self, name: str) -> Client:
        client = self._clients.get(name)
        if client is None:
            allowed = ", ".join(sorted(self._clients)) or "none"
            raise ServiceError(name, None, f"not one of this run's services ({allowed})")
        return client

    def log(self, message: object) -> None:
        self._log(str(message))
