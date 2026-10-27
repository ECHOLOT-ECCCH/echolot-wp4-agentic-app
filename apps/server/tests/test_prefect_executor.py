from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from echolot_app.db import RunStatus
from echolot_app.runs.executor import ExecState, ExecutorError, app_status
from echolot_app.runs.prefect import PrefectExecutor

API = "http://prefect.test/api"


class FakePrefect:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.deployments = {"run-script/default": "dep-1"}
        self.flow_runs: dict[str, dict[str, Any]] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/api")
        if path.startswith("/deployments/name/"):
            dep = self.deployments.get(path.removeprefix("/deployments/name/"))
            return httpx.Response(200, json={"id": dep}) if dep else httpx.Response(404)
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


def test_the_deployment_name_has_two_parts() -> None:
    with pytest.raises(ValueError, match="'<flow>/<deployment>'"):
        PrefectExecutor(API, "run-script")
