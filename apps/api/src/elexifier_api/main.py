"""FastAPI application factory."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

import procrastinate
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from elexifier_agent.catalog import ModelCatalog
from elexifier_agent.runtime import PydanticAIRuntime
from elexifier_api import __version__
from elexifier_api import jobs as _jobs  # noqa: F401  # registers tasks on the shared app
from elexifier_api.auth.oidc import OidcClient
from elexifier_api.auth.session import SessionStore
from elexifier_api.routers import auth, chat, credentials, documents, events, files, me, projects
from elexifier_api.schemas import HealthOut
from elexifier_api.state import AppState
from elexifier_core.crypto import SecretBox
from elexifier_core.db.session import Database
from elexifier_core.events import EventBus
from elexifier_core.jobs import app as jobs_app
from elexifier_core.logging import configure_logging
from elexifier_core.settings import Settings, get_settings
from elexifier_core.storage import StorageError, build_storage

log = structlog.get_logger(__name__)


def build_state(settings: Settings) -> AppState:
    api_base = settings.public_url.rstrip("/")
    return AppState(
        settings=settings,
        db=Database(settings),
        storage=build_storage(settings),
        secret_box=SecretBox(settings.secret_keys),
        session_store=SessionStore(settings.elx_session_secret, settings.cookie_secure),
        oidc=OidcClient(
            issuer=settings.oidc_issuer,
            client_id=settings.oidc_client_id,
            client_secret=settings.oidc_client_secret,
            redirect_uri=f"{api_base}/auth/callback",
            internal_issuer=settings.oidc_internal_issuer,
        ),
        event_bus=EventBus(settings.database_dsn),
        catalog=ModelCatalog(),
        runtime=PydanticAIRuntime(),
        jobs=jobs_app,
    )


def create_app(settings: Settings | None = None, state: AppState | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.is_production)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app_state = state or build_state(settings)
        app.state.app = app_state
        # The shared Procrastinate app was created at import time; point it at this process's
        # database for as long as the API runs, then open its connection pool.
        connector = procrastinate.PsycopgConnector(conninfo=settings.database_dsn)
        with app_state.jobs.replace_connector(connector):
            await app_state.jobs.open_async()
            await app_state.event_bus.start()
            log.info("api.started", version=__version__, environment=settings.environment)
            try:
                yield
            finally:
                await app_state.runtime.drain()
                await app_state.event_bus.stop()
                await app_state.jobs.close_async()
                await app_state.oidc.aclose()
                await app_state.catalog.aclose()
                await app_state.db.dispose()

    app = FastAPI(
        title="Elexifier API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if not settings.is_production else None,
        openapi_url="/api/openapi.json" if not settings.is_production else None,
    )

    @app.exception_handler(StorageError)
    async def _storage_error(_request: Request, exc: StorageError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/healthz", response_model=HealthOut, tags=["ops"])
    async def healthz(request: Request) -> HealthOut:
        app_state: AppState = request.app.state.app
        database = "ok"
        try:
            async with app_state.db.session() as session:
                await session.execute(text("SELECT 1"))
        except Exception as exc:
            database = f"error: {type(exc).__name__}"
        events = "ok" if app_state.event_bus.is_ready else "connecting"
        status_value = "ok" if database == "ok" else "degraded"
        return HealthOut(status=status_value, database=database, events=events)

    app.include_router(auth.router)
    app.include_router(me.router)
    app.include_router(credentials.router)
    app.include_router(projects.router)
    app.include_router(files.router)
    app.include_router(chat.router)
    app.include_router(events.router)
    app.include_router(documents.router)
    return app


app = create_app()
