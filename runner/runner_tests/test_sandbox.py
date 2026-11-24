"""The sandbox's supervisor, started as an ordinary user (its development mode: the workers run
as that user). The uids of their own, the missing network and the capabilities are checked in
Docker by deploy/smoke.sh."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from echolot_runner.harness import RunFailed, run
from echolot_runner.sandbox import processes_of

from .conftest import FakeWorld, params, service
from .test_worker import ok


@pytest.fixture
def sandbox() -> Iterator[str]:
    folder = tempfile.mkdtemp(prefix="sbx-", dir="/tmp")  # a Unix socket's path must be short
    path = os.path.join(folder, "sandbox.sock")
    supervisor = subprocess.Popen(
        [sys.executable, "-m", "echolot_runner.sandbox"],
        env={**os.environ, "SANDBOX_SOCKET": path},
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not os.path.exists(path):
            assert supervisor.poll() is None, supervisor.stderr and supervisor.stderr.read()
            assert time.monotonic() < deadline, "the supervisor did not listen"
            time.sleep(0.05)
        yield path
        assert supervisor.poll() is None, "the supervisor ended during the test"
    finally:
        supervisor.terminate()
        supervisor.wait(timeout=10)
        shutil.rmtree(folder, ignore_errors=True)


SCRIPT = 'def process(record, ctx):\n    return ctx.service("echo").get(f"/{record["a"]}")\n'


def test_runs_go_through_the_sandbox(sandbox: str) -> None:
    for _ in range(2):  # the supervisor serves one run after another
        world = FakeWorld(records=[{"a": i} for i in range(50)], services={"echo.test": ok})
        summary = run(
            params(SCRIPT, services=["echo"], concurrency=8),
            http=world.client(),
            services=service(),
            sandbox=sandbox,
        )
        assert summary["ok"] == 50
        assert [line["path"] for line in world.lines()][:3] == ["/0", "/1", "/2"]


def test_the_sandbox_says_how_a_worker_ended(sandbox: str) -> None:
    script = "import os\ndef process(record, ctx):\n    os.kill(os.getpid(), 9)\n"
    world = FakeWorld(records=[{"a": 1}])
    with pytest.raises(RunFailed, match=r"killed \(signal 9\)"):
        run(params(script), http=world.client(), services={}, sandbox=sandbox)
    world = FakeWorld(records=[{"a": 1}], services={"echo.test": ok})
    summary = run(
        params(SCRIPT, services=["echo"]), http=world.client(), services=service(), sandbox=sandbox
    )
    assert summary["ok"] == 1


def test_a_run_that_is_stopped_ends_its_worker(sandbox: str) -> None:
    """The harness is killed mid-run (a cancel): the worker must not live on."""
    child = subprocess.run(
        [sys.executable, "-c", HARNESS_KILLED_MID_RUN, sandbox],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert child.returncode == -9, child.stderr  # it killed itself, as a cancel does
    pid = int(child.stdout.strip())
    deadline = time.monotonic() + 10
    while _alive(pid):
        assert time.monotonic() < deadline, f"worker {pid} still runs"
        time.sleep(0.1)


def test_without_a_sandbox_the_run_fails_clearly() -> None:
    world = FakeWorld(records=[{"a": 1}])
    with pytest.raises(RunFailed, match="the script sandbox does not answer"):
        run(params(SCRIPT), http=world.client(), services={}, sandbox="/tmp/no-such.sock")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # a zombie is not running any more
    status = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return bool(status.stdout.strip()) and not status.stdout.strip().startswith("Z")


# starts a run whose script reports its pid and then sleeps, and kills itself (the harness) once
# the pid has come; prints the worker's pid
HARNESS_KILLED_MID_RUN = """
import os, signal, sys, threading
sys.path.insert(0, "runner")
from runner_tests.conftest import FakeWorld, params
from echolot_runner import harness

script = (
    "import os, time\\n"
    "def process(record, ctx):\\n    ctx.log(f'pid {os.getpid()}')\\n    time.sleep(60)\\n"
)

class Grab:
    def log(self, level, fmt, message):
        if message.startswith("pid "):
            print(message[4:], flush=True)
            os.kill(os.getpid(), signal.SIGKILL)

harness.script_log = Grab()
world = FakeWorld(records=[{"a": 1}])
harness.run(params(script), http=world.client(), services={}, sandbox=sys.argv[1])
"""


def test_a_killed_worker_waiting_to_be_collected_does_not_count(tmp_path: Path) -> None:
    def process(pid: int, state: str, uid: int) -> None:
        (tmp_path / str(pid)).mkdir()
        status = f"Name:\tpython\nState:\t{state}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n"
        (tmp_path / str(pid) / "status").write_text(status)

    process(10, "S (sleeping)", 20001)
    process(11, "Z (zombie)", 20001)
    process(12, "R (running)", 20002)
    (tmp_path / "self").mkdir()
    assert processes_of(20001, proc=str(tmp_path)) == [10]
    assert processes_of(20003, proc=str(tmp_path)) == []
