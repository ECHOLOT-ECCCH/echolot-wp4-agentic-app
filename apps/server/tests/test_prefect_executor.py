from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from echolot_app.db import RunStatus
from echolot_app.runs.executor import ExecState, ExecutorError, app_status
from echolot_app.runs.prefect import PrefectExecutor, display_url

API = "http://prefect.test/api"


class FakePrefect:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.deployments = {"run-script/default": "dep-1"}
        self.ready = True  # a runner polls the deployment
        self.flow_runs: dict[str, dict[str, Any]] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/api")
        if path == "/admin/version":
            return httpx.Response(200, json="3.8.8")
        if path.startswith("/deployments/name/"):
            dep = self.deployments.get(path.removeprefix("/deployments/name/"))
            if not dep:
                return httpx.Response(404)
            status = "READY" if self.ready else "NOT_READY"
            return httpx.Response(
                200, json={"id": dep, "status": status, "last_polled": "2026-11-09T10:00:00Z"}
            )
        if path == "/deployments/dep-1/create_flow_run":
            run_id = f"fr-{len(self.flow_runs) + 1}"
            self.flow_runs[run_id] = {"state": {"type": "SCHEDULED"}}
            return httpx.Response(201, json={"id": run_id})
        if path.startswith("/flow_runs/") and path.endswith("/set_state"):
            run_id = path.split("/")[2]
            self.flow_runs[run_id]["state"] = json.loads(request.content)["state"]
            return httpx.Response(201, json={"status": "ACCEPT"})
        if path.startswith("/flow_runs/"):
            run = self.flow_runs.get(path.split("/")[2])
            return httpx.Response(200, json=run) if run else httpx.Response(404)
        return httpx.Response(500)

    def executor(self, **kwargs: Any) -> PrefectExecutor:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return PrefectExecutor(
            API, kwargs.pop("deployment", "run-script/default"), http=client, **kwargs
        )


async def test_submit_status_and_cancel() -> None:
    prefect = FakePrefect()
    executor = prefect.executor(auth_string="admin:pass")
    flow_run = await executor.submit("0123456789", {"run_id": "0123456789", "token": "t"})
    assert flow_run == "fr-1"
    create = next(r for r in prefect.requests if r.url.path.endswith("/create_flow_run"))
    body = json.loads(create.content)
    assert body["parameters"] == {"params": {"run_id": "0123456789", "token": "t"}}
    assert body["name"] == "echolot-01234567"
    expected = "Basic " + base64.b64encode(b"admin:pass").decode()
    assert all(r.headers["authorization"] == expected for r in prefect.requests)

    assert await executor.status("fr-1") == ExecState("SCHEDULED")
    await executor.cancel("fr-1")
    assert (await executor.status("fr-1")).kind == "CANCELLING"
    await executor.submit("x", {})
    lookups = [r for r in prefect.requests if "/deployments/name/" in r.url.path]
    assert len(lookups) == 1  # the deployment id is cached
    await executor.aclose()


@pytest.mark.parametrize(
    ("kind", "result", "status"),
    [
        ("SCHEDULED", False, RunStatus.QUEUED),
        ("PENDING", False, RunStatus.QUEUED),
        ("PAUSED", False, RunStatus.QUEUED),
        ("RUNNING", False, RunStatus.RUNNING),
        ("CANCELLING", False, RunStatus.RUNNING),
        ("COMPLETED", True, RunStatus.DONE),
        ("COMPLETED", False, RunStatus.FAILED),
        ("FAILED", False, RunStatus.FAILED),
        ("CRASHED", False, RunStatus.FAILED),
        ("CANCELLED", False, RunStatus.CANCELLED),
    ],
)
async def test_every_state_type_maps(kind: str, result: bool, status: RunStatus) -> None:
    prefect = FakePrefect()
    prefect.flow_runs["fr-9"] = {"state": {"type": kind, "message": None}}
    state = await prefect.executor().status("fr-9")
    assert state.kind == kind
    assert app_status(state, result_received=result) is status


async def test_failure_messages_keep_the_runners_words() -> None:
    prefect = FakePrefect()
    prefect.flow_runs["fr-9"] = {
        "state": {
            "type": "FAILED",
            "message": "Flow run encountered an exception: RunFailed: setup failed: KeyError: 'x'",
        }
    }
    assert (await prefect.executor().status("fr-9")).message == "setup failed: KeyError: 'x'"
    gone = await prefect.executor().status("fr-404")
    assert gone == ExecState("CRASHED", "the flow run no longer exists on the Prefect server")


async def test_a_missing_deployment_says_what_to_do() -> None:
    prefect = FakePrefect()
    prefect.deployments = {}
    with pytest.raises(ExecutorError, match=r"is the runner serving it \(python -m echolot_runner"):
        await prefect.executor().submit("r", {})


