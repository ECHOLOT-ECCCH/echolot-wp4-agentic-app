from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from echolot_app.runs.local import LocalExecutor


async def test_a_relative_data_dir_still_finds_the_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    executor = LocalExecutor(Path("data/runner"))
    params = {
        "run_id": "r1",
        "script": "def process(record, ctx):\n    return {}\n",
        "input_url": "http://127.0.0.1:9/input",  # nothing listens: the download fails
        "progress_url": "http://127.0.0.1:9/progress",
        "result_url": "http://127.0.0.1:9/result",
        "token": "t",
    }
    external = await executor.submit("r1", params)
    state = await executor.status(external)
    for _ in range(200):
        state = await executor.status(external)
        if state.kind != "RUNNING":
            break
        await asyncio.sleep(0.05)
    assert state.kind == "FAILED"
    assert state.message == "the input could not be downloaded (ConnectError)"
    assert (await executor.status("unknown")).kind == "CRASHED"
