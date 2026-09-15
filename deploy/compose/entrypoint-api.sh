#!/bin/sh
# Runs migrations (idempotent) before starting the API; the worker skips them.
set -e
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "applying database migrations"
  alembic -c /app/packages/core/alembic.ini upgrade head
  python -m elexifier_core.worker schema
fi
exec "$@"
