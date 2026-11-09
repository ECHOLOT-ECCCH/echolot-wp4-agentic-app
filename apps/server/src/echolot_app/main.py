"""FastAPI application factory."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from starlette.types import ASGIApp, Receive, Scope, Send

from echolot_app import __version__
from echolot_app.auth import CSRF_HEADER
from echolot_app.logging import configure_logging
from echolot_app.routers import (
    admin,
    auth,
    chat,
    files,
    models,
    runner_io,
    runs,
    scripts,
    services,
    workspaces,
)
from echolot_app.runs.prefect import display_url
from echolot_app.settings import Settings, get_settings
from echolot_app.state import AppState, build_state
from echolot_app.storage import NotFoundError, StorageError

log = structlog.get_logger(__name__)


class HealthOut(BaseModel):
    status: str
    database: str


_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_CSRF_HEADER = CSRF_HEADER.lower().encode()


class CsrfGuard:
    """Unsafe requests to /api must carry the X-Echolot-Request header (docs/PLAN.md 7.11).

    The session cookie is SameSite=Lax; this header closes the remaining gaps: a form on another
    site cannot set it, and a script there would need a CORS preflight this app never grants.
    Pure ASGI, so streamed chat answers and uploads pass through untouched."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] not in _SAFE_METHODS
            and scope["path"].startswith("/api/")
            and not any(name == _CSRF_HEADER for name, _ in scope["headers"])
        ):
            response = JSONResponse(
                {"detail": f"Missing the {CSRF_HEADER} header"}, status_code=403
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.log_json)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = build_state(settings)
        await state.db.create_all()
        app.state.app = state
        state.poller.start()
        runs = {"executor": settings.executor}
        if settings.executor == "prefect":  # where runs go; "echolot-admin check-runs" tests it
            runs["prefect"] = display_url(settings.prefect_api_url)
            runs["public_url"] = settings.public_url
        log.info("app.started", version=__version__, data_dir=str(settings.data_dir), **runs)
        try:
            yield
        finally:
            await state.poller.stop()
            await state.runtime.drain()
            await state.executor.aclose()
            await state.catalog.aclose()
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

    app.add_middleware(CsrfGuard)

    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(models.router)
    app.include_router(workspaces.router)
    app.include_router(files.router)
    app.include_router(scripts.router)
    app.include_router(runs.router)
    app.include_router(runner_io.router)
    app.include_router(services.router)
    app.include_router(chat.router)
    return app


app = create_app()
