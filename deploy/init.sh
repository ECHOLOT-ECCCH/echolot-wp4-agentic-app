#!/bin/sh
# Prepares deploy/ for "docker compose up": .env with fresh secrets, and config/services.toml and
# config/runner.env from their examples. Never overwrites a file that exists. Needs python3.
set -eu
cd "$(dirname "$0")"

if [ -e .env ]; then
  echo ".env exists, left as it is"
else
  fernet=$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')
  prefect="echolot:$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
  umask 077
  sed -e "s|^ECHO_SECRET_KEY=.*|ECHO_SECRET_KEY=${fernet}|" \
      -e "s|^PREFECT_AUTH_STRING=.*|PREFECT_AUTH_STRING=${prefect}|" .env.example > .env
  umask 022
  echo "wrote .env with new secrets; now set SITE_ADDRESS (and the model settings) in it"
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
