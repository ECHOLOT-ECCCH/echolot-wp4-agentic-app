"""S3 backend tests run only when an S3 endpoint is configured and reachable (Garage in dev)."""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import pytest

from elexifier_core.storage import NotFoundError, S3Storage

pytestmark = pytest.mark.skipif(not os.environ.get("S3_ENDPOINT"), reason="S3_ENDPOINT not set")


def _reachable(url: str) -> bool:
    parsed = urlparse(url)
    try:
        with socket.create_connection((parsed.hostname or "localhost", parsed.port or 80), 2):
            return True
    except OSError:
        return False


@pytest.fixture
async def s3() -> S3Storage:
    endpoint = os.environ["S3_ENDPOINT"]
    if not _reachable(endpoint):
        pytest.skip(f"S3 endpoint {endpoint} not reachable")
    storage = S3Storage(
        endpoint=endpoint,
        bucket=os.environ.get("S3_BUCKET", "elexifier"),
        access_key=os.environ.get("S3_ACCESS_KEY"),
        secret_key=os.environ.get("S3_SECRET_KEY"),
        region=os.environ.get("S3_REGION", "garage"),
    )
    await storage.ensure_bucket()
    return storage


async def _chunks(data: bytes, size: int) -> AsyncIterator[bytes]:
    for i in range(0, len(data), size):
        yield data[i : i + size]


async def test_s3_round_trip(s3: S3Storage) -> None:
    key = f"projects/test/source/{uuid.uuid4()}/a.txt"
    await s3.put(key, b"hello s3", content_type="text/plain")
    assert b"".join([c async for c in s3.open(key)]) == b"hello s3"
    assert b"".join([c async for c in s3.open(key, byte_range=(6, 7))]) == b"s3"
    head = await s3.stat(key)
    assert head.size == 8
    url = await s3.presigned_get_url(key, 60)
    assert url and key in url
    await s3.delete(key)
    with pytest.raises(NotFoundError):
        await s3.stat(key)


async def test_s3_multipart_streaming(s3: S3Storage) -> None:
    payload = os.urandom(9 * 1024 * 1024)  # forces two parts
    key = f"projects/test/source/{uuid.uuid4()}/big.bin"
    stat = await s3.put(key, _chunks(payload, 1_000_000))
    assert stat.size == len(payload)
    assert b"".join([c async for c in s3.open(key)]) == payload
    await s3.delete(key)
