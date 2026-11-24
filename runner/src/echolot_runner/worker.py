"""A script's worker (docs/DECISIONS.md 2026-11-24): one run's script, in a process that holds
nothing secret. The harness keeps the run's token, URLs and service keys and makes the service
calls; the worker gets the script, its parameters, the service names and the records.

    python -m echolot_runner.worker --fd N [--max-processes M]

In the sandbox container the supervisor (sandbox.py) starts it under a uid of its own, with no
network; elsewhere the harness starts it as a child process with an empty environment.

Messages, one JSON object per line on the socket N (protocol.py):

    harness -> worker   {"t": "start", "script", "params", "services", "concurrency"}
                        {"t": "record", "row", "record"} ... then {"t": "end"}
                        {"t": "reply", "id", "ok": true, "body"}
                        {"t": "reply", "id", "ok": false, "status", "message"}
    worker -> harness   {"t": "ready"} after setup, or {"t": "failed", "message"}
                        {"t": "call", "id", "service", "method", "path", "params", "json"}
                        {"t": "log", "level", "message"}
                        {"t": "result", "line"} per record, then {"t": "done"}
    supervisor -> harness, after the worker ended: {"t": "exit", "code"}

The worker ends when the harness closes the connection. A script's print() goes to the run log.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import io
import itertools
import os
import queue
import resource
import shutil
import socket
import sys
import tempfile
import threading
import time
import traceback
import types
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from echolot_runner.context import Context, ServiceError
from echolot_runner.protocol import Channel, encode

MAX_ERROR_CHARS = 2000
MAX_LOG_CHARS = 10_000
PR_SET_DUMPABLE = 4

Process = Callable[[dict[str, Any], Context], Any]


class _Failed(Exception):
    """The run cannot start; the message is for the user."""


class _Link:
    """The worker's side of the connection. A reader thread hands records to the main thread and
    replies to the threads that wait for them, and ends the process when the harness is gone."""

    def __init__(self, channel: Channel, workdir: str) -> None:
        self.channel = channel
        self.records: queue.Queue[tuple[int, Any] | None] = queue.Queue()
        self._workdir = workdir
        self._waiting: dict[int, Future[dict[str, Any]]] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def start_reading(self) -> None:
        threading.Thread(target=self._read, name="link", daemon=True).start()

    def _read(self) -> None:
        with contextlib.suppress(Exception):
            while (message := self.channel.receive()) is not None:
                kind = message["t"]
                if kind == "record":
                    self.records.put((int(message["row"]), message["record"]))
                elif kind == "end":
                    self.records.put(None)
                elif kind == "reply":
                    with self._lock:
                        waiting = self._waiting.pop(int(message["id"]), None)
                    if waiting is not None:
                        waiting.set_result(message)
        # the harness closed the connection (the run is over or was stopped) or spoke nonsense:
        # nothing is left to do, for this thread or the script's
        shutil.rmtree(self._workdir, ignore_errors=True)
        os._exit(0)

    def call(
        self, service: str, method: str, path: str, params: dict[str, Any] | None, body: Any
    ) -> Any:
        waiting: Future[dict[str, Any]] = Future()
        with self._lock:
            ident = next(self._ids)
        request = {
            "t": "call",
            "id": ident,
            "service": service,
            "method": method,
            "path": path,
            "params": params,
            "json": body,
        }
        data = encode(request)  # a body that is not JSON raises here, in the script's thread
        with self._lock:
            self._waiting[ident] = waiting
        self.channel.send_bytes(data)
        reply = waiting.result()
        if reply.get("ok"):
            return reply.get("body")
        status = reply.get("status")
        raise ServiceError(
            service, status if isinstance(status, int) else None, str(reply.get("message") or "")
        )

    def log(self, message: str, level: str = "info") -> None:
        with contextlib.suppress(OSError):
            self.channel.send({"t": "log", "level": level, "message": message[:MAX_LOG_CHARS]})


class _Service:
    """What ``ctx.service(name)`` returns in the worker: the call goes to the harness."""

    def __init__(self, name: str, link: _Link) -> None:
        self._name = name
        self._link = link

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, json: Any = None, params: dict[str, Any] | None = None) -> Any:
        return self.request("POST", path, json=json, params=params)

    def request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None
    ) -> Any:
        return self._link.call(self._name, method.upper(), path, params, json)


class _ToLog(io.TextIOBase):
    """sys.stdout and sys.stderr of the script: whole lines go to the run log."""

    def __init__(self, link: _Link, level: str) -> None:
        self._link = link
        self._level = level
        self._buffer = ""
        self._lock = threading.Lock()

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        with self._lock:
            self._buffer += text
            lines = self._buffer.split("\n")
            self._buffer = lines.pop()
            if len(self._buffer) > MAX_LOG_CHARS:
                lines.append(self._buffer)
                self._buffer = ""
        for line in lines:
            if line.strip():
                self._link.log(line, self._level)
        return len(text)


def _load(script: str, ctx: Context, link: _Link) -> Process:
    module = types.ModuleType("workspace_script")
    module.__file__ = "script.py"
    try:
        exec(compile(script, "script.py", "exec"), module.__dict__)
    except Exception as exc:
        raise _Failed(f"the script does not load: {type(exc).__name__}: {exc}") from exc
    process = module.__dict__.get("process")
    if not callable(process):
        raise _Failed("the script defines no process(record, ctx)")
    setup = module.__dict__.get("setup")
    if callable(setup):
        try:
            setup(ctx)
        except Exception as exc:
            link.log(f"setup failed:\n{traceback.format_exc()}", "error")
            raise _Failed(f"setup failed: {type(exc).__name__}: {exc}") from exc
    return process


def _call(process: Process, ctx: Context, row: int, record: dict[str, Any]) -> dict[str, Any]:
    """One record: its result line."""
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
    except BaseException as exc:  # SystemExit from a script fails its record, not the worker
        message = f"{type(exc).__name__}: {exc}"
        line = {"_row": row, "_status": "error", "_error": message[:MAX_ERROR_CHARS]}
        line["_traceback"] = traceback.format_exc()
    line["_ms"] = round((time.monotonic() - started) * 1000)
    return line


def _send_result(channel: Channel, row: int, line: dict[str, Any]) -> None:
    try:
        data = encode({"t": "result", "line": line}, default=str)
    except (TypeError, ValueError) as exc:
        problem = "too large" if isinstance(exc, ValueError) else "not JSON"
        failed = {"_row": row, "_status": "error", "_error": f"the result is {problem}: {exc}"}
        data = encode({"t": "result", "line": {**failed, "_ms": line.get("_ms")}})
    with contextlib.suppress(OSError):
        channel.send_bytes(data)


def serve(channel: Channel, workdir: str) -> None:
    link = _Link(channel, workdir)
    start = channel.receive()
    if start is None or start.get("t") != "start":
        os._exit(1)
    names = [str(s) for s in start.get("services") or []]
    ctx = Context({n: _Service(n, link) for n in names}, dict(start.get("params") or {}), link.log)
    sys.stdout = _ToLog(link, "info")
    sys.stderr = _ToLog(link, "warning")
    link.start_reading()
    try:
        process = _load(str(start.get("script") or ""), ctx, link)
    except _Failed as exc:
        channel.send({"t": "failed", "message": str(exc)[:MAX_ERROR_CHARS]})
        _wait_for_the_end()
        return
    channel.send({"t": "ready"})
    concurrency = max(1, min(int(start.get("concurrency") or 4), 64))
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="record") as pool:
        while (item := link.records.get()) is not None:
            row, record = item

            def done(future: Future[dict[str, Any]], row: int = row) -> None:
                _send_result(channel, row, future.result())

            pool.submit(_call, process, ctx, row, record).add_done_callback(done)
    channel.send({"t": "done"})
    _wait_for_the_end()


def _wait_for_the_end() -> None:
    """Until the harness closes the connection, which ends the process (_Link._read)."""
    threading.Event().wait()


def _harden(max_processes: int | None) -> None:
    """Before any script code: no core dumps, and /proc/<pid> of this process closed to other
    processes of the same uid (a script may undo that for its own process only)."""
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if max_processes:
        resource.setrlimit(resource.RLIMIT_NPROC, (max_processes, max_processes))
    if sys.platform.startswith("linux"):
        ctypes.CDLL(None, use_errno=True).prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m echolot_runner.worker")
    parser.add_argument("--fd", type=int, required=True)
    parser.add_argument("--max-processes", type=int, default=None)
    args = parser.parse_args(argv)
    os.umask(0o077)
    _harden(args.max_processes)
    workdir = tempfile.mkdtemp(prefix="script-")
    os.chdir(workdir)
    os.environ["HOME"] = os.environ["TMPDIR"] = workdir
    tempfile.tempdir = workdir
    sock = socket.socket(fileno=args.fd)
    serve(Channel(sock), workdir)


if __name__ == "__main__":
    main()
