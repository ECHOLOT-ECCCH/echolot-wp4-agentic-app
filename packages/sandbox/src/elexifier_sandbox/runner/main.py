"""Sandbox runner service (docs/PLAN.md 7.12).

Milestone 0 ships the service skeleton with the request/response contract; execution arrives in
milestone 3. Until then ``POST /run`` answers 501 so callers can integrate against the real shape.
"""

from __future__ import annotations

import os
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

RUNNER_TOKEN = os.environ.get("SANDBOX_RUNNER_TOKEN", "")
SANDBOX_RUNTIME = os.environ.get("SANDBOX_RUNTIME", "runc")


class RunInput(BaseModel):
    s3_key: str
    mount_path: str = Field(pattern=r"^in/[A-Za-z0-9._\-/ ]+$")


class RunLimits(BaseModel):
    timeout_s: int = Field(default=300, ge=1, le=3600)
    cpus: float = Field(default=2, gt=0, le=8)
    memory_mb: int = Field(default=2048, ge=128, le=16384)
    pids: int = Field(default=256, ge=16, le=4096)
    max_output_mb: int = Field(default=200, ge=1, le=4096)


class RunRequest(BaseModel):
    run_id: str
    project_id: str
    language: str = "python"
    code: str
    inputs: list[RunInput] = Field(default_factory=list)
    limits: RunLimits = Field(default_factory=RunLimits)


class RunOutput(BaseModel):
    s3_key: str
    name: str
    size: int


class RunResponse(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool
    duration_s: float
    outputs: list[RunOutput]


def require_token(x_runner_token: str | None = Header(default=None)) -> None:
    if (
        not RUNNER_TOKEN
        or not x_runner_token
        or not secrets.compare_digest(x_runner_token, RUNNER_TOKEN)
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid runner token")


app = FastAPI(title="Elexifier sandbox runner", version="0.1.0")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "runtime": SANDBOX_RUNTIME, "execution": "not available until M3"}


@app.post("/run", response_model=RunResponse, dependencies=[Depends(require_token)])
async def run(_request: RunRequest) -> RunResponse:
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED, "sandboxed execution arrives in milestone 3"
    )
