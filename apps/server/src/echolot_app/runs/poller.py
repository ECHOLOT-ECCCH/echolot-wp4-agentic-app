"""Follows unfinished runs: asks the executor every few seconds and records what changed."""

from __future__ import annotations

import asyncio
import contextlib

import structlog

from echolot_app.db import Database
from echolot_app.runs.executor import Executor
from echolot_app.runs.service import refresh_run, unfinished_runs

log = structlog.get_logger(__name__)


class Poller:
    def __init__(self, db: Database, executor: Executor, interval_s: float = 3.0) -> None:
        self._db = db
        self._executor = executor
        self._interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="run-poller")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def tick(self) -> int:
        """One pass; returns how many runs changed."""
        changed = 0
        async with self._db.session() as db:
            for run in await unfinished_runs(db):
                if await refresh_run(db, self._executor, run):
                    changed += 1
                    log.info("run.status", run=str(run.id), status=run.status, error=run.error)
            await db.commit()
        return changed

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("run.poller_failed")
            await asyncio.sleep(self._interval_s)
