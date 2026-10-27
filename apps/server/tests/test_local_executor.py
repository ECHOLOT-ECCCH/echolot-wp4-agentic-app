from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from echolot_app.runs.executor import ExecState
from echolot_app.runs.local import LocalExecutor, read_env_file


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


async def _final_state(executor: LocalExecutor, external: str) -> ExecState:
    state = await executor.status(external)
    for _ in range(200):
        if state.kind != "RUNNING":
            break
        await asyncio.sleep(0.05)
        state = await executor.status(external)
    return state


async def test_the_runner_env_file_reaches_the_subprocess(tmp_path: Path) -> None:
    services = tmp_path / "services.toml"
    services.write_text(
        '[svc]\nurl = "http://127.0.0.1:9"\nauth_header = "X-API-Key"\n'
        'key_env = "ECHO_TEST_RUNNER_KEY"\n',
        encoding="utf-8",
    )
    params = {
        "run_id": "r2",
        "script": "def process(record, ctx):\n    return {}\n",
        "services": ["svc"],
        "input_url": "http://127.0.0.1:9/input",
        "progress_url": "http://127.0.0.1:9/progress",
        "result_url": "http://127.0.0.1:9/result",
        "token": "t",
    }
    env_file = tmp_path / "runner.env"
    without = LocalExecutor(tmp_path / "a", services, env_file)
    state = await _final_state(without, await without.submit("r2", params))
    assert state.message == (
        "service 'svc' needs its key in the environment variable ECHO_TEST_RUNNER_KEY"
    )
    env_file.write_text("# keys for the runner\nexport ECHO_TEST_RUNNER_KEY='s3cret'\n")
    with_key = LocalExecutor(tmp_path / "b", services, env_file)
    state = await _final_state(with_key, await with_key.submit("r2", params))
    assert state.message == "the input could not be downloaded (ConnectError)"  # got past keys


def test_env_file_lines(tmp_path: Path) -> None:
    path = tmp_path / "runner.env"
    path.write_text(
        'A=1\nexport B="two words"\n# C=3\n\nnot a line\nD = x=y\n1BAD=no\n', encoding="utf-8"
    )
    assert read_env_file(path) == {"A": "1", "B": "two words", "D": "x=y"}
    assert read_env_file(tmp_path / "missing.env") == {}
