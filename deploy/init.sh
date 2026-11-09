#!/bin/sh
# Prepares deploy/ for "docker compose up": .env with a fresh secret key, and
# config/services.toml and config/runner.env from their examples (for a runner in this stack).
# Never overwrites a file that exists. Needs python3.
#   ./init.sh                  web and app; runs go to your Prefect server (set it in .env)
#   ./init.sh --with-prefect   also a Prefect server and a runner of this stack's own
set -eu
cd "$(dirname "$0")"

with_prefect=false
case "${1:-}" in
  --with-prefect) with_prefect=true ;;
  "") ;;
  *) echo "usage: $0 [--with-prefect]" >&2; exit 2 ;;
esac

if [ -e .env ]; then
  echo ".env exists, left as it is"
else
  fernet=$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')
  umask 077
  if [ "$with_prefect" = true ]; then
    prefect="echolot:$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
    sed -e "s|^ECHO_SECRET_KEY=.*|ECHO_SECRET_KEY=${fernet}|" \
        -e "s|^ECHO_PREFECT_AUTH_STRING=.*|ECHO_PREFECT_AUTH_STRING=${prefect}|" \
        .env.example > .env
    {
      echo
      echo "# ./init.sh --with-prefect: the Prefect server and the runner of this stack"
      echo "# (ECHO_PREFECT_API_URL and ECHO_PUBLIC_URL above are not used)"
      echo "COMPOSE_FILE=docker-compose.yml:docker-compose.prefect.yml"
      echo "COMPOSE_PROFILES=runner"
    } >> .env
    echo "wrote .env with new secrets and a Prefect server of its own; now set SITE_ADDRESS"
  else
    sed -e "s|^ECHO_SECRET_KEY=.*|ECHO_SECRET_KEY=${fernet}|" .env.example > .env
    echo "wrote .env with a new secret key; now set SITE_ADDRESS, the Prefect server and"
    echo "ECHO_PUBLIC_URL in it"
  fi
  umask 022
fi

copy() { # example, target, mode
  if [ -e "$2" ]; then
    echo "$2 exists, left as it is"
  else
    cp "$1" "$2"
    chmod "$3" "$2"
    echo "wrote $2 from $1"
  fi
}
copy config/services.example.toml config/services.toml 644
copy config/runner.env.example config/runner.env 600
