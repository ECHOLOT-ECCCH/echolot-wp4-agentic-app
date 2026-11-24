"""``python -m echolot_runner.sandbox``: the supervisor of the script sandbox (docs/DECISIONS.md
2026-11-24), the container's first process.

It listens on a Unix socket that only the runner's group may connect to, and for each connection
starts a worker (worker.py) under a uid of its own, never given again before the container
restarts. It reads nothing from the connection: the worker and the harness talk directly. When
the harness hangs up or the worker ends, it kills whatever that uid still runs, has that uid
remove what it left in /tmp, tells the harness how the worker ended ({"t": "exit", "code"})
and closes its copy of the connection.

Started as root it needs only CAP_SETUID and CAP_SETGID (the workers' uids) and CAP_KILL (their
processes); deploy/docker-compose.yml takes every other capability. Started as any other user
(development, tests) it runs the workers as itself.

    SANDBOX_SOCKET          the socket, default /run/sandbox/sandbox.sock
    SANDBOX_CLIENT_GID      the group that may connect (the runner's), default 10001
    SANDBOX_UIDS            the workers' uids, default 20000-79999
    SANDBOX_MAX_WORKERS     workers at a time, default 16
    SANDBOX_MAX_PROCESSES   processes and threads per worker, default 256
"""

from __future__ import annotations

import contextlib
import os
import select
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass

from echolot_runner.protocol import encode

WORKER_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
WORKER_COMMAND = [sys.executable, "-I", "-B", "-m", "echolot_runner.worker"]
# the harness hung up: poll() reports it on our copy of the connection without reading from it
HANGUP = getattr(select, "POLLRDHUP", 0) | select.POLLHUP | select.POLLERR | select.POLLNVAL


@dataclass
class Worker:
    process: subprocess.Popen[bytes]
    conn: socket.socket
    uid: int


def say(message: str) -> None:
    print(f"sandbox: {message}", file=sys.stderr, flush=True)


def listen(path: str, client_gid: int, as_root: bool) -> socket.socket:
    """The socket, owned by root and the runner's group, mode 0660, in a directory a worker
    cannot write to, so no script can connect to it or put another in its place."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    if as_root:
        os.setegid(client_gid)  # the socket takes this group
        try:
            listener.bind(path)
        finally:
            os.setegid(0)
        os.chmod(path, 0o660)
    else:
        listener.bind(path)
        os.chmod(path, 0o600)
    listener.listen(16)
    return listener


def uids_of(spec: str) -> Iterator[int]:
    first, _, last = spec.partition("-")
    return iter(range(int(first), int(last or first) + 1))


def start(conn: socket.socket, uid: int, as_root: bool, max_processes: int) -> Worker:
    command = [*WORKER_COMMAND, "--fd", str(conn.fileno())]
    who: dict[str, object] = {}
    if as_root:
        command += ["--max-processes", str(max_processes)]
        who = {"user": uid, "group": uid, "extra_groups": []}
    process = subprocess.Popen(
        command,
        pass_fds=(conn.fileno(),),
        env=WORKER_ENV,
        cwd="/",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        start_new_session=True,
        umask=0o077,
        **who,  # type: ignore[arg-type]
    )
    return Worker(process, conn, uid)


def processes_of(uid: int, proc: str = "/proc") -> list[int]:
    """The live processes of ``uid``; a zombie (killed, not yet collected) no longer runs."""
    found = []
    for name in os.listdir(proc):
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(proc, name, "status")) as fh:
                fields = dict(line.split(":", 1) for line in fh if ":" in line)
        except OSError:
            continue
        uids = fields.get("Uid", "").split()
        if fields.get("State", "").split()[:1] != ["Z"] and str(uid) in uids:
            found.append(int(name))
    return found


def kill_all(worker: Worker, as_root: bool) -> None:
    """The worker and everything its uid runs; without root, the worker's process group."""
    if not as_root:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(worker.process.pid, signal.SIGKILL)
        return
    for _ in range(50):  # a script may fork while we kill
        pids = processes_of(worker.uid)
        if not pids:
            return
        for pid in pids:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)
        time.sleep(0.02)
    say(f"uid {worker.uid} still runs processes")


def clean_tmp(uid: int) -> None:
    """Remove what ``uid`` left in /tmp, as that uid (root here may not read its files)."""
    subprocess.Popen(
        [*WORKER_COMMAND[:-1], "echolot_runner.sandbox", "--clean"],
        env=WORKER_ENV,
        cwd="/",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        user=uid,
        group=uid,
        extra_groups=[],
    )


def clean() -> None:
    """``--clean``: as a worker's uid, delete everything of this uid under /tmp."""
    me = os.getuid()
    for root, dirs, files in os.walk("/tmp", topdown=False):
        for name in files + dirs:
            path = os.path.join(root, name)
            with contextlib.suppress(OSError):
                if os.lstat(path).st_uid == me:
                    if os.path.isdir(path) and not os.path.islink(path):
                        os.rmdir(path)
                    else:
                        os.unlink(path)


def serve() -> None:
    path = os.environ.get("SANDBOX_SOCKET", "/run/sandbox/sandbox.sock")
    client_gid = int(os.environ.get("SANDBOX_CLIENT_GID", "10001"))
    uid_range = os.environ.get("SANDBOX_UIDS", "20000-79999")
    max_workers = int(os.environ.get("SANDBOX_MAX_WORKERS", "16"))
    max_processes = int(os.environ.get("SANDBOX_MAX_PROCESSES", "256"))
    as_root = os.geteuid() == 0
    uids = uids_of(uid_range) if as_root else iter(lambda: os.getuid(), None)
    listener = listen(path, client_gid, as_root)
    poller = select.poll()
    poller.register(listener, select.POLLIN)
    workers: dict[int, Worker] = {}  # by pid
    by_fd: dict[int, Worker] = {}
    who = f"uids {uid_range}" if as_root else "this process's own uid (not root)"
    say(f"listening on {path}; workers run as {who}")

    def stop(*_: object) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    while True:
        for fd, events in poller.poll(200):
            if fd == listener.fileno():
                conn, _ = listener.accept()
                if len(workers) >= max_workers:
                    say(f"refused a connection: {max_workers} workers already")
                    conn.close()
                    continue
                uid = next(uids, None)
                if uid is None:
                    say("no uid left for a worker; restarting the container frees them")
                    raise SystemExit(1)
                worker = start(conn, uid, as_root, max_processes)
                workers[worker.process.pid] = by_fd[conn.fileno()] = worker
                poller.register(conn, HANGUP)
            elif events & HANGUP and (worker := by_fd.get(fd)) is not None:
                kill_all(worker, as_root)  # the harness hung up: the run is over or stopped
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                break
            if (worker := workers.pop(pid, None)) is not None:
                finish(worker, os.waitstatus_to_exitcode(status), as_root, poller, by_fd)


def finish(
    worker: Worker,
    code: int,
    as_root: bool,
    poller: select.poll,
    by_fd: dict[int, Worker],
) -> None:
    kill_all(worker, as_root)
    if as_root:
        clean_tmp(worker.uid)
    fd = worker.conn.fileno()
    with contextlib.suppress(OSError):
        worker.conn.sendall(encode({"t": "exit", "code": code}))
    with contextlib.suppress(KeyError, ValueError):
        poller.unregister(fd)
    by_fd.pop(fd, None)
    worker.conn.close()


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--clean"]:
        clean()
        return
    serve()


if __name__ == "__main__":
    main()
