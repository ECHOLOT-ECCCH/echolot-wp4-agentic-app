"""Follows unfinished runs: asks the executor every few seconds and records what changed, and
stops a run that has stalled or gone over its time (docs/PLAN.md M6.3)."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid

import structlog

from echolot_app.db import Database, Run, RunStatus, utcnow
from echolot_app.runs.executor import Executor
from echolot_app.runs.service import refresh_run, stop_run, unfinished_runs

log = structlog.get_logger(__name__)


def duration(seconds: float) -> str:
    """A limit as people read it: '30 minutes', '24 hours', '3 seconds'."""
    if seconds < 120:
        return f"{round(seconds, 1):g} seconds"
    if seconds < 7200:
        return f"{round(seconds / 60, 1):g} minutes"
    return f"{round(seconds / 3600, 1):g} hours"


class Poller:
    """``stall_s``: stop a running run in which no record has finished for this long;
    ``max_s``: stop a run that has been running this long. 0 turns either off."""

    def __init__(
        self,
        db: Database,
        executor: Executor,
        interval_s: float = 3.0,
        *,
        stall_s: float = 0.0,
        max_s: float = 0.0,
    ) -> None:
        self._db = db
        self._executor = executor
        self._interval_s = interval_s
        self.stall_s = stall_s
        self.max_s = max_s
        # run id -> (records done, when that number last changed, monotonic). In memory: the app
        # is one process, and a restart only starts the stall clocks again.
        self._progress: dict[uuid.UUID, tuple[int, float]] = {}
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
        now = time.monotonic()
        running: set[uuid.UUID] = set()
        async with self._db.session() as db:
            for run in await unfinished_runs(db):
                if await refresh_run(db, self._executor, run):
                    changed += 1
                    log.info("run.status", run=str(run.id), status=run.status, error=run.error)
                if run.status != RunStatus.RUNNING.value:
                    continue
                running.add(run.id)
                reason = self.overdue(run, now)
                if reason is not None and await stop_run(self._executor, run, reason):
                    changed += 1
                    log.warning("run.stopped", run=str(run.id), reason=reason)
            await db.commit()
        self._progress = {rid: seen for rid, seen in self._progress.items() if rid in running}
        return changed

    def overdue(self, run: Run, now: float) -> str | None:
        """Why a running run should stop now, or None."""
        done = int((run.progress or {}).get("done") or 0)
        seen = self._progress.get(run.id)
        if seen is None or seen[0] != done:
            seen = self._progress[run.id] = (done, now)
        if self.stall_s > 0 and now - seen[1] >= self.stall_s:
            return f"stopped after {duration(self.stall_s)} without a finished record"
        running_s = (utcnow() - run.started_at).total_seconds() if run.started_at else 0.0
        if self.max_s > 0 and running_s >= self.max_s:
            return f"stopped after running for {duration(self.max_s)}, this server's limit"
        return None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("run.poller_failed")
            await asyncio.sleep(self._interval_s)
