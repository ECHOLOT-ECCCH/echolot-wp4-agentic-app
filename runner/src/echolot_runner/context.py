"""``ctx``: what a script's ``setup`` and ``process`` receive (docs/PLAN.md 7.1)."""

from __future__ import annotations

import logging
from typing import Any

from echolot_runner.services import ServiceClient, ServiceError

log = logging.getLogger("echolot_runner.script")


class Context:
    def __init__(self, clients: dict[str, ServiceClient], params: dict[str, Any]) -> None:
        self._clients = clients
        self.params = params  # the run's script_params
        self.ServiceError = ServiceError  # so a script can catch it without importing

    def service(self, name: str) -> ServiceClient:
        client = self._clients.get(name)
        if client is None:
            allowed = ", ".join(sorted(self._clients)) or "none"
            raise ServiceError(name, None, f"not one of this run's services ({allowed})")
        return client

    def log(self, message: object) -> None:
        log.info("%s", message)
