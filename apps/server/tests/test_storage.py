from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from echolot_app.storage import LocalStorage, NotFoundError, StorageError, file_key, safe_filename


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


async def test_put_open_stat_delete(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    key = file_key("w1", "f1", "data.csv")
    stat = await storage.put(key, _chunks(b"a,b\n", b"1,2\n"), content_type="text/csv")
    assert stat.size == 8
    assert stat.sha256 == hashlib.sha256(b"a,b\n1,2\n").hexdigest()
    assert b"".join([c async for c in storage.open(key)]) == b"a,b\n1,2\n"
    assert b"".join([c async for c in storage.open(key, (4, 6))]) == b"1,2"
    assert (await storage.stat(key)).content_type == "text/csv"
    await storage.delete(key)
    with pytest.raises(NotFoundError):
        await storage.stat(key)
    assert not (tmp_path / "workspaces").exists()  # empty directories pruned


def test_keys_cannot_escape_the_root(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    with pytest.raises(StorageError):
        storage.local_path("workspaces/../../etc/passwd")
    assert safe_filename("../../evil name?.csv") == "evil name_.csv"
