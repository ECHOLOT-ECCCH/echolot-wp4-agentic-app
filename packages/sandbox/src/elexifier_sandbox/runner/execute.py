"""Run one snippet in a fresh container (docs/PLAN.md 7.12).

The runner never bind-mounts host paths: it builds a tar with the snippet (``main.py``), the
inputs under ``in/`` (root-owned, read-only) and an empty ``out/`` owned by the sandbox user,
puts it into a container created for this run, starts the container, waits for it (killing it
at the timeout), reads the logs, fetches ``out/`` back and uploads it to storage. That works
when the runner is a sibling container in Compose (a bind mount would refer to the daemon's
host) as well as when it runs as a local process.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import mimetypes
import posixpath
import shutil
import tarfile
import tempfile
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import requests.exceptions
from docker.types import Mount

from elexifier_core.sandbox_contract import RunInput, RunOutput, RunRequest, RunResponse
from elexifier_core.storage import Storage

LOG_LIMIT = 64 * 1024  # bytes per stream
SANDBOX_UID = 1000
KILLED_EXIT_CODE = 137
WORK = "/work"


@dataclass
class ExecutionSettings:
    image: str = "elexifier-sandbox:local"
    runtime: str | None = None  # "runsc" to run under gVisor
    tmp_size_mb: int = 256


class ExecutionError(Exception):
    """The run could not even start (no daemon, no image, bad request)."""


# --- what we need from docker-py (so tests can fake it) ----------------------------------------


class ContainerLike(Protocol):
    def put_archive(self, path: str, data: Any) -> bool: ...
    def start(self) -> None: ...
    def wait(self, **kwargs: Any) -> dict[str, Any]: ...
    def kill(self, signal: Any = None) -> None: ...
    def logs(self, **kwargs: Any) -> bytes: ...
    def get_archive(self, path: str, **kwargs: Any) -> tuple[Iterator[bytes], dict[str, Any]]: ...
    def remove(self, **kwargs: Any) -> None: ...


class DockerLike(Protocol):
    containers: Any  # .create(image, command=..., **options) -> ContainerLike
    images: Any  # .get(name)


def mount_path_ok(mount_path: str) -> bool:
    """``in/...`` with no empty, ``.`` or ``..`` segments."""
    parts = mount_path.split("/")
    return parts[0] == "in" and len(parts) > 1 and all(p not in ("", ".", "..") for p in parts)


@dataclass
class StagedInput:
    mount_path: str
    local: Path
    size: int


@dataclass
class ContainerResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    truncated: bool
    duration_s: float
    killed: bool
    out_dir: Path | None  # extracted ``out/``; None when the outputs were refused
    notes: list[str] = field(default_factory=list)


async def stage_inputs(
    storage: Storage, inputs: list[RunInput], workdir: Path
) -> list[StagedInput]:
    """Download the inputs from storage into ``workdir/stage`` (kept out of the tar's ``in/``
    tree until it is built, so a bad path cannot escape)."""
    staged: list[StagedInput] = []
    seen: set[str] = set()
    for item in inputs:  # all paths checked before anything is downloaded
        if not mount_path_ok(item.mount_path):
            raise ExecutionError(f"bad mount path {item.mount_path!r}")
        if item.mount_path in seen:
            raise ExecutionError(f"duplicate mount path {item.mount_path!r}")
        seen.add(item.mount_path)
    root = workdir / "stage"
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    for index, item in enumerate(inputs):
        local = root / f"{index}.bin"
        size = 0
        with local.open("wb") as fh:
            async for chunk in storage.open(item.s3_key):
                fh.write(chunk)
                size += len(chunk)
        staged.append(StagedInput(item.mount_path, local, size))
    return staged


def build_tar(code: str, staged: list[StagedInput], target: Path) -> None:
    """``main.py`` and ``in/`` root-owned and read-only, ``out/`` writable for the sandbox
    user; the daemon extracts the archive as root, so the ownership sticks."""
    now = int(time.time())

    def root_owned(info: tarfile.TarInfo, mode: int) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        info.mode = mode
        info.mtime = now
        return info

    with tarfile.open(target, "w") as tar:
        source = code.encode("utf-8")
        info = root_owned(tarfile.TarInfo("main.py"), 0o444)
        info.size = len(source)
        tar.addfile(info, io.BytesIO(source))
        dirs_added: set[str] = set()

        def add_dir(path: str, *, mode: int = 0o555, uid: int = 0) -> None:
            if path in dirs_added:
                return
            parent = posixpath.dirname(path)
            if parent and parent not in dirs_added:
                add_dir(parent)
            dirs_added.add(path)
            info = root_owned(tarfile.TarInfo(path), mode)
            info.type = tarfile.DIRTYPE
            if uid:
                info.uid = info.gid = uid
                info.uname = info.gname = "sandbox"
            tar.addfile(info)

        add_dir("in")
        for item in staged:
            add_dir(posixpath.dirname(item.mount_path))
            info = root_owned(tarfile.TarInfo(item.mount_path), 0o444)
            info.size = item.size
            with item.local.open("rb") as fh:
                tar.addfile(info, fh)
        add_dir("out", mode=0o755, uid=SANDBOX_UID)


def _truncate(data: bytes) -> tuple[bytes, bool]:
    if len(data) <= LOG_LIMIT:
        return data, False
    return data[:LOG_LIMIT], True


def _extract_outputs(stream: Iterator[bytes], out_dir: Path, max_bytes: int) -> int:
    """Write the ``out/`` archive to disk (refusing it beyond ``max_bytes``) and extract the
    regular files into ``out_dir``. Returns the number of files."""
    archive = out_dir.parent / "out.tar"
    received = 0
    with archive.open("wb") as fh:
        for chunk in stream:
            received += len(chunk)
            if received > max_bytes + 64 * 1024:  # tar headers
                raise ExecutionError(f"outputs exceed {max_bytes // (1024 * 1024)} MB")
            fh.write(chunk)
    count = 0
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            relative = member.name.split("/", 1)[1] if "/" in member.name else ""
            if not relative or any(p in ("", ".", "..") for p in relative.split("/")):
                continue
            destination = out_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            with destination.open("wb") as fh:
                shutil.copyfileobj(extracted, fh)
            count += 1
    archive.unlink(missing_ok=True)
    return count


def container_options(request: RunRequest, settings: ExecutionSettings) -> dict[str, Any]:
    """The container's isolation and limits (docs/PLAN.md 7.12)."""
    limits = request.limits
    options: dict[str, Any] = {
        "command": ["python", f"{WORK}/main.py"],
        "working_dir": WORK,
        "user": f"{SANDBOX_UID}:{SANDBOX_UID}",
        "network_mode": "none",
        "read_only": True,
        "tmpfs": {"/tmp": f"rw,noexec,nosuid,size={settings.tmp_size_mb}m"},
        # an anonymous volume: the archive can be written before the start (the rootfs is
        # read-only), and it is removed with the container
        "mounts": [Mount(target=WORK, source=None, type="volume")],
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "pids_limit": limits.pids,
        "mem_limit": f"{limits.memory_mb}m",
        "memswap_limit": f"{limits.memory_mb}m",  # no swap on top of the memory limit
        "nano_cpus": int(limits.cpus * 1_000_000_000),
        "environment": {"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", "HOME": "/tmp"},
        "labels": {"elexifier.run_id": request.run_id, "elexifier.project_id": request.project_id},
        "detach": True,
        "auto_remove": False,
    }
    if settings.runtime:
        options["runtime"] = settings.runtime
    return options


