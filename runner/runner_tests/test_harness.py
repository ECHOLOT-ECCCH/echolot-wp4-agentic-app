from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from echolot_runner.harness import RunFailed, run

from .conftest import FakeWorld, InFlight, params, service

ECHO = """
def process(record, ctx):
    reply = ctx.service("echo").post("/upper", json={"name": record["name"]})
    return {"upper": reply["name"].upper()}
"""


def upper(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=json.loads(request.content))


def test_a_thousand_records_in_row_order(people: list[dict[str, Any]]) -> None:
    world = FakeWorld(records=people, services={"echo.test": upper})
    summary = run(
        params(ECHO, services=["echo"], concurrency=8), http=world.client(), services=service()
    )
    assert summary["total"] == 1000 and summary["ok"] == 1000 and summary["error"] == 0
    lines = world.lines()
    assert [line["_row"] for line in lines] == list(range(1000))
    assert lines[7] == {
        "_row": 7,
        "_status": "ok",
        "_error": None,
        "upper": "PERSON 7",
        "_ms": lines[7]["_ms"],
    }
    assert world.progress[-1] == {"done": 1000, "total": 1000, "ok": 1000, "error": 0}


def test_limit_cuts_the_run(people: list[dict[str, Any]]) -> None:
    world = FakeWorld(records=people, services={"echo.test": upper})
    summary = run(
        params(ECHO, services=["echo"], limit=20), http=world.client(), services=service()
    )
    assert summary["total"] == 20 and len(world.lines()) == 20


def test_retries_then_success_or_a_record_error() -> None:
    attempts: dict[str, int] = {}

    def flaky(request: httpx.Request) -> httpx.Response:
        name = json.loads(request.content)["name"]
        attempts[name] = attempts.get(name, 0) + 1
        if name == "always" or attempts[name] < 3:
            return httpx.Response(502, text="Wikifier timed out")
        return httpx.Response(200, json={"name": name})

    world = FakeWorld(
        records=[{"name": "flaky"}, {"name": "always"}], services={"echo.test": flaky}
    )
    summary = run(params(ECHO, services=["echo"]), http=world.client(), services=service())
    assert summary["ok"] == 1 and summary["error"] == 1
    ok, failed = world.lines()
    assert ok["upper"] == "FLAKY" and attempts["flaky"] == 3
    assert failed["_status"] == "error"
    assert failed["_error"] == (
        "ServiceError: 502 from echo: Wikifier timed out (after 3 attempts)"
    )


def test_a_client_error_is_not_retried() -> None:
    calls = 0

    def unauthorized(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"detail": "bad key"})

    world = FakeWorld(records=[{"name": "x"}], services={"echo.test": unauthorized})
    run(params(ECHO, services=["echo"]), http=world.client(), services=service())
    assert calls == 1
    assert world.lines()[0]["_error"].startswith("ServiceError: 401 from echo:")


def test_one_failing_record_among_many(people: list[dict[str, Any]]) -> None:
    script = ECHO.replace(
        "def process(record, ctx):\n",
        "def process(record, ctx):\n"
        '    if record["name"] == "Person 500":\n'
        '        raise ValueError("no name for this one")\n',
    )
    world = FakeWorld(records=people, services={"echo.test": upper})
    summary = run(params(script, services=["echo"]), http=world.client(), services=service())
    assert summary["ok"] == 999 and summary["error"] == 1
    assert world.lines()[500]["_error"] == "ValueError: no name for this one"
    assert "_traceback" not in world.lines()[500]


def test_service_concurrency_caps_the_run() -> None:
    gauge = InFlight()
    script = 'def process(record, ctx):\n    return ctx.service("echo").post("/", json=1)\n'
    world = FakeWorld(records=[{"i": i} for i in range(60)], services={"echo.test": gauge})
    run(
        params(script, services=["echo"], concurrency=16),
        http=world.client(),
        services=service(concurrency=3),
    )
    assert gauge.peak == 3


