"""Background jobs with Procrastinate (PostgreSQL-backed queue, no extra broker).

The app object lives here; the CLI entry point is ``elexifier_core.worker`` so that this module is
never imported as ``__main__`` (Procrastinate warns about that).
"""

from __future__ import annotations

import procrastinate

from elexifier_core.settings import get_settings

QUEUE_DEFAULT = "default"
QUEUE_HEAVY = "heavy"  # long conversions, PDF layout extraction


def build_app(dsn: str | None = None) -> procrastinate.App:
    settings = get_settings()
    return procrastinate.App(
        connector=procrastinate.PsycopgConnector(conninfo=dsn or settings.database_dsn),
        import_paths=[m.strip() for m in settings.task_modules.split(",") if m.strip()],
    )


app = build_app()


async def run_worker(queues: list[str] | None = None, concurrency: int = 2) -> None:
    async with app.open_async():
        if queues:
            await app.run_worker_async(queues=queues, concurrency=concurrency, wait=True)
        else:
            await app.run_worker_async(concurrency=concurrency, wait=True)


async def apply_schema(dsn: str | None = None) -> bool:
    """Create the Procrastinate tables if they do not exist yet. Returns True when created.

    Upgrades of an existing Procrastinate schema are applied with its own SQL migrations
    (``procrastinate schema --migrations-path``), tracked in docs/self-hosting.md.
    """
    target = build_app(dsn) if dsn else app
    async with target.open_async():
        rows = await target.connector.execute_query_all_async(
            "SELECT to_regclass('public.procrastinate_jobs') AS existing"
        )
        if rows and rows[0]["existing"] is not None:
            return False
        await target.schema_manager.apply_schema_async()
        return True
