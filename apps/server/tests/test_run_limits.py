"""The poller stops a run that has stalled or gone over its time (docs/PLAN.md M6.3). The live
runs go through the LocalExecutor, whose cancel ends the runner subprocess like Prefect's does."""

from __future__ import annotations

import time

from echolot_app.agent.agent import run_limits, workspace_block
from echolot_app.db import Run
from echolot_app.runs.executor import ExecutorError
from echolot_app.runs.poller import duration
from echolot_app.runs.service import stop_run

from .conftest import Live, Workspace

SLEEPY = "import time\n\ndef process(record, ctx):\n    time.sleep({s})\n    return {{}}\n"


async def test_a_stalled_run_is_stopped(live: Live) -> None:
    live.state.poller.stall_s = 3.0
    await live.script(SLEEPY.format(s=60))
    started = time.monotonic()

    final = await live.wait((await live.run(limit=2, concurrency=1))["id"], timeout_s=30)

    assert final["status"] == "failed"
    assert final["error"] == "stopped after 3 seconds without a finished record"
    assert final["result_file_id"] is None
    assert time.monotonic() - started < 20  # the subprocess was ended, not waited for


async def test_a_run_that_keeps_finishing_records_is_not_stopped(live: Live) -> None:
    # progress reaches the app every 2 s while records finish (the harness's PROGRESS_EVERY_S)
    live.state.poller.stall_s = 4.5
    await live.script(SLEEPY.format(s=0.4))

    final = await live.wait((await live.run(limit=16, concurrency=1))["id"], timeout_s=60)

    assert final["status"] == "done", final["error"]
    assert final["counts"]["ok"] == 16


async def test_a_run_over_its_time_is_stopped(live: Live) -> None:
    live.state.poller.max_s = 2.0
    await live.script(SLEEPY.format(s=0.3))

    final = await live.wait((await live.run(limit=40, concurrency=1))["id"], timeout_s=30)

    assert final["status"] == "failed"
    assert final["error"] == "stopped after running for 2 seconds, this server's limit"
    assert final["result_file_id"] is None


class RefusesOnce:
    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def cancel(self, external_id: str) -> None:
        self.cancelled.append(external_id)
        if len(self.cancelled) == 1:
            raise ExecutorError("the Prefect server is not answering")


async def test_a_stop_the_executor_refuses_is_tried_again() -> None:
    executor = RefusesOnce()
    run = Run(status="running", external_id="flow-run-1")

    assert not await stop_run(executor, run, "stopped")  # type: ignore[arg-type]
    assert run.status == "running" and run.finished_at is None

    assert await stop_run(executor, run, "stopped")  # type: ignore[arg-type]
    assert run.status == "failed" and run.error == "stopped" and run.finished_at is not None
    assert executor.cancelled == ["flow-run-1", "flow-run-1"]


def test_limits_in_words() -> None:
    assert duration(3) == "3 seconds"
    assert duration(1800) == "30 minutes"
    assert duration(24 * 3600) == "24 hours"
    assert duration(90 * 60) == "90 minutes"
    assert run_limits(1800, 24 * 3600) == (
        "after 24 hours, or when no record has finished for 30 minutes"
    )
    assert run_limits(1800, 0) == "when no record has finished for 30 minutes"
    assert run_limits(0, 0) == ""


async def test_the_agent_knows_the_limits(workspace: Workspace) -> None:
    block = await workspace_block(workspace.deps)
    assert (
        "- Runs on this server stop after 24 hours, or when no record has finished for "
        "30 minutes: a full run has to fit"
    ) in block
