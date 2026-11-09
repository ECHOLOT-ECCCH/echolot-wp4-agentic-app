#!/bin/sh
# Builds the images, starts the whole stack (web, app, Prefect, runner) with the test model on
# http://127.0.0.1:${SMOKE_PORT:-8080}, creates an account and runs smoke_check.py through the
# front: pages, sign-in, a streamed chat turn, a run through Prefect and the runner. The stack
# and its volumes are removed afterwards; deploy/.env and deploy/config are not used.
#   deploy/smoke.sh            (make deploy-smoke; CI runs it too)
set -eu
cd "$(dirname "$0")"

port=${SMOKE_PORT:-8080}
tmp=$(mktemp -d)
compose() { docker compose -p echolot-smoke --env-file "$tmp/smoke.env" --profile runner "$@"; }
cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then compose logs --no-color --tail 80 || true; fi
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$tmp"
  exit "$status"
}
trap cleanup EXIT INT TERM

password=$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')
cat > "$tmp/smoke.env" <<EOF
APP_ENV_FILE=$tmp/smoke.env
RUNNER_ENV_FILE=$tmp/runner.env
SERVICES_FILE=$tmp/services.toml
SITE_ADDRESS=:80
HTTP_PORT=127.0.0.1:$port
HTTPS_PORT=127.0.0.1:$((port + 363))
PREFECT_PORT=$((port + 1))
ECHO_SECRET_KEY=$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')
PREFECT_AUTH_STRING=smoke:$(python3 -c 'import secrets; print(secrets.token_hex(16))')
ECHO_COOKIE_SECURE=false
ECHO_MODEL_PROVIDER=test
ECHO_MODEL_ID=test
ECHO_ENABLE_TEST_MODEL=true
EOF
: > "$tmp/runner.env"
: > "$tmp/services.toml"
chmod 644 "$tmp/services.toml"

compose up -d --build --wait
printf '%s\n' "$password" |
  compose exec -T app echolot-admin create-user smoke@example.org --admin --password-stdin
python3 smoke_check.py "http://127.0.0.1:$port" smoke@example.org "$password"
