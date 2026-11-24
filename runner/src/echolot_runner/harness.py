"""Run a workspace script over records (docs/PLAN.md 7.2-7.4).

Download the run's input (JSONL, ``{"_row": n, "record": {...}}`` per line) and hand the records
to a worker that runs the script (worker.py; docs/DECISIONS.md 2026-11-24): in the sandbox
container when ``RUNNER_SANDBOX_SOCKET`` names its socket, otherwise a child process with an
empty environment. The worker calls ``setup(ctx)`` once, then ``process(record, ctx)`` per
record on a thread pool, and asks the harness to make its service calls, so the run's token, its
URLs and the service keys never leave this process. A record that raises gets
``_status: "error"`` and the run goes on; only run-level problems (the script does not load,
``setup`` fails, the worker dies, input or result transfer fails) fail the run. Results are
written in ``_row`` order with a bounded window of records in flight, progress is posted at most
every two seconds, and the results JSONL is uploaded at the end.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import queue
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import httpx

from echolot_runner.params import RunnerParams
from echolot_runner.protocol import Channel, ProtocolError
from echolot_runner.services import (
    ServiceClient,
    ServiceConfig,
    ServiceError,
    ServicesConfigError,
    load_services,
)

log = logging.getLogger("echolot_runner")
script_log = logging.getLogger("echolot_runner.script")

PROGRESS_EVERY_S = 2.0
MAX_ERROR_CHARS = 2000
MAX_LOG_CHARS = 10_000
MAX_TRACEBACKS_LOGGED = 20
WINDOW_PER_WORKER = 4  # records in flight per worker thread
TRANSFER_TIMEOUT_S = 300.0
# a worker started here gets nothing of this process's environment (keys, Prefect credentials)
WORKER_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
WORKER_COMMAND = [sys.executable, "-I", "-B", "-m", "echolot_runner.worker"]
OUT_OF_TURN = "the script's process sent a message out of turn"
LEVELS = {"info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}

ExitCode = Callable[[], int | None]


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
    sandbox: str | None = None,
) -> dict[str, Any]:
    """Run once; return ``{"total", "ok", "error", "duration_s"}`` or raise RunFailed.
    ``sandbox``: the sandbox's socket (default ``RUNNER_SANDBOX_SOCKET``); without one, the
    worker is a child process."""
    if isinstance(params, dict):
        params = RunnerParams.from_dict(params)
    started = time.monotonic()
    owned = http is None
    client = http or httpx.Client(follow_redirects=True)
    socket_path = sandbox or os.environ.get("RUNNER_SANDBOX_SOCKET") or None
    counts = Counts()
    reporter = _Reporter(client, params)
    try:
        clients = _service_clients(params, client, services)
        with tempfile.TemporaryDirectory(prefix=f"run-{params.run_id}-") as tmp:
            workdir = Path(tmp)
            input_path = _download(client, params, workdir / "input.jsonl")
            result_path = workdir / "result.jsonl"
            with _worker(socket_path) as (channel, exit_code):
                session = _Session(channel, clients, params, exit_code)
                try:
                    session.start()
                    counts.total = _count_lines(input_path, params.limit)
                    with input_path.open("rb") as source, result_path.open("wb") as sink:
                        session.process_all(source, sink, counts, reporter)
                finally:
                    session.close()
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


@contextlib.contextmanager
def _worker(socket_path: str | None) -> Iterator[tuple[Channel, ExitCode]]:
    """A connection to a new worker, and how to learn its exit code once it has gone."""
    if socket_path:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(socket_path)
        except OSError as exc:
            sock.close()
            raise RunFailed(f"the script sandbox does not answer ({type(exc).__name__})") from exc
        channel = Channel(sock)
        try:
            yield channel, lambda: None  # the sandbox sends the exit code before it closes
        finally:
            channel.close()
        return

    ours, theirs = socket.socketpair()
    try:
        process = subprocess.Popen(
            [*WORKER_COMMAND, "--fd", str(theirs.fileno())],
            pass_fds=(theirs.fileno(),),
            env=WORKER_ENV,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        ours.close()
        raise RunFailed(f"the script's process could not start ({exc})") from exc
    finally:
        theirs.close()
    channel = Channel(ours)

    def exit_code() -> int | None:
        try:
            return process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            return None

    try:
        yield channel, exit_code
    finally:
        channel.close()  # the worker ends when it reads the end of the connection
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)  # with whatever the script started
        process.wait()


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


def _ended(code: int | None) -> str:
    if code is None:
        return "the script's process ended unexpectedly"
    if code == -signal.SIGKILL:
        return (
            "the script's process was killed (signal 9), most likely for using more memory "
            "than the runner allows"
        )
    if code < 0:
        return f"the script's process was ended by signal {-code}"
    return f"the script's process ended unexpectedly (exit code {code})"


class _Session:
    """The harness's side of one worker. A reader thread makes the service calls the worker
    asks for and passes everything else to the main thread, which sends the records and writes
    the results. Nothing from the worker is trusted: a call names one of the run's services and
    stays under its URL (ServiceClient), and a result answers a record that was sent."""

    def __init__(
        self,
        channel: Channel,
        clients: dict[str, ServiceClient],
        params: RunnerParams,
        exit_code: ExitCode,
    ) -> None:
        self._channel = channel
        self._clients = clients
        self._params = params
        self._exit_code = exit_code
        self._inbox: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._calls = ThreadPoolExecutor(
            max_workers=params.concurrency + 1, thread_name_prefix="call"
        )
        self._reader = threading.Thread(target=self._read, name="worker-link", daemon=True)
        self._tracebacks = 0

    def start(self) -> None:
        self._reader.start()
        self._send(
            {
                "t": "start",
                "script": self._params.script,
                "params": self._params.script_params,
                "services": sorted(self._clients),
                "concurrency": self._params.concurrency,
            }
        )
        message = self._next()
        if message["t"] == "failed":
            raise RunFailed(str(message.get("message") or "the script failed")[:MAX_ERROR_CHARS])
        if message["t"] != "ready":
            raise RunFailed(OUT_OF_TURN)

    def process_all(
        self, source: BinaryIO, sink: BinaryIO, counts: Counts, reporter: _Reporter
    ) -> None:
        window = self._params.concurrency * WINDOW_PER_WORKER
        order: deque[int] = deque()  # rows sent, in input order, until written
        waiting: set[int] = set()  # rows sent whose result has not come
        received: dict[int, dict[str, Any]] = {}
        records = _records(source, self._params.limit)
        more = True
        while True:
            while more and len(order) < window:
                item = next(records, None)
                if item is None:
                    more = False
                    self._send({"t": "end"})
                    break
                row, record = item
                if row in waiting or row in received:
                    raise RunFailed(f"the input has row {row} twice")
                order.append(row)
                waiting.add(row)
                self._send({"t": "record", "row": row, "record": record})
            if not order:
                break
            message = self._next()
            line = message.get("line") if message["t"] == "result" else None
            if not isinstance(line, dict) or line.get("_status") not in ("ok", "error"):
                raise RunFailed(OUT_OF_TURN)
            row = line.get("_row")
            if not isinstance(row, int) or row not in waiting:
                raise RunFailed(OUT_OF_TURN)
            waiting.discard(row)
            received[row] = line
            while order and order[0] in received:
                self._write(received.pop(order.popleft()), sink, counts, reporter)
        if self._next()["t"] != "done":
            raise RunFailed(OUT_OF_TURN)

    def close(self) -> None:
        self._calls.shutdown(wait=False, cancel_futures=True)

    def _write(
        self, line: dict[str, Any], sink: BinaryIO, counts: Counts, reporter: _Reporter
    ) -> None:
        trace = line.pop("_traceback", None)
        if line["_status"] == "ok":
            counts.ok += 1
        else:
            counts.error += 1
            if trace and self._tracebacks < MAX_TRACEBACKS_LOGGED:
                self._tracebacks += 1
                log.warning("row %s failed:\n%s", line["_row"], str(trace)[:MAX_LOG_CHARS])
        counts.done += 1
        sink.write(json.dumps(line, ensure_ascii=False, default=str).encode("utf-8") + b"\n")
        reporter.post(counts)

    def _send(self, message: dict[str, Any]) -> None:
        try:
            self._channel.send(message)
        except OSError:
            # the worker is gone; the reader thread says how, or the connection's end does
            raise RunFailed(self._why_gone()) from None

    def _why_gone(self) -> str:
        with contextlib.suppress(queue.Empty):
            while (message := self._inbox.get(timeout=2)) is not None:
                if message["t"] == "exit":
                    return _ended(_code(message))
        return _ended(self._exit_code())

    def _next(self) -> dict[str, Any]:
        message = self._inbox.get()
        if message is None:
            raise RunFailed(_ended(self._exit_code()))
        if message["t"] == "exit":
            raise RunFailed(_ended(_code(message)))
        if message["t"] == "broken":
            raise RunFailed(f"the script's process sent something unreadable ({message['why']})")
        return message

    def _read(self) -> None:
        try:
            while (message := self._channel.receive()) is not None:
                kind = message["t"]
                if kind == "call":
                    with contextlib.suppress(RuntimeError):  # shut down: the run is over
                        self._calls.submit(self._call, message)
                elif kind == "log":
                    level = LEVELS.get(str(message.get("level")), logging.INFO)
                    script_log.log(level, "%s", str(message.get("message", ""))[:MAX_LOG_CHARS])
                else:
                    self._inbox.put(message)
        except (ProtocolError, OSError, ValueError) as exc:
            self._inbox.put({"t": "broken", "why": str(exc)})
            return
        self._inbox.put(None)

    def _call(self, message: dict[str, Any]) -> None:
        ident = message.get("id")
        try:
            reply = {"t": "reply", "id": ident, "ok": True, "body": self._request(message)}
        except ServiceError as exc:
            reply = {"t": "reply", "id": ident, "ok": False, "status": exc.status}
            reply["message"] = exc.message
        except Exception as exc:
            reply = {"t": "reply", "id": ident, "ok": False, "status": None}
            reply["message"] = f"a request this runner cannot make ({type(exc).__name__})"
        with contextlib.suppress(OSError, TypeError, ValueError):
            self._channel.send(reply)

    def _request(self, message: dict[str, Any]) -> Any:
        name = str(message.get("service"))
        client = self._clients.get(name)
        if client is None:
            allowed = ", ".join(sorted(self._clients)) or "none"
            raise ServiceError(name, None, f"not one of this run's services ({allowed})")
        method, path, params = message.get("method"), message.get("path"), message.get("params")
        if method not in ("GET", "POST") or not isinstance(path, str):
            raise ServiceError(name, None, "only GET and POST with a path")
        if params is not None and not isinstance(params, dict):
            raise ServiceError(name, None, "params must be a dict")
        if method == "GET":
            return client.get(path, params=params)
        return client.post(path, json=message.get("json"), params=params)


def _code(message: dict[str, Any]) -> int | None:
    code = message.get("code")
    return code if isinstance(code, int) else None


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