async def test_unreachable_and_refused() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    executor = PrefectExecutor(
        API, "run-script/default", http=httpx.AsyncClient(transport=httpx.MockTransport(down))
    )
    with pytest.raises(ExecutorError, match=r"not reachable \(ConnectError\)"):
        await executor.status("fr-1")
    refused = PrefectExecutor(
        API,
        "run-script/default",
        http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401))),
    )
    with pytest.raises(ExecutorError, match="check ECHO_PREFECT_AUTH_STRING"):
        await refused.submit("r", {})


async def test_basic_auth_wins_over_an_api_key() -> None:
    prefect = FakePrefect()
    await prefect.executor(auth_string="u:p", api_key="k").submit("r", {})
    basic = prefect.requests[-1].headers["Authorization"]
    await prefect.executor(api_key="k").submit("r", {})
    assert basic == "Basic " + base64.b64encode(b"u:p").decode()
    assert prefect.requests[-1].headers["Authorization"] == "Bearer k"
    await prefect.executor().submit("r", {})
    assert "Authorization" not in prefect.requests[-1].headers


async def test_the_check_reports_the_server_the_deployment_and_the_runner() -> None:
    prefect = FakePrefect()
    assert await prefect.executor().check() == [
        (True, "the Prefect server answers (3.8.8)"),
        (True, "the deployment run-script/default exists"),
        (True, "a runner polls it (last at 2026-11-09T10:00:00Z)"),
    ]
    prefect.ready = False
    *_, last = await prefect.executor().check()
    assert not last[0] and "is the runner up?" in last[1]
    prefect.deployments = {}
    *_, last = await prefect.executor().check()
    assert last == (
        False,
        "no deployment run-script/default: start the runner (python -m echolot_runner.serve)",
    )


async def test_the_check_works_without_a_version() -> None:
    def cloud(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/admin/version"):
            return httpx.Response(404)
        if request.url.path.endswith("/deployments/filter"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"id": "d", "status": "READY", "last_polled": "t"})

    transport = httpx.MockTransport(cloud)
    executor = PrefectExecutor(API, "a/b", api_key="k", http=httpx.AsyncClient(transport=transport))
    assert executor.auth == "API key"
    assert (await executor.check())[0] == (
        True,
        "the Prefect server answers (version not reported)",
    )


def _answering(response: httpx.Response) -> PrefectExecutor:
    transport = httpx.MockTransport(lambda _r: response)
    return PrefectExecutor(API, "run-script/default", http=httpx.AsyncClient(transport=transport))


async def test_the_check_names_a_wrong_url_and_a_refusal() -> None:
    wrong = [(False, "no Prefect API answers at this URL: it usually ends in /api")]
    assert await _answering(httpx.Response(200, text="<!doctype html>")).check() == wrong
    assert await _answering(httpx.Response(404)).check() == wrong
    [(fine, what)] = await _answering(httpx.Response(403)).check()
    assert not fine
    assert what == (
        "the Prefect server refused the app (403): "
        "check ECHO_PREFECT_AUTH_STRING or ECHO_PREFECT_API_KEY"
    )


async def test_the_check_reports_a_server_that_goes_away() -> None:
    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/admin/version"):
            return httpx.Response(200, json="3.8.8")
        raise httpx.ReadTimeout("gone")

    transport = httpx.MockTransport(flaky)
    executor = PrefectExecutor(API, "a/b", http=httpx.AsyncClient(transport=transport))
    assert await executor.check() == [
        (True, "the Prefect server answers (3.8.8)"),
        (False, "the Prefect server is not reachable (ReadTimeout)"),
    ]


def test_urls_are_shown_without_credentials() -> None:
    assert display_url("https://u:p@prefect.example.org/api") == "https://prefect.example.org/api"
    assert PrefectExecutor("https://u:p@prefect.example.org/api", "a/b").auth == "basic auth"
    assert display_url("http://prefect:4200/api") == "http://prefect:4200/api"


def test_check_runs_on_the_command_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from echolot_app import cli
    from echolot_app.settings import Settings

    prefect = FakePrefect()
    prefect.ready = False
    monkeypatch.setattr(cli, "build_executor", lambda _settings: prefect.executor(api_key="k"))
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: Settings(executor="prefect", public_url="http://app:8000"),
    )
    assert cli.main(["check-runs"]) == 1
    out = capsys.readouterr().out
    assert "(API key), deployment run-script/default." in out
    assert "ok    the deployment run-script/default exists" in out
    assert "FAIL  no runner has polled it lately" in out
    assert "only a runner on the same Docker network reaches that" in out
    monkeypatch.undo()  # the real build_executor
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(executor="local"))
    assert cli.main(["check-runs"]) == 0
    assert "ECHO_EXECUTOR=local" in capsys.readouterr().out


def test_the_deployment_name_has_two_parts() -> None:
    with pytest.raises(ValueError, match="'<flow>/<deployment>'"):
        PrefectExecutor(API, "run-script")
