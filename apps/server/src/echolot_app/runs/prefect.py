"""``PrefectExecutor``: runs as flow runs of the runner's deployment, through Prefect's REST API
with httpx (docs/PLAN.md 7.6). The app has no Prefect dependency; routes verified against
Prefect 3.7.5.dev5 and 3.8.8. The server may be anywhere: basic auth (a self-hosted server with
``PREFECT_SERVER_API_AUTH_STRING``) or an API key (Prefect Cloud, or a proxy that wants a bearer
token), as Prefect's own client does."""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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
        api_key: str | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        flow, sep, name = deployment.partition("/")
        if not sep or not flow or not name:
            raise ValueError(f"deployment must be '<flow>/<deployment>', not {deployment!r}")
        self._flow, self._deployment = flow, name
        headers = {}
        # the basic auth string first, then the key: Prefect's client does the same
        if auth_string:
            token = base64.b64encode(auth_string.encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        elif api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        basic = auth_string or urlsplit(api_url).username  # httpx sends a URL's user, too
        self.auth = "basic auth" if basic else "API key" if api_key else "no auth"
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

    async def check(self) -> list[tuple[bool, str]]:
        """What ``echolot-admin check-runs`` reports, each with whether it is fine: the server
        answers this app, the runner's deployment exists, and a runner polls it."""
        found: list[tuple[bool, str]] = []
        try:
            await self._check(found)
        except ExecutorError as exc:
            found.append((False, str(exc)))
        return found

    async def _check(self, found: list[tuple[bool, str]]) -> None:
        response = await self._request("GET", "/admin/version")
        if response.status_code in (401, 403):
            return found.append((False, _refused(response, "the app")))
        version = _json(response) if response.status_code == 200 else None
        if not isinstance(version, str):  # Prefect Cloud, say; or not Prefect's API at all
            version = None
            response = await self._request("POST", "/deployments/filter", json={"limit": 1})
            if response.status_code in (401, 403):
                return found.append((False, _refused(response, "the app")))
            if response.status_code != 200 or not isinstance(_json(response), list):
                return found.append(
                    (False, "no Prefect API answers at this URL: it usually ends in /api")
                )
        found.append((True, f"the Prefect server answers ({version or 'version not reported'})"))

        name = f"{self._flow}/{self._deployment}"
        response = await self._request("GET", f"/deployments/name/{name}")
        if response.status_code == 404:
            return found.append(
                (False, f"no deployment {name}: start the runner (python -m echolot_runner.serve)")
            )
        if response.status_code >= 400:
            return found.append((False, f"the Prefect server answered {response.status_code}"))
        deployment = _json(response) or {}
        found.append((True, f"the deployment {name} exists"))
        polled = deployment.get("last_polled") or "never"
        if deployment.get("status") == "READY":
            found.append((True, f"a runner polls it (last at {polled})"))
        else:
            found.append(
                (False, f"no runner has polled it lately (last: {polled}); is the runner up?")
            )

    async def aclose(self) -> None:
        await self._http.aclose()


def display_url(url: str) -> str:
    """The URL without user and password, for logs and messages."""
    parts = urlsplit(url)
    host = parts.netloc.rpartition("@")[2]
    return urlunsplit(parts._replace(netloc=host))


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _refused(response: httpx.Response, who: str) -> str:
    return (
        f"the Prefect server refused {who} ({response.status_code}): "
        "check ECHO_PREFECT_AUTH_STRING or ECHO_PREFECT_API_KEY"
    )


def _raise_for(response: httpx.Response, doing: str) -> None:
    if response.status_code in (401, 403):
        raise ExecutorError(_refused(response, doing))
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
