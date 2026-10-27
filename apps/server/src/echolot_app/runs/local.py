"""``LocalExecutor``: the runner CLI as a subprocess on the app host (dev, tests, demos).

The subprocess gets the app's environment plus the variables of ``ECHO_LOCAL_RUNNER_ENV_FILE``
(default ``./runner.env``, read at every submit), which is where the service keys go: the app's
``.env`` is read into its settings, not into its environment. It reads the services from
``ECHO_LOCAL_SERVICES_FILE``; on this machine the app host is also the runner host. A run whose
process is not known any more (the app restarted) reports CRASHED.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from echolot_app.runs.executor import ExecState

STDERR_TAIL = 2000


@dataclass
class _Job:
    process: asyncio.subprocess.Process
    workdir: Path
    cancelled: bool = False


class LocalExecutor:
    name = "local"

    def __init__(
        self, workdir: Path, services_file: Path | None = None, env_file: Path | None = None
    ) -> None:
        # absolute: the subprocess starts in its own directory, a relative data dir would miss
        self._root = workdir.resolve()
        self._services_file = services_file
        self._env_file = env_file
        self._jobs: dict[str, _Job] = {}

    async def submit(self, run_id: str, params: dict[str, Any]) -> str:
        workdir = self._root / run_id
        workdir.mkdir(parents=True, exist_ok=True)
        params_path = workdir / "params.json"
        params_path.write_text(json.dumps(params), encoding="utf-8")
        params_path.chmod(0o600)  # holds the run token
        env = {**os.environ, **read_env_file(self._env_file)}
        if self._services_file is not None:
            env["RUNNER_SERVICES_FILE"] = str(self._services_file.resolve())
        with (workdir / "stdout.log").open("wb") as out, (workdir / "stderr.log").open("wb") as err:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "echolot_runner.cli",
                str(params_path),
                cwd=workdir,
                env=env,
                stdout=out,
                stderr=err,
            )
        self._jobs[run_id] = _Job(process=process, workdir=workdir)
        return run_id

    async def status(self, external_id: str) -> ExecState:
        job = self._jobs.get(external_id)
        if job is None:
            return ExecState("CRASHED", "the local runner process is gone (was the app restarted?)")
        code = job.process.returncode
        if code is None:
            return ExecState("CANCELLING" if job.cancelled else "RUNNING")
        if job.cancelled:
            return ExecState("CANCELLED")
        if code == 0:
            return ExecState("COMPLETED")
        return ExecState("FAILED", _failure(job.workdir, code))

    async def cancel(self, external_id: str) -> None:
        job = self._jobs.get(external_id)
        if job is None or job.process.returncode is not None:
            return
        job.cancelled = True
        with contextlib.suppress(ProcessLookupError):
            job.process.terminate()

    async def aclose(self) -> None:
        """Stop what is still running (the app is shutting down)."""
        for job in self._jobs.values():
            if job.process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    job.process.terminate()
        for job in self._jobs.values():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(job.process.wait(), timeout=5)


def read_env_file(path: Path | None) -> dict[str, str]:
    """``KEY=VALUE`` lines (optionally ``export KEY=VALUE``, values optionally quoted); blank
    lines and ``#`` comments are skipped. A missing file is empty."""
    if path is None or not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key.isidentifier():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def _failure(workdir: Path, code: int) -> str:
    """The CLI's reason ("run failed: ..."), else the end of its stderr."""
    try:
        text = (workdir / "stderr.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for line in reversed(text.splitlines()):
        if line.startswith("run failed: "):
            return line.removeprefix("run failed: ")
    tail = text.strip()[-STDERR_TAIL:]
    return f"the runner exited with code {code}" + (f": {tail}" if tail else "")
