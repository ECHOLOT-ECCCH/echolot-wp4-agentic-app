"""Run a workspace script over records (docs/PLAN.md 7.2-7.4).

Download the run's input (JSONL, ``{"_row": n, "record": {...}}`` per line), load the script
into a fresh module, call ``setup(ctx)`` once, then ``process(record, ctx)`` per record on a
thread pool. A record that raises gets ``_status: "error"`` and the run goes on; only run-level
problems (the script does not load, ``setup`` fails, input or result transfer fails) fail the
run. Results are written in ``_row`` order with a bounded window of records in flight, progress
is posted at most every two seconds, and the results JSONL is uploaded at the end.
"""

from __future__ import annotations

import json
import logging
import tempfile
import time
import traceback
import types
from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import httpx

from echolot_runner.context import Context
from echolot_runner.params import RunnerParams
from echolot_runner.services import (
    ServiceClient,
    ServiceConfig,
    ServicesConfigError,
    load_services,
)

log = logging.getLogger("echolot_runner")

PROGRESS_EVERY_S = 2.0
MAX_ERROR_CHARS = 2000
MAX_TRACEBACKS_LOGGED = 20
WINDOW_PER_WORKER = 4  # records in flight per worker thread
TRANSFER_TIMEOUT_S = 300.0


class RunFailed(Exception):
    """The run cannot go on; the message is for the user."""


@dataclass
class Counts:
    total: int = 0
    done: int = 0
    ok: int = 0
    error: int = 0

    def progress(self) -> dict[str, int]:
        return {"done": self.done, "total": self.total, "ok": self.ok, "error": self.error}


def run(
    params: RunnerParams | dict[str, Any],
    *,
    http: httpx.Client | None = None,
    services: dict[str, ServiceConfig] | None = None,
) -> dict[str, Any]:
    """Run once; return ``{"total", "ok", "error", "duration_s"}`` or raise RunFailed."""
    if isinstance(params, dict):
        params = RunnerParams.from_dict(params)
    started = time.monotonic()
    owned = http is None
    client = http or httpx.Client(follow_redirects=True)
    try:
        clients = _service_clients(params, client, services)
        with tempfile.TemporaryDirectory(prefix=f"run-{params.run_id}-") as tmp:
            workdir = Path(tmp)
            input_path = _download(client, params, workdir / "input.jsonl")
            process, ctx = _load(params, clients)
            result_path = workdir / "result.jsonl"
            counts = Counts(total=_count_lines(input_path, params.limit))
            reporter = _Reporter(client, params)
            with input_path.open("rb") as source, result_path.open("wb") as sink:
                _process_all(params, process, ctx, source, sink, counts, reporter)
            reporter.post(counts, force=True)
            _upload(client, params, result_path)
    finally:
        if owned:
            client.close()
    summary = {
        "total": counts.total,
        "ok": counts.ok,
        "error": counts.error,
        "duration_s": round(time.monotonic() - started, 3),
    }
    log.info("run %s finished: %s", params.run_id, summary)
    return summary


def _service_clients(
    params: RunnerParams, http: httpx.Client, services: dict[str, ServiceConfig] | None
) -> dict[str, ServiceClient]:
    try:
        configured = load_services() if services is None else services
        clients = {}
        for name in params.services:
            config = configured.get(name)
            if config is None:
                raise ServicesConfigError(
                    f"the service {name!r} is not configured on this runner host"
                )
            clients[name] = ServiceClient(config, http)
        return clients
    except ServicesConfigError as exc:
        raise RunFailed(str(exc)) from exc


def _auth(params: RunnerParams) -> dict[str, str]:
    return {"Authorization": f"Bearer {params.token}"}


def _download(http: httpx.Client, params: RunnerParams, target: Path) -> Path:
    try:
        with http.stream(
            "GET", params.input_url, headers=_auth(params), timeout=TRANSFER_TIMEOUT_S
        ) as response:
            if response.status_code != 200:
                raise RunFailed(f"the input could not be downloaded (HTTP {response.status_code})")
            with target.open("wb") as fh:
                for chunk in response.iter_bytes():
                    fh.write(chunk)
    except httpx.HTTPError as exc:
        raise RunFailed(f"the input could not be downloaded ({type(exc).__name__})") from exc
    return target


