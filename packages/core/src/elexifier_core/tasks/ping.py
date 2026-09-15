"""Smoke-test task used by the compose stack and tests."""

from __future__ import annotations

import structlog

from elexifier_core.jobs import QUEUE_DEFAULT, app

log = structlog.get_logger(__name__)


@app.task(name="core.ping", queue=QUEUE_DEFAULT)
async def ping(message: str = "pong") -> str:
    log.info("ping", message=message)
    return message