def run_container(
    client: DockerLike,
    request: RunRequest,
    staged: list[StagedInput],
    workdir: Path,
    settings: ExecutionSettings,
) -> ContainerResult:
    """The synchronous part: tar in, container lifecycle, logs, tar out."""
    archive = workdir / "in.tar"
    build_tar(request.code, staged, archive)
    container: ContainerLike = client.containers.create(
        settings.image, **container_options(request, settings)
    )
    notes: list[str] = []
    try:
        with archive.open("rb") as fh:
            container.put_archive(WORK, fh)
        started = time.perf_counter()
        container.start()
        killed = False
        try:
            outcome = container.wait(timeout=request.limits.timeout_s)
            exit_code = int(outcome.get("StatusCode", 1))
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            killed = True
            with contextlib.suppress(Exception):  # already gone
                container.kill()
            exit_code = KILLED_EXIT_CODE
            notes.append(f"killed after {request.limits.timeout_s} s (timeout)")
        duration = round(time.perf_counter() - started, 3)
        stdout, cut_out = _truncate(container.logs(stdout=True, stderr=False) or b"")
        stderr, cut_err = _truncate(container.logs(stdout=False, stderr=True) or b"")
        out_dir: Path | None = workdir / "out"
        assert out_dir is not None
        out_dir.mkdir(exist_ok=True)
        try:
            stream, _stat = container.get_archive(f"{WORK}/out")
            _extract_outputs(stream, out_dir, request.limits.max_output_mb * 1024 * 1024)
        except ExecutionError as exc:
            notes.append(f"{exc}; nothing uploaded")
            out_dir = None
        return ContainerResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            truncated=cut_out or cut_err,
            duration_s=duration,
            killed=killed,
            out_dir=out_dir,
            notes=notes,
        )
    finally:
        with contextlib.suppress(Exception):
            container.remove(v=True, force=True)


