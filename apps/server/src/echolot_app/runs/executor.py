"""The executor boundary (docs/PLAN.md 7.6) and the mapping of its states to run statuses (7.5)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from echolot_app.db import RunStatus

# Prefect's state types; the local executor reports the same words
ExecKind = Literal[
    "SCHEDULED",
    "PENDING",
    "PAUSED",
    "RUNNING",
    "CANCELLING",
    "COMPLETED",
    "FAILED",
    "CRASHED",
    "CANCELLED",
]


@dataclass(frozen=True)
class ExecState:
    kind: ExecKind
    message: str | None = None


class ExecutorError(Exception):
    """The executor could not take or report the run (unreachable, misconfigured)."""


class Executor(Protocol):
    name: str

    async def submit(self, run_id: str, params: dict[str, Any]) -> str:
        """Start the run; return the executor's id for it."""
        ...

    async def status(self, external_id: str) -> ExecState: ...

    async def cancel(self, external_id: str) -> None: ...

    async def aclose(self) -> None: ...


def app_status(state: ExecState, *, result_received: bool) -> RunStatus:
    """docs/PLAN.md 7.5."""
    match state.kind:
        case "SCHEDULED" | "PENDING" | "PAUSED":
            return RunStatus.QUEUED
        case "RUNNING" | "CANCELLING":
            return RunStatus.RUNNING
        case "COMPLETED":
            return RunStatus.DONE if result_received else RunStatus.FAILED
        case "CANCELLED":
            return RunStatus.CANCELLED
        case _:
            return RunStatus.FAILED
