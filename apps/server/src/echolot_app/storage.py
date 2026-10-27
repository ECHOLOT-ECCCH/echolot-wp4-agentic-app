"""Files on local disk under the data directory (docs/PLAN.md section 8).

Keys: ``workspaces/{workspace_id}/files/{file_id}/{filename}`` and
``workspaces/{workspace_id}/runs/{run_id}/result.jsonl``.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import anyio

CHUNK_SIZE = 64 * 1024
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._\-()+ ,@]+$")


class StorageError(Exception):
    pass


class NotFoundError(StorageError):
    pass


@dataclass(frozen=True)
class StorageStat:
    size: int
    etag: str | None = None
    content_type: str | None = None
    sha256: str | None = None  # set by ``put`` (computed while streaming), not by ``stat``


def file_key(workspace_id: str, file_id: str, filename: str) -> str:
    return f"workspaces/{workspace_id}/files/{file_id}/{safe_filename(filename)}"


def run_key(workspace_id: str, run_id: str, filename: str) -> str:
    return f"workspaces/{workspace_id}/runs/{run_id}/{safe_filename(filename)}"


def safe_filename(name: str) -> str:
    """Keep the extension, replace anything unusual, avoid path tricks."""
    base = os.path.basename(name.replace("\\", "/")).strip() or "file"
    cleaned = re.sub(r"[^A-Za-z0-9._\-()+ ,@]", "_", base)
    cleaned = cleaned.lstrip(".") or "file"
    return cleaned[:200]


def validate_key(key: str) -> str:
    if not key or key.startswith("/") or "\x00" in key:
        raise StorageError(f"invalid storage key: {key!r}")
    for segment in key.split("/"):
        if segment in ("", ".", "..") or not _SAFE_SEGMENT.match(segment):
            raise StorageError(f"invalid storage key segment: {segment!r}")
    return key


async def _iter_bytes(data: bytes | AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    if isinstance(data, bytes | bytearray | memoryview):
        yield bytes(data)
        return
    async for chunk in data:
        yield chunk


class LocalStorage:
    """Files under a root directory."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / validate_key(key)).resolve()
        if self.root not in path.parents:
            raise StorageError(f"key escapes storage root: {key!r}")
        return path

    async def put(
        self, key: str, data: bytes | AsyncIterator[bytes], content_type: str | None = None
    ) -> StorageStat:
        path = self._path(key)
        await anyio.Path(path.parent).mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        tmp = await anyio.to_thread.run_sync(_make_temp, path.parent)
        try:
            async with await anyio.open_file(tmp, "wb") as fh:
                async for chunk in _iter_bytes(data):
                    await fh.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            await anyio.to_thread.run_sync(os.replace, tmp, path)
        except BaseException:
            await anyio.Path(tmp).unlink(missing_ok=True)
            raise
        if content_type:
            await anyio.Path(_sidecar(path)).write_text(content_type, encoding="utf-8")
        return StorageStat(size=size, sha256=digest.hexdigest(), content_type=content_type)

    async def open(
        self, key: str, byte_range: tuple[int, int] | None = None
    ) -> AsyncIterator[bytes]:
        path = self._path(key)
        if not await anyio.Path(path).is_file():
            raise NotFoundError(key)
        start, end = 0, None
        if byte_range is not None:
            start, end = byte_range
        async with await anyio.open_file(path, "rb") as fh:
            if start:
                await fh.seek(start)
            remaining = None if end is None else end - start + 1
            while remaining is None or remaining > 0:
                to_read = CHUNK_SIZE if remaining is None else min(CHUNK_SIZE, remaining)
                chunk = await fh.read(to_read)
                if not chunk:
                    break
                if remaining is not None:
                    remaining -= len(chunk)
                yield chunk

    async def stat(self, key: str) -> StorageStat:
        apath = anyio.Path(self._path(key))
        if not await apath.is_file():
            raise NotFoundError(key)
        st = await apath.stat()
        return StorageStat(size=st.st_size, etag=None, content_type=await _read_sidecar(apath))

    async def delete(self, key: str) -> None:
        path = self._path(key)
        await anyio.Path(path).unlink(missing_ok=True)
        await anyio.Path(_sidecar(path)).unlink(missing_ok=True)
        await anyio.to_thread.run_sync(_prune_empty_dirs, path.parent, self.root)

    async def delete_prefix(self, prefix: str) -> None:
        """Delete everything under a key prefix (a workspace, a file's directory)."""
        path = self._path(prefix.rstrip("/"))
        await anyio.to_thread.run_sync(lambda: shutil.rmtree(path, ignore_errors=True))
        await anyio.to_thread.run_sync(_prune_empty_dirs, path.parent, self.root)

    def local_path(self, key: str) -> Path:
        """The file behind a key, for readers that need a real path (record normalization)."""
        return self._path(key)

    def wipe(self) -> None:
        """Delete everything (tests only)."""
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)


def _sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".content-type")


async def _read_sidecar(path: anyio.Path) -> str | None:
    sidecar = anyio.Path(_sidecar(Path(path)))
    if not await sidecar.is_file():
        return None
    return (await sidecar.read_text(encoding="utf-8")).strip()


def _make_temp(directory: Path) -> Path:
    fd, name = tempfile.mkstemp(dir=directory, prefix=".upload-")
    os.close(fd)
    return Path(name)


def _prune_empty_dirs(start: Path, root: Path) -> None:
    parent = start
    while parent != root and parent.exists() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent
