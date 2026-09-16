"""The sandbox runner's request and response (docs/PLAN.md 7.12), shared by the runner and its
clients (the agent's ``run_code`` tool, the conversion jobs replaying custom steps)."""

from __future__ import annotations

from pydantic import BaseModel, Field


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
    run_id: str = Field(pattern=r"^[A-Za-z0-9._\-]{1,80}$")
    project_id: str = Field(pattern=r"^[A-Za-z0-9._\-]{1,80}$")
    language: str = "python"
    code: str = Field(max_length=512 * 1024)
    inputs: list[RunInput] = Field(default_factory=list)
    limits: RunLimits = Field(default_factory=RunLimits)


class RunOutput(BaseModel):
    s3_key: str
    name: str
    size: int
    sha256: str | None = None


class RunResponse(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool
    duration_s: float
    outputs: list[RunOutput]
    killed: bool = False  # stopped at the timeout


__all__ = ["RunInput", "RunLimits", "RunOutput", "RunRequest", "RunResponse"]