def _upload(http: httpx.Client, params: RunnerParams, source: Path) -> None:
    def chunks() -> Iterator[bytes]:
        with source.open("rb") as fh:
            while chunk := fh.read(1024 * 1024):
                yield chunk

    try:
        response = http.put(
            params.result_url,
            content=chunks(),
            headers={**_auth(params), "Content-Type": "application/x-ndjson"},
            timeout=TRANSFER_TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        raise RunFailed(f"the results could not be uploaded ({type(exc).__name__})") from exc
    if response.status_code >= 300:
        raise RunFailed(f"the results could not be uploaded (HTTP {response.status_code})")


def _load(
    params: RunnerParams, clients: dict[str, ServiceClient]
) -> tuple[Callable[[dict[str, Any], Context], Any], Context]:
    module = types.ModuleType("workspace_script")
    module.__file__ = "script.py"
    try:
        code = compile(params.script, "script.py", "exec")
        exec(code, module.__dict__)
    except Exception as exc:
        raise RunFailed(f"the script does not load: {type(exc).__name__}: {exc}") from exc
    process = module.__dict__.get("process")
    if not callable(process):
        raise RunFailed("the script defines no process(record, ctx)")
    ctx = Context(clients, params.script_params)
    setup = module.__dict__.get("setup")
    if callable(setup):
        try:
            setup(ctx)
        except Exception as exc:
            log.error("setup failed:\n%s", traceback.format_exc())
            raise RunFailed(f"setup failed: {type(exc).__name__}: {exc}") from exc
    return process, ctx


def _count_lines(path: Path, limit: int | None) -> int:
    with path.open("rb") as fh:
        total = sum(1 for line in fh if line.strip())
    return total if limit is None else min(total, limit)


def _records(source: BinaryIO, limit: int | None) -> Iterator[tuple[int, dict[str, Any]]]:
    seen = 0
    for number, line in enumerate(source, start=1):
        if not line.strip():
            continue
        if limit is not None and seen >= limit:
            return
        try:
            item = json.loads(line)
            row, record = int(item["_row"]), item["record"]
        except (ValueError, KeyError, TypeError) as exc:
            raise RunFailed(f"input line {number} is not a record line") from exc
        seen += 1
        yield row, record


def _call(
    process: Callable[[dict[str, Any], Context], Any],
    ctx: Context,
    row: int,
    record: dict[str, Any],
) -> dict[str, Any]:
    """One record: its result line (without serializing)."""
    started = time.monotonic()
    try:
        output = process(record, ctx)
        if not isinstance(output, dict):
            raise TypeError(f"process returned {type(output).__name__}, not a dict")
        reserved = sorted(k for k in output if str(k).startswith("_"))
        if reserved:
            raise ValueError(f"process returned reserved keys: {', '.join(reserved)}")
        line: dict[str, Any] = {"_row": row, "_status": "ok", "_error": None}
        line.update(output)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        line = {"_row": row, "_status": "error", "_error": message[:MAX_ERROR_CHARS]}
        line["_traceback"] = traceback.format_exc()
    line["_ms"] = round((time.monotonic() - started) * 1000)
    return line


def _process_all(
    params: RunnerParams,
    process: Callable[[dict[str, Any], Context], Any],
    ctx: Context,
    source: BinaryIO,
    sink: BinaryIO,
    counts: Counts,
    reporter: _Reporter,
) -> None:
    window = params.concurrency * WINDOW_PER_WORKER
    pending: deque[Future[dict[str, Any]]] = deque()
    tracebacks = 0

    def finish(future: Future[dict[str, Any]]) -> None:
        nonlocal tracebacks
        line = future.result()
        trace = line.pop("_traceback", None)
        if line["_status"] == "ok":
            counts.ok += 1
        else:
            counts.error += 1
            if trace and tracebacks < MAX_TRACEBACKS_LOGGED:
                tracebacks += 1
                log.warning("row %s failed:\n%s", line["_row"], trace)
        counts.done += 1
        sink.write(json.dumps(line, ensure_ascii=False, default=str).encode("utf-8") + b"\n")
        reporter.post(counts)

    with ThreadPoolExecutor(max_workers=params.concurrency, thread_name_prefix="record") as pool:
        for row, record in _records(source, params.limit):
            pending.append(pool.submit(_call, process, ctx, row, record))
            while len(pending) >= window:
                finish(pending.popleft())
        while pending:
            finish(pending.popleft())


class _Reporter:
    """Posts progress to the app; a failed post is logged and the run goes on."""

    def __init__(self, http: httpx.Client, params: RunnerParams) -> None:
        self._http = http
        self._params = params
        self._last = 0.0

    def post(self, counts: Counts, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last < PROGRESS_EVERY_S:
            return
        self._last = now
        try:
            self._http.post(
                self._params.progress_url,
                json=counts.progress(),
                headers=_auth(self._params),
                timeout=10,
            )
        except httpx.HTTPError as exc:
            log.warning("progress not posted: %s", type(exc).__name__)