async def _file_chunks(path: Path, size: int = 1024 * 1024) -> AsyncIterator[bytes]:
    with path.open("rb") as fh:
        while chunk := fh.read(size):
            yield chunk


async def upload_outputs(
    storage: Storage, project_id: str, run_id: str, out_dir: Path
) -> list[RunOutput]:
    outputs: list[RunOutput] = []
    paths = await asyncio.to_thread(lambda: sorted(p for p in out_dir.rglob("*") if p.is_file()))
    for path in paths:
        relative = path.relative_to(out_dir).as_posix()
        key = f"projects/{project_id}/artifacts/{run_id}/{relative}"
        content_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
        stat = await storage.put(key, _file_chunks(path), content_type)
        outputs.append(RunOutput(s3_key=key, name=relative, size=stat.size, sha256=stat.sha256))
    return outputs


async def execute(
    request: RunRequest,
    *,
    storage: Storage,
    client: DockerLike,
    settings: ExecutionSettings,
) -> RunResponse:
    """Stage inputs, run the container in a thread, upload ``out/``."""
    if request.language != "python":
        raise ExecutionError(f"unsupported language {request.language!r}")
    workdir = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="elexifier-sandbox-"))
    try:
        staged = await stage_inputs(storage, request.inputs, workdir)
        result = await asyncio.to_thread(run_container, client, request, staged, workdir, settings)
        outputs: list[RunOutput] = []
        if result.out_dir is not None:
            outputs = await upload_outputs(
                storage, request.project_id, request.run_id, result.out_dir
            )
        stderr = result.stderr.decode("utf-8", errors="replace")
        if result.notes:
            stderr = (stderr + "\n" if stderr else "") + "\n".join(
                f"[runner] {n}" for n in result.notes
            )
        return RunResponse(
            exit_code=result.exit_code,
            stdout=result.stdout.decode("utf-8", errors="replace"),
            stderr=stderr,
            truncated=result.truncated,
            duration_s=result.duration_s,
            outputs=outputs,
            killed=result.killed,
        )
    finally:
        await asyncio.to_thread(shutil.rmtree, workdir, ignore_errors=True)


__all__ = [
    "ContainerResult",
    "ExecutionError",
    "ExecutionSettings",
    "RunInput",
    "RunOutput",
    "RunRequest",
    "RunResponse",
    "build_tar",
    "container_options",
    "execute",
    "mount_path_ok",
    "run_container",
    "stage_inputs",
    "upload_outputs",
]
