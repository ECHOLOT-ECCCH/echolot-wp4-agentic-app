"""The script runs in a worker process (docs/DECISIONS.md 2026-11-24): here the child-process
worker, which the sandbox's workers share their code with (test_sandbox.py)."""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from echolot_runner.harness import RunFailed, run
from echolot_runner.services import ServiceConfig

from .conftest import TOKEN, FakeWorld, params, service

# the worker's own end of the connection, for scripts that pretend to be the worker
FD = 'import os, sys\nFD = int(sys.argv[sys.argv.index("--fd") + 1])\n'


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"path": request.url.path})


def test_the_script_sees_nothing_of_the_runners_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECHOLOT_API_KEY", "s3cret-key")
    monkeypatch.setenv("PREFECT_API_AUTH_STRING", "admin:s3cret-pass")
    # what the script looks for is written backwards and turned round only after the snapshot,
    # so that the script's own source, which the worker holds, does not count as a leak
    needles = ["s3cret"[::-1], TOKEN[::-1], "app.test"[::-1]]
    script = (
        "import gc, os\n"
        "def process(record, ctx):\n"
        "    seen = [repr(o) for o in gc.get_objects() if isinstance(o, (dict, list, tuple))]\n"
        "    text = ' '.join(seen) + ' '.join(os.environ.values())\n"
        f"    leaks = [n[::-1] for n in {needles!r} if n[::-1] in text]\n"
        "    return {'env': sorted(os.environ), 'leaks': leaks}\n"
    )
    world = FakeWorld(records=[{"a": 1}])
    run(params(script), http=world.client(), services={})
    [line] = world.lines()
    assert line["_status"] == "ok", line["_error"]
    assert set(line["env"]) <= {
        "PATH",
        "LANG",
        "HOME",
        "TMPDIR",
        "LC_CTYPE",
        "__CF_USER_TEXT_ENCODING",
    }
    assert line["leaks"] == []


def test_print_and_ctx_log_reach_the_run_log(caplog: pytest.LogCaptureFixture) -> None:
    script = (
        "def setup(ctx):\n    print('loading the table')\n"
        "def process(record, ctx):\n    ctx.log(f'row with {record[\"a\"]}')\n    return {}\n"
    )
    world = FakeWorld(records=[{"a": 1}])
    with caplog.at_level(logging.INFO, logger="echolot_runner.script"):
        run(params(script), http=world.client(), services={})
    messages = [r.getMessage() for r in caplog.records if r.name == "echolot_runner.script"]
    assert "loading the table" in messages and "row with 1" in messages


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("os._exit(3)", r"the script's process ended unexpectedly \(exit code 3\)"),
        ("os.kill(os.getpid(), 9)", r"killed \(signal 9\), most likely for using more memory"),
        ("os.write(FD, b'not json\\n')", "the script's process sent something unreadable"),
        (
            'os.write(FD, b\'{"t": "result", "line": {"_row": 999, "_status": "ok"}}\\n\')',
            "the script's process sent a message out of turn",
        ),
    ],
)
def test_a_worker_that_dies_or_lies_fails_the_run(body: str, message: str) -> None:
    script = FD + f"def process(record, ctx):\n    {body}\n    import time; time.sleep(5)\n"
    world = FakeWorld(records=[{"a": 1}, {"a": 2}])
    with pytest.raises(RunFailed, match=message):
        run(params(script), http=world.client(), services={})
    assert world.result is None


@pytest.mark.parametrize(
    ("base", "path"),
    [
        ("http://echo.test/api", "/../admin"),
        ("http://echo.test/api", "/%2e%2e/admin"),
        ("http://echo.test/api", "/x/%2E%2E/%2e%2e/admin"),
    ],
)
def test_a_path_cannot_lead_out_of_the_service(base: str, path: str) -> None:
    script = f'def process(record, ctx):\n    return ctx.service("echo").get({path!r})\n'
    configs = {"echo": ServiceConfig(name="echo", url=base, backoff_s=0)}
    world = FakeWorld(records=[{"a": 1}], services={"echo.test": ok})
    run(params(script, services=["echo"]), http=world.client(), services=configs)
    assert world.lines()[0]["_error"] == (
        "ServiceError: echo unreachable: the path leads out of the service"
    )


def test_a_path_under_the_service_works() -> None:
    script = (
        'def process(record, ctx):\n    return ctx.service("echo").get("/a/b", params={"q": 1})\n'
    )
    world = FakeWorld(records=[{"a": 1}], services={"echo.test": ok})
    run(params(script, services=["echo"]), http=world.client(), services=service())
    assert world.lines()[0]["path"] == "/a/b"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('ctx.service("echo").post("/", json={1, 2})', "TypeError: Object of type set"),
        ("raise SystemExit(3)", "SystemExit: 3"),
        ("return {(1, 2): 'tuple key'}", "the result is not JSON"),
    ],
)
def test_script_mistakes_fail_only_their_record(body: str, message: str) -> None:
    script = (
        f"def process(record, ctx):\n    if record['a'] == 1:\n        {body}\n    return {{}}\n"
    )
    world = FakeWorld(records=[{"a": 1}, {"a": 2}], services={"echo.test": ok})
    summary = run(params(script, services=["echo"]), http=world.client(), services=service())
    assert summary["ok"] == 1 and summary["error"] == 1
    first, second = world.lines()
    assert message in first["_error"] and second["_status"] == "ok"


def test_module_state_lasts_for_the_whole_run() -> None:
    script = (
        "SEEN = []\n"
        "def process(record, ctx):\n    SEEN.append(record['a'])\n    return {'seen': len(SEEN)}\n"
    )
    world = FakeWorld(records=[{"a": i} for i in range(5)])
    run(params(script, concurrency=1), http=world.client(), services={})
    assert [line["seen"] for line in world.lines()] == [1, 2, 3, 4, 5]
    assert json.loads(json.dumps(world.progress[-1]))["done"] == 5
