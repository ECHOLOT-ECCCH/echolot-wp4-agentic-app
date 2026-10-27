"""FastAPI application factory."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from echolot_app import __version__
from echolot_app.logging import configure_logging
from echolot_app.routers import chat, files, scripts, workspaces
from echolot_app.settings import Settings, get_settings
from echolot_app.state import AppState, build_state
from echolot_app.storage import NotFoundError, StorageError

log = structlog.get_logger(__name__)


class HealthOut(BaseModel):
    status: str
    database: str


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.log_json)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = build_state(settings)
        await state.db.create_all()
        app.state.app = state
        log.info("app.started", version=__version__, data_dir=str(settings.data_dir))
        try:
            yield
        finally:
            await state.runtime.drain()
            await state.db.dispose()

    app = FastAPI(
        title="ECHOLOT Workbench API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    @app.exception_handler(NotFoundError)
    async def _not_found(_request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": f"stored file missing: {exc}"}, status_code=404)

    @app.exception_handler(StorageError)
    async def _storage_error(_request: Request, exc: StorageError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/healthz", response_model=HealthOut, tags=["ops"])
    async def healthz(request: Request) -> HealthOut:
        state: AppState = request.app.state.app
        database = "ok"
        try:
            async with state.db.session() as session:
                await session.execute(text("SELECT 1"))
        except Exception as exc:
            database = f"error: {type(exc).__name__}"
        return HealthOut(status="ok" if database == "ok" else "degraded", database=database)

    app.include_router(workspaces.router)
    app.include_router(files.router)
    app.include_router(scripts.router)
    app.include_router(chat.router)
    return app


app = create_app()
