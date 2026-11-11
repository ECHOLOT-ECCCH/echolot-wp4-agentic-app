"""Single requests to a service, sent by the agent through the runner (service_call, docs/PLAN.md
7.9). A call travels like a run of one record: the app stores it with a token, the executor starts
the runner with a fixed script that makes the request, and the runner fetches the request and
sends the reply back through the endpoints under /runner/calls (7.3). The service's key stays on
the runner. A call leaves no script version, run or result file in the workspace."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from echolot_app.db import RunStatus, ServiceCall, utcnow
from echolot_app.runs.executor import Executor, app_status
from echolot_app.runs.tokens import new_token
from echolot_app.settings import Settings

log = structlog.get_logger(__name__)

# The runner runs this for the call's one record, which is the request (call_input)
CALL_SCRIPT = """\
def process(record, ctx):
    client = ctx.service(record["service"])
    params = record.get("params") or None
    if record["method"] == "GET":
        return {"reply": client.get(record["path"], params=params)}
    return {"reply": client.post(record["path"], json=record.get("body"), params=params)}
"""
CALL_SCRIPT_SHA256 = hashlib.sha256(CALL_SCRIPT.encode("utf-8")).hexdigest()

# a little longer than a service's own timeout with its retries usually takes
CALL_WAIT_S = 200.0
POLL_INTERVAL_S = 0.5
MAX_RESULT_BYTES = 2_000_000

SessionFactory = Callable[[], AsyncSession]


@dataclass(frozen=True)
class CallOutcome:
    status: RunStatus
    reply: Any = None
    error: str | None = None


def call_params(settings: Settings, call: ServiceCall, token: str) -> dict[str, Any]:
    """The runner parameters of docs/PLAN.md 7.2, for a call."""
    base = f"{settings.public_url.rstrip('/')}/runner/calls/{call.id}"
    return {
        "run_id": str(call.id),
        "script": CALL_SCRIPT,
        "script_sha256": CALL_SCRIPT_SHA256,
        "script_params": {},
        "services": [call.service],
        "input_url": f"{base}/input",
        "progress_url": f"{base}/progress",
        "result_url": f"{base}/result",
        "token": token,
        "limit": 1,
        "concurrency": 1,
    }


def call_input(call: ServiceCall) -> bytes:
    """The call's single input line: the request, as the record CALL_SCRIPT reads."""
    record = {"service": call.service, **call.request}
    return (json.dumps({"_row": 0, "record": record}, ensure_ascii=False) + "\n").encode("utf-8")


async def store_call_result(call: ServiceCall, body: AsyncIterator[bytes]) -> None:
    """Keep the runner's one result line; a reply too large to keep becomes an error."""
    received = bytearray()
    async for chunk in body:
        received.extend(chunk)
        if len(received) > MAX_RESULT_BYTES:
            call.result = {
                "_status": "error",
                "_error": f"the reply is larger than {MAX_RESULT_BYTES // 1_000_000} MB",
            }
            return
    lines = [line for line in received.decode("utf-8").splitlines() if line.strip()]
    call.result = json.loads(lines[0]) if lines else {"_status": "error", "_error": "no result"}


async def start_call(
    db: AsyncSession,
    settings: Settings,
    executor: Executor,
    workspace_id: uuid.UUID,
    service: str,
    request: dict[str, Any],
    purpose: str | None,
) -> ServiceCall:
    """Store the call, hand it to the executor and return it (committed)."""
    token, hashed = new_token()
    call = ServiceCall(
        workspace_id=workspace_id,
        service=service,
        request=request,
        purpose=purpose,
        status=RunStatus.QUEUED.value,
        executor=executor.name,
        token_hash=hashed,
    )
    db.add(call)
    await db.commit()  # the runner may ask for its input before submit returns
    try:
        call.external_id = await executor.submit(str(call.id), call_params(settings, call, token))
    except Exception as exc:
        log.warning("call.submit_failed", call=str(call.id), error=str(exc))
        call.status = RunStatus.FAILED.value
        call.error = f"the call could not be started: {exc}"
        call.finished_at = utcnow()
    await db.commit()
    return call


async def wait_for_call(
    session: SessionFactory,
    executor: Executor,
    call_id: uuid.UUID,
    timeout_s: float = CALL_WAIT_S,
) -> CallOutcome:
    """Follow the call until the executor reports an end, or cancel it after ``timeout_s``."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        async with session() as db:
            call = await db.get(ServiceCall, call_id)
            if call is None:
                return CallOutcome(RunStatus.FAILED, error="the call is gone")
            if RunStatus(call.status).is_terminal:  # it could not be started
                return CallOutcome(RunStatus(call.status), error=call.error)
            assert call.external_id is not None
            state = await executor.status(call.external_id)
            status = app_status(state, result_received=call.result is not None)
            timed_out = asyncio.get_running_loop().time() >= deadline
            if status.is_terminal or timed_out:
                if not status.is_terminal:
                    await executor.cancel(call.external_id)
                    status = RunStatus.FAILED
                    call.error = f"no answer within {timeout_s:g} s; the call was cancelled"
                elif status is RunStatus.FAILED and call.error is None:
                    call.error = state.message or f"the runner reports {state.kind.lower()}"
                call.status = status.value
                call.finished_at = utcnow()
                await db.commit()
                return _outcome(status, call.result, call.error)
        await asyncio.sleep(POLL_INTERVAL_S)


def _outcome(status: RunStatus, result: dict[str, Any] | None, error: str | None) -> CallOutcome:
    if status is RunStatus.DONE and result is not None:
        if result.get("_status") == "ok":
            return CallOutcome(status, reply=result.get("reply"))
        return CallOutcome(RunStatus.FAILED, error=result.get("_error") or "the call failed")
    return CallOutcome(status, error=error or (result or {}).get("_error"))
