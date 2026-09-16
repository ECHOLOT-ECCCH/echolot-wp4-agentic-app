"""Test helpers: a runner client that executes snippets in the test process (no container).

Only for tests: it runs the snippet with the current interpreter in a temporary directory, with
the inputs read from the given storage and the outputs uploaded back the way the real runner
does. Nothing is isolated."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

from elexifier_agent.sandbox import RunnerClient
from elexifier_core.sandbox_contract import RunRequest, RunResponse
from elexifier_core.storage import Storage


def local_runner(storage: Storage, *, token: str = "test-token") -> RunnerClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("x-runner-token") != token:
            return httpx.Response(401, json={"detail": "invalid runner token"})
        run = RunRequest.model_validate(json.loads(request.content))
        response = await execute_locally(run, storage)
        return httpx.Response(200, json=response.model_dump())

    return RunnerClient("http://local-runner", token, transport=httpx.MockTransport(handler))


async def execute_locally(run: RunRequest, storage: Storage) -> RunResponse:
    workdir = Path(tempfile.mkdtemp(prefix="elexifier-local-run-"))
    try:
        (workdir / "in").mkdir()
        (workdir / "out").mkdir()
        (workdir / "main.py").write_text(run.code, encoding="utf-8")
        for item in run.inputs:
            target = workdir / item.mount_path
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as fh:
                async for chunk in storage.open(item.s3_key):
                    fh.write(chunk)
        started = time.perf_counter()
        killed = False
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "main.py"],
                cwd=workdir,
                capture_output=True,
                timeout=run.limits.timeout_s,
                check=False,
            )
            exit_code = completed.returncode
            stdout, stderr = completed.stdout, completed.stderr
        except subprocess.TimeoutExpired as exc:
            killed, exit_code = True, 137
            stdout = exc.stdout or b""
            stderr = (
                exc.stderr or b""
            ) + f"\n[runner] killed after {run.limits.timeout_s} s".encode()
        duration = round(time.perf_counter() - started, 3)
        outputs = []
        for path in sorted(p for p in (workdir / "out").rglob("*") if p.is_file()):
            relative = path.relative_to(workdir / "out").as_posix()
            key = f"projects/{run.project_id}/artifacts/{run.run_id}/{relative}"
            stat = await storage.put(
                key, path.read_bytes(), content_type="application/octet-stream"
            )
            outputs.append(
                {"s3_key": key, "name": relative, "size": stat.size, "sha256": stat.sha256}
            )
        return RunResponse(
            exit_code=exit_code,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            truncated=False,
            duration_s=duration,
            outputs=outputs,  # type: ignore[arg-type]
            killed=killed,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


__all__ = ["execute_locally", "local_runner"]
