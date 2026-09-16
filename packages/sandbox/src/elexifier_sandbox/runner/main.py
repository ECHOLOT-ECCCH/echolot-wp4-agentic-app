"""Sandbox runner service (docs/PLAN.md 7.12): the only process that talks to the Docker
daemon. ``POST /run`` executes one snippet in a fresh container and answers with its exit code,
logs and the outputs uploaded to storage."""

from __future__ import annotations

import asyncio
import secrets
from functools import lru_cache
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, status

from elexifier_core.settings import Settings, get_settings
from elexifier_core.storage import Storage, build_storage
from elexifier_sandbox.runner.execute import (
    ExecutionError,
    ExecutionSettings,
    RunRequest,
    RunResponse,
    execute,
)


@lru_cache(maxsize=1)
def runner_settings() -> Settings:
    return get_settings()


@lru_cache(maxsize=1)
def runner_storage() -> Storage:
    return build_storage(runner_settings())


def execution_settings(settings: Settings) -> ExecutionSettings:
    return ExecutionSettings(
        image=settings.sandbox_image,
        runtime="runsc" if settings.sandbox_runtime == "runsc" else None,
    )


def docker_client() -> Any:
    """A docker-py client from the environment (DOCKER_HOST or the default socket)."""
    import docker

    return docker.from_env(timeout=30)


def require_token(x_runner_token: str | None = Header(default=None)) -> None:
    token = runner_settings().sandbox_runner_token
    if not token or not x_runner_token or not secrets.compare_digest(x_runner_token, token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid runner token")


app = FastAPI(title="Elexifier sandbox runner", version="0.2.0")


def _daemon_status(settings: Settings) -> dict[str, Any]:
    """Whether the daemon answers and the sandbox image is present."""
    import docker.errors

    try:
        client = docker_client()
        client.ping()
        try:
            client.images.get(settings.sandbox_image)
            image = "present"
        except docker.errors.ImageNotFound:
            image = "missing"
        return {"docker": "ok", "image": image}
    except Exception as exc:  # no daemon, no socket
        return {"docker": f"unavailable: {type(exc).__name__}", "image": "unknown"}


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    settings = runner_settings()
    state = await asyncio.to_thread(_daemon_status, settings)
    return {
        "status": "ok",
        "runtime": settings.sandbox_runtime,
        "image_name": settings.sandbox_image,
        **state,
    }


@app.post("/run", response_model=RunResponse, dependencies=[Depends(require_token)])
async def run(request: RunRequest) -> RunResponse:
    settings = runner_settings()
    try:
        client = await asyncio.to_thread(docker_client)
        return await execute(
            request,
            storage=runner_storage(),
            client=client,
            settings=execution_settings(settings),
        )
    except ExecutionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except Exception as exc:  # daemon or image trouble: the caller can report it
        import docker.errors

        if isinstance(exc, docker.errors.ImageNotFound):
            detail = (
                f"sandbox image {settings.sandbox_image!r} is missing; run `make sandbox-image`"
            )
        else:
            detail = f"sandbox unavailable: {type(exc).__name__}: {exc}"[:500]
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail) from exc
