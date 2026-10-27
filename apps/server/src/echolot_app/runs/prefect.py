"""``PrefectExecutor``: runs as flow runs of the runner's deployment, through Prefect's REST API
with httpx (docs/PLAN.md 7.6). The app has no Prefect dependency; routes verified against
Prefect 3.7.5.dev5 and 3.8.8."""

from __future__ import annotations

import base64
from typing import Any

import httpx

from echolot_app.runs.executor import ExecKind, ExecState, ExecutorError

KINDS: frozenset[str] = frozenset(ExecKind.__args__)  # type: ignore[attr-defined]
_NOISE = ("Flow run encountered an exception: ", "RunFailed: ")
MAX_MESSAGE = 2000


class PrefectExecutor:
    name = "prefect"

    def __init__(
        self,
        api_url: str,
        deployment: str,
        auth_string: str | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        flow, sep, name = deployment.partition("/")
        if not sep or not flow or not name:
            raise ValueError(f"deployment must be '<flow>/<deployment>', not {deployment!r}")
        self._flow, self._deployment = flow, name
        headers = {}
        if auth_string:
            token = base64.b64encode(auth_string.encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        self._http = http or httpx.AsyncClient(timeout=30)
        self._base = api_url.rstrip("/")
        self._headers = headers
        self._deployment_id: str | None = None

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._http.request(
                method, f"{self._base}{path}", headers=self._headers, **kwargs
            )
        except httpx.HTTPError as exc:
            raise ExecutorError(
                f"the Prefect server is not reachable ({type(exc).__name__})"
            ) from exc

    async def _lookup_deployment(self) -> str:
        if self._deployment_id is None:
            response = await self._request(
                "GET", f"/deployments/name/{self._flow}/{self._deployment}"
            )
            if response.status_code == 404:
                raise ExecutorError(
                    f"the Prefect deployment {self._flow}/{self._deployment} does not exist; "
                    "is the runner serving it (python -m echolot_runner.serve)?"
                )
            _raise_for(response, "reading the deployment")
            self._deployment_id = str(response.json()["id"])
        return self._deployment_id

    async def submit(self, run_id: str, params: dict[str, Any]) -> str:
        deployment = await self._lookup_deployment()
        response = await self._request(
            "POST",
            f"/deployments/{deployment}/create_flow_run",
            json={
                "parameters": {"params": params},
                "name": f"echolot-{run_id[:8]}",
                "tags": ["echolot"],
            },
        )
        if response.status_code == 404:
            self._deployment_id = None  # the deployment was removed; look it up again next time
        _raise_for(response, "creating the flow run")
        return str(response.json()["id"])

    async def status(self, external_id: str) -> ExecState:
        response = await self._request("GET", f"/flow_runs/{external_id}")
        if response.status_code == 404:
            return ExecState("CRASHED", "the flow run no longer exists on the Prefect server")
        _raise_for(response, "reading the flow run")
        state = response.json().get("state") or {}
        kind = str(state.get("type") or "PENDING")
        if kind not in KINDS:
            kind = "PENDING"
        return ExecState(kind, _clean(state.get("message")))  # type: ignore[arg-type]

    async def cancel(self, external_id: str) -> None:
        response = await self._request(
            "POST",
            f"/flow_runs/{external_id}/set_state",
            json={"state": {"type": "CANCELLING", "name": "Cancelling"}, "force": False},
        )
        if response.status_code != 404:
            _raise_for(response, "cancelling the flow run")

    async def aclose(self) -> None:
        await self._http.aclose()


def _raise_for(response: httpx.Response, doing: str) -> None:
    if response.status_code == 401:
        raise ExecutorError(f"the Prefect server refused {doing}: check ECHO_PREFECT_AUTH_STRING")
    if response.status_code >= 400:
        raise ExecutorError(f"the Prefect server answered {response.status_code} {doing}")


def _clean(message: Any) -> str | None:
    """Prefect wraps a flow's exception: keep the runner's own words."""
    if not message:
        return None
    text = str(message)
    for prefix in _NOISE:
        text = text.removeprefix(prefix)
    return text[:MAX_MESSAGE]
