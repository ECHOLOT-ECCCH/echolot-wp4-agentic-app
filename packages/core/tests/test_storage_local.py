from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from elexifier_core.storage import (
    LocalStorage,
    NotFoundError,
    StorageError,
    make_key,
    safe_filename,
    validate_key,
)


async def _chunks(data: bytes, size: int) -> AsyncIterator[bytes]:
    for i in range(0, len(data), size):
        yield data[i : i + size]


async def _collect(it: AsyncIterator[bytes]) -> bytes:
    return b"".join([c async for c in it])


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "store")


async def test_put_open_stat_delete(storage: LocalStorage) -> None:
    key = make_key("p1", "source", "f1", "dict.xml")
    stat = await storage.put(key, b"<a>hello</a>", content_type="application/xml")
    assert stat.size == 12
    assert stat.etag and len(stat.etag) == 64
    assert await _collect(storage.open(key)) == b"<a>hello</a>"
    head = await storage.stat(key)
    assert head.size == 12
    assert head.content_type == "application/xml"
    await storage.delete(key)
    with pytest.raises(NotFoundError):
        await storage.stat(key)
    with pytest.raises(NotFoundError):
        await _collect(storage.open(key))
    # parent directories are pruned, root remains
    assert storage.root.exists()
    assert not any(storage.root.iterdir())


async def test_streaming_put_and_ranges(storage: LocalStorage) -> None:
    payload = bytes(range(256)) * 1000  # 256 kB
    key = "projects/p/source/f/big.bin"
    stat = await storage.put(key, _chunks(payload, 7000))
    assert stat.size == len(payload)
    assert await _collect(storage.open(key)) == payload
    assert await _collect(storage.open(key, byte_range=(10, 19))) == payload[10:20]
    assert (
        await _collect(storage.open(key, byte_range=(len(payload) - 5, len(payload) - 1)))
        == (payload[-5:])
    )


async def test_failed_put_leaves_no_partial_file(storage: LocalStorage) -> None:
    async def broken() -> AsyncIterator[bytes]:
        yield b"partial"
        raise RuntimeError("upload interrupted")

    with pytest.raises(RuntimeError):
        await storage.put("projects/p/source/f/x.bin", broken())
    assert not list(storage.root.rglob("*.bin"))
    assert not list(storage.root.rglob(".upload-*"))


def test_key_validation() -> None:
    assert validate_key("projects/p/source/f/a b.xml") == "projects/p/source/f/a b.xml"
    for bad in ["../x", "a//b", "/abs", "a/../b", "a/b\x00c", ""]:
        with pytest.raises(StorageError):
            validate_key(bad)


def test_safe_filename() -> None:
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("Slovar (2. izdaja).pdf") == "Slovar (2. izdaja).pdf"
    assert safe_filename("čšž.xml") == "___.xml"
    assert safe_filename(".hidden") == "hidden"
    assert safe_filename("") == "file"


async def test_key_cannot_escape_root(storage: LocalStorage) -> None:
    with pytest.raises(StorageError):
        await storage.put("projects/../../outside.txt", b"x")
