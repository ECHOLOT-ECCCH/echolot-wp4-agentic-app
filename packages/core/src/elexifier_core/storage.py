"""Object storage behind one interface: local disk for development, S3 (Garage) in production.

Key scheme: ``projects/{project_id}/{kind}/{file_id}/{filename}`` (see docs/PLAN.md 7.5).
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
from typing import Any, Protocol, runtime_checkable

import aioboto3
import anyio

from elexifier_core.settings import Settings, get_settings

CHUNK_SIZE = 64 * 1024
MULTIPART_PART_SIZE = 8 * 1024 * 1024
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


def make_key(project_id: str, kind: str, file_id: str, filename: str) -> str:
    return f"projects/{project_id}/{kind}/{file_id}/{safe_filename(filename)}"


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


@runtime_checkable
class Storage(Protocol):
    async def put(
        self, key: str, data: bytes | AsyncIterator[bytes], content_type: str | None = None
    ) -> StorageStat: ...

    def open(self, key: str, byte_range: tuple[int, int] | None = None) -> AsyncIterator[bytes]: ...

    async def stat(self, key: str) -> StorageStat: ...

    async def delete(self, key: str) -> None: ...

    async def presigned_get_url(self, key: str, ttl_s: int) -> str | None: ...


class LocalStorage:
    """Files under a root directory. ``etag`` is the sha256 of the content."""

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
        return StorageStat(size=size, etag=digest.hexdigest(), content_type=content_type)

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

    async def presigned_get_url(self, key: str, ttl_s: int) -> str | None:
        return None

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


class S3Storage:
    """S3-compatible storage (Garage, AWS, Ceph RGW). Streams uploads with multipart when needed."""

    def __init__(
        self,
        endpoint: str | None,
        bucket: str,
        access_key: str | None,
        secret_key: str | None,
        region: str = "garage",
    ) -> None:
        self.bucket = bucket
        self._session = aioboto3.Session(
            aws_access_key_id=access_key, aws_secret_access_key=secret_key, region_name=region
        )
        self._client_kwargs: dict[str, Any] = {"service_name": "s3"}
        if endpoint:
            self._client_kwargs["endpoint_url"] = endpoint

    def _client(self) -> Any:
        return self._session.client(**self._client_kwargs)

    async def ensure_bucket(self) -> None:
        async with self._client() as s3:
            try:
                await s3.head_bucket(Bucket=self.bucket)
            except s3.exceptions.ClientError:
                await s3.create_bucket(Bucket=self.bucket)

    async def put(
        self, key: str, data: bytes | AsyncIterator[bytes], content_type: str | None = None
    ) -> StorageStat:
        validate_key(key)
        extra: dict[str, Any] = {"ContentType": content_type} if content_type else {}
        digest = hashlib.sha256()
        if isinstance(data, bytes | bytearray | memoryview):
            body = bytes(data)
            digest.update(body)
            async with self._client() as s3:
                await s3.put_object(Bucket=self.bucket, Key=key, Body=body, **extra)
            return StorageStat(size=len(body), etag=digest.hexdigest(), content_type=content_type)

        async with self._client() as s3:
            upload = await s3.create_multipart_upload(Bucket=self.bucket, Key=key, **extra)
            upload_id = upload["UploadId"]
            parts: list[dict[str, Any]] = []
            buffer = bytearray()
            size = 0
            try:
                async for chunk in data:
                    buffer.extend(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                    while len(buffer) >= MULTIPART_PART_SIZE:
                        part = bytes(buffer[:MULTIPART_PART_SIZE])
                        del buffer[:MULTIPART_PART_SIZE]
                        parts.append(
                            await _upload_part(s3, self.bucket, key, upload_id, parts, part)
                        )
                if buffer or not parts:
                    parts.append(
                        await _upload_part(s3, self.bucket, key, upload_id, parts, bytes(buffer))
                    )
                await s3.complete_multipart_upload(
                    Bucket=self.bucket,
                    Key=key,
                    UploadId=upload_id,
                    MultipartUpload={"Parts": parts},
                )
            except BaseException:
                await s3.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
                raise
        return StorageStat(size=size, etag=digest.hexdigest(), content_type=content_type)

    async def open(
        self, key: str, byte_range: tuple[int, int] | None = None
    ) -> AsyncIterator[bytes]:
        validate_key(key)
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if byte_range is not None:
            kwargs["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
        async with self._client() as s3:
            try:
                resp = await s3.get_object(**kwargs)
            except s3.exceptions.NoSuchKey as exc:
                raise NotFoundError(key) from exc
            async with resp["Body"] as body:
                while True:
                    chunk = await body.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk

    async def stat(self, key: str) -> StorageStat:
        validate_key(key)
        async with self._client() as s3:
            try:
                head = await s3.head_object(Bucket=self.bucket, Key=key)
            except s3.exceptions.ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                    raise NotFoundError(key) from exc
                raise
        return StorageStat(
            size=int(head["ContentLength"]),
            etag=(head.get("ETag") or "").strip('"') or None,
            content_type=head.get("ContentType"),
        )

    async def delete(self, key: str) -> None:
        validate_key(key)
        async with self._client() as s3:
            await s3.delete_object(Bucket=self.bucket, Key=key)

    async def presigned_get_url(self, key: str, ttl_s: int) -> str | None:
        validate_key(key)
        async with self._client() as s3:
            url: str = await s3.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=ttl_s
            )
            return url


async def _upload_part(
    s3: Any, bucket: str, key: str, upload_id: str, parts: list[dict[str, Any]], body: bytes
) -> dict[str, Any]:
    number = len(parts) + 1
    resp = await s3.upload_part(
        Bucket=bucket, Key=key, UploadId=upload_id, PartNumber=number, Body=body
    )
    return {"ETag": resp["ETag"], "PartNumber": number}


def build_storage(settings: Settings | None = None) -> Storage:
    settings = settings or get_settings()
    if settings.storage_backend == "s3":
        return S3Storage(
            endpoint=settings.s3_endpoint,
            bucket=settings.s3_bucket,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            region=settings.s3_region,
        )
    return LocalStorage(settings.storage_local_root)