def test_a_service_outside_the_run_fails_that_record() -> None:
    script = 'def process(record, ctx):\n    return ctx.service("other").get("/")\n'
    world = FakeWorld(records=[{"a": 1}], services={"echo.test": upper})
    run(params(script, services=["echo"]), http=world.client(), services=service())
    assert world.lines()[0]["_error"] == (
        "ServiceError: other unreachable: not one of this run's services (echo)"
    )


def test_a_service_missing_on_the_host_fails_the_run() -> None:
    world = FakeWorld(records=[{"a": 1}])
    with pytest.raises(RunFailed, match="'echolot' is not configured on this runner host"):
        run(params(ECHO, services=["echolot"]), http=world.client(), services=service())


def test_a_missing_key_fails_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ECHO_TEST_KEY", raising=False)
    world = FakeWorld(records=[{"a": 1}])
    with pytest.raises(RunFailed, match="environment variable ECHO_TEST_KEY"):
        run(
            params(ECHO, services=["echo"]),
            http=world.client(),
            services=service(auth_header="X-API-Key", key_env="ECHO_TEST_KEY"),
        )


def test_the_key_goes_in_the_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECHO_TEST_KEY", "s3cret")
    seen: list[str | None] = []

    def keyed(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("x-api-key"))
        return httpx.Response(200, json={"name": "a"})

    world = FakeWorld(records=[{"name": "a"}], services={"echo.test": keyed})
    run(
        params(ECHO, services=["echo"]),
        http=world.client(),
        services=service(auth_header="X-API-Key", key_env="ECHO_TEST_KEY"),
    )
    assert seen == ["s3cret"]
    assert "s3cret" not in (world.result or b"").decode()


@pytest.mark.parametrize(
    ("script", "message"),
    [
        ("def process(record, ctx)\n    pass\n", "the script does not load: SyntaxError"),
        ("import not_a_module\ndef process(r, c):\n    return {}\n", "ModuleNotFoundError"),
        ("x = 1\n", "defines no process"),
        (
            "def setup(ctx):\n    raise RuntimeError('lookup table missing')\n"
            "def process(r, c):\n    return {}\n",
            "setup failed: RuntimeError: lookup table missing",
        ),
    ],
)
def test_run_level_failures(script: str, message: str) -> None:
    world = FakeWorld(records=[{"a": 1}])
    with pytest.raises(RunFailed, match=message):
        run(params(script), http=world.client(), services={})
    assert world.result is None


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("return [1]", "TypeError: process returned list, not a dict"),
        ("return {'_row': 1, 'ok': 2}", "ValueError: process returned reserved keys: _row"),
    ],
)
def test_bad_return_values_are_record_errors(body: str, message: str) -> None:
    world = FakeWorld(records=[{"a": 1}])
    run(params(f"def process(record, ctx):\n    {body}\n"), http=world.client(), services={})
    assert world.lines()[0]["_error"] == message


def test_setup_and_params_reach_the_script() -> None:
    script = (
        "STATE = {}\n"
        "def setup(ctx):\n    STATE['threshold'] = ctx.params['threshold']\n"
        "def process(record, ctx):\n    return {'keep': record['score'] >= STATE['threshold']}\n"
    )
    world = FakeWorld(records=[{"score": 0.2}, {"score": 0.9}])
    run(params(script, script_params={"threshold": 0.5}), http=world.client(), services={})
    assert [line["keep"] for line in world.lines()] == [False, True]


def test_transfer_failures_fail_the_run() -> None:
    world = FakeWorld(records=[{"a": 1}], input_status=404)
    with pytest.raises(RunFailed, match=r"input could not be downloaded \(HTTP 404\)"):
        run(params("def process(r, c):\n    return {}\n"), http=world.client(), services={})
    world = FakeWorld(records=[{"a": 1}], result_status=409)
    with pytest.raises(RunFailed, match=r"results could not be uploaded \(HTTP 409\)"):
        run(params("def process(r, c):\n    return {}\n"), http=world.client(), services={})
