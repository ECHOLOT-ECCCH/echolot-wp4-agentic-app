"""The flow's body, called through ``.fn`` against the real HTTP fakes of test_cli. A real
Prefect server cannot be started in this environment: Prefect 3.8.8's server needs
fastapi<0.150 (docs/DECISIONS.md, M2.1); the flow on a server is checked in M2.2 and M2.3."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from echolot_runner.flow import FLOW_NAME, run_script
from echolot_runner.harness import RunFailed

from .conftest import TOKEN
from .test_cli import Fake
from .test_cli import server as server  # the fixture


def _params(server: str, script: str) -> dict[str, object]:
    return {
        "run_id": "flow1",
        "script": script,
        "services": ["upper"],
        "input_url": f"{server}/runner/runs/flow1/input",
        "progress_url": f"{server}/runner/runs/flow1/progress",
        "result_url": f"{server}/runner/runs/flow1/result",
        "token": TOKEN,
    }


def test_the_flow_is_named_for_the_deployment() -> None:
    assert run_script.name == FLOW_NAME == "run-script"


def test_the_flow_runs_the_harness(
    server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "services.toml").write_text(f'[upper]\nurl = "{server}/upper"\n', encoding="utf-8")
    monkeypatch.setenv("RUNNER_SERVICES_FILE", str(tmp_path / "services.toml"))
    script = (
        "def process(record, ctx):\n"
        '    return {"upper": ctx.service("upper").post("/", json=record)["name"]}\n'
    )
    summary = run_script.fn(_params(server, script))
    assert summary["total"] == 1000 and summary["ok"] == 1000
    assert json.loads(Fake.result.decode().splitlines()[0])["upper"] == "PERSON 0"


def test_a_run_level_failure_fails_the_flow(server: str) -> None:
    with pytest.raises(RunFailed, match="the script does not load"):
        run_script.fn({**_params(server, "def process(record, ctx)\n"), "services": []})
