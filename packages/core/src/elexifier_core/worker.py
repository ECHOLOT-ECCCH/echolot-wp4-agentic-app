"""CLI: ``python -m elexifier_core.worker worker [queue,queue]`` or ``... schema``."""

from __future__ import annotations

import asyncio
import sys

from elexifier_core.jobs import apply_schema, run_worker
from elexifier_core.logging import configure_logging
from elexifier_core.settings import get_settings


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.is_production)
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "worker"
    if command == "worker":
        queues = args[1].split(",") if len(args) > 1 else None
        asyncio.run(run_worker(queues))
        return 0
    if command == "schema":
        asyncio.run(apply_schema())
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
