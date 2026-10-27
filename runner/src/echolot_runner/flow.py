"""The Prefect flow around the harness (docs/PLAN.md M2.1): one flow run per workspace run.

The flow's single parameter is the runner parameters of PLAN 7.2. A run-level problem raises
RunFailed, so the flow run ends FAILED with that message; record errors never fail it. The
harness's log lines go into the flow run's log.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from typing import Any

from prefect import flow, get_run_logger
from prefect.exceptions import MissingContextError

from echolot_runner.harness import run

FLOW_NAME = "run-script"


class _ToRunLogger(logging.Handler):
    """Forwards the harness's log records (``echolot_runner.*``) to the flow run's logger."""

    def __init__(self, target: Any) -> None:
        super().__init__()
        self._target = target

    def emit(self, record: logging.LogRecord) -> None:
        self._target.log(record.levelno, record.getMessage())


@contextlib.contextmanager
def _run_log() -> Iterator[None]:
    try:
        target = get_run_logger()
    except MissingContextError:  # called as run_script.fn(...) outside a flow run (tests)
        yield
        return
    handler = _ToRunLogger(target)
    source = logging.getLogger("echolot_runner")
    source.addHandler(handler)
    source.setLevel(logging.INFO)
    try:
        yield
    finally:
        source.removeHandler(handler)


@flow(name=FLOW_NAME, persist_result=False, retries=0)
def run_script(params: dict[str, Any]) -> dict[str, Any]:
    """Run a workspace script over the records the app serves at ``params["input_url"]``."""
    with _run_log():
        return run(params)
