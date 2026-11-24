#!/bin/sh
# Installs the workbench on this computer, for the people who use this computer: the app, a
# Prefect server, the runner, the script sandbox and ECHOLOT's reconciliation API, in Docker,
# on http://localhost:8080. For a server others sign in to, see deploy/README.md.
#
#   ./install.sh                 install and start; run it again after "git pull" to update
#   ./install.sh compose ...     any docker compose command on it:
#       ./install.sh compose stop            stop it (compose start: start it again)
#       ./install.sh compose logs -f app     the app's log
#       ./install.sh compose down            remove it, keep the data (down -v: the data too)
#
# Needs Docker with Compose, and curl. Settings and generated keys are in deploy/local/, which
# this script never overwrites; after editing them, run it again.
set -eu
cd "$(dirname "$0")/deploy"

# The table of people that rule_based matches against, so reconciliation works without any key:
# Getty ULAN (291k people), from the project's public WP4 repository
ULAN_URL=https://raw.githubusercontent.com/ECHOLOT-ECCCH/echolot-WP4/8435331420e1bad4024dd526649f662fb9f47485/rule_based/datasheets/ulan.tsv
ULAN_SHA256=9632e82ff400c130c696539375caef607e2d92ce007f5d1d05de4add6708a9ee
ADMIN=admin@localhost

say() { printf '%s\n' "$*"; }
fail() { printf '\n%s\n' "$*" >&2; exit 1; }

compose() {
  docker compose --env-file local/workbench.env -p echolot-local \
    -f docker-compose.yml -f docker-compose.prefect.yml -f docker-compose.echolot.yml \
    --profile runner "$@"
}

# a variable of the shell would win over local/workbench.env
unset SITE_ADDRESS HTTP_PORT HTTPS_PORT PREFECT_PORT RUNS_BRIDGE IMAGE_PREFIX IMAGE_TAG \
  ECHO_SECRET_KEY ECHO_PREFECT_AUTH_STRING ECHO_COOKIE_SECURE ECHO_EXECUTOR \
  APP_ENV_FILE RUNNER_ENV_FILE SERVICES_FILE ECHOLOT_ENV_FILE ECHOLOT_DATASHEETS

case "${1:-}" in
  "") ;;
  compose)
    [ -e local/workbench.env ] || fail "Not installed yet: run ./install.sh first."
    shift
    compose "$@"
    exit ;;
  -h | --help) awk 'NR > 1 { if (!/^#/) exit; sub(/^# ?/, ""); print }' ../install.sh; exit 0 ;;
  *) fail "Unknown option: $1 (see ./install.sh --help)" ;;
esac

# --- what it needs

command -v docker >/dev/null 2>&1 ||
  fail "Docker is not installed: https://docs.docker.com/get-started/get-docker/"
docker compose version >/dev/null 2>&1 ||
  fail "Docker has no Compose plugin: https://docs.docker.com/compose/install/"
docker info >/dev/null 2>&1 ||
  fail "Docker is installed but does not answer. Start it (on Linux, check that you may use it: docker info)."
command -v curl >/dev/null 2>&1 || fail "curl is not installed."

# --- settings and keys, once

random_hex() { od -An -N"$1" -tx1 /dev/urandom | tr -d ' \n'; }

in_use() { # does anything answer on this port of this computer? (curl's 7: nothing does)
  for host in 127.0.0.1 "[::1]"; do
    status=0
    curl -s -o /dev/null --noproxy '*' --max-time 2 "http://$host:$1/" || status=$?
    [ "$status" -eq 7 ] || return 0
  done
  return 1
}

umask 077
mkdir -p local

if [ ! -e local/workbench.env ]; then
  port=8080
  while in_use "$port" || in_use $((port + 1)); do
    port=$((port + 10))
    [ "$port" -le 8180 ] || fail "No free port between 8080 and 8180."
  done
  secret=$(head -c 32 /dev/urandom | base64 | tr -d '\n' | tr '+/' '-_')
  cat > local/workbench.env.new <<EOF
# Written by ./install.sh, never overwritten by it. After a change: ./install.sh
# Every setting: deploy/.env.example
SITE_ADDRESS=:80
# where the workbench (HTTP_PORT) and Prefect's UI (PREFECT_PORT) answer, on this computer only
HTTP_PORT=127.0.0.1:$port
HTTPS_PORT=127.0.0.1:0
PREFECT_PORT=$((port + 1))
# plain http on localhost
ECHO_COOKIE_SECURE=false
# encrypts the model keys saved in the app: without it they must be entered again
ECHO_SECRET_KEY=$secret
ECHO_PREFECT_AUTH_STRING=echolot:$(random_hex 24)
IMAGE_PREFIX=echolot-local/
RUNS_BRIDGE=echolocal-runs
APP_ENV_FILE=local/workbench.env
RUNNER_ENV_FILE=local/runner.env
SERVICES_FILE=./local/services.toml
ECHOLOT_ENV_FILE=local/echolot.env
ECHOLOT_DATASHEETS=./local/datasheets
EOF
  mv local/workbench.env.new local/workbench.env
fi

if [ ! -e local/echolot.env ]; then
  cat > local/echolot.env.new <<EOF
# ECHOLOT's settings, written by ./install.sh, never overwritten by it. After a change:
# ./install.sh. What each algorithm needs: the README of
# https://github.com/ECHOLOT-ECCCH/echolot-wp4-api-service
API_DOCS=false
# what the runner sends (local/runner.env holds the same value)
API_KEY=$(random_hex 32)
# rule_based: people, against Getty ULAN; no key needed
RULE_BASED_TARGET_FILE=/data/ulan.tsv
# wikifier: a key from http://www.wikifier.org/register.html
WIKIFIER_API_KEY=
# llm_based: a key from https://zerbitzuak.hitz.eus/tresnak (Profile, Generate new API Key)
HITZ_API_KEY=
EOF
  mv local/echolot.env.new local/echolot.env
fi

if [ ! -e local/runner.env ]; then
  printf '# the service keys named by key_env in services.toml\nECHOLOT_API_KEY=%s\n' \
    "$(sed -n 's/^API_KEY=//p' local/echolot.env)" > local/runner.env
fi

umask 022
if [ ! -e local/services.toml ]; then
  # the server example, with ECHOLOT in this project instead of on the host
  sed 's|^url = .*|url = "http://echolot:5000"|' config/services.example.toml > local/services.toml
fi

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi |
    cut -d ' ' -f 1
}

mkdir -p local/datasheets
# the containers read these as a user of their own
chmod 755 local/datasheets
chmod 644 local/services.toml
if [ ! -e local/datasheets/ulan.tsv ]; then
  say "Downloading the table of people for reconciliation (24 MB)..."
  if curl -fsSL -o local/datasheets/ulan.tsv.new "$ULAN_URL" &&
     [ "$(sha256 local/datasheets/ulan.tsv.new)" = "$ULAN_SHA256" ]; then
    mv local/datasheets/ulan.tsv.new local/datasheets/ulan.tsv
  else
    rm -f local/datasheets/ulan.tsv.new
    say "That did not work, so rule_based is not available yet; ./install.sh tries again."
  fi
fi

# --- build and start

say "Building and starting the containers (the first time takes several minutes)..."
compose up -d --build --wait --wait-timeout 300 --remove-orphans ||
  fail "The containers did not all start. What they say: ./install.sh compose logs --tail 50
If a port is taken, change HTTP_PORT or PREFECT_PORT in deploy/local/workbench.env and run ./install.sh again."

# --- the first account

password=
if [ -z "$(compose exec -T app echolot-admin list-users </dev/null)" ]; then
  password=$(compose exec -T app echolot-admin create-user "$ADMIN" --name Admin --admin \
    </dev/null | sed -n 's/^Temporary password: //p')
fi

# --- does it work?

say "Checking..."
tries=0
until checks=$(compose exec -T app echolot-admin check-runs </dev/null 2>&1); do
  tries=$((tries + 1))
  [ "$tries" -lt 30 ] || fail "Runs do not work yet:
$checks
See ./install.sh compose logs --tail 50 runner"
  sleep 2
done
compose exec -T runner python -c \
  "import urllib.request as u; u.urlopen('http://echolot:5000/health', timeout=10)" </dev/null ||
  fail "The runner does not reach ECHOLOT. See ./install.sh compose logs --tail 50 echolot"

port=$(sed -n 's/^HTTP_PORT=.*://p' local/workbench.env)
say ""
say "The workbench is running: http://localhost:$port"
if [ -n "$password" ]; then
  say "  Sign in as $ADMIN with the password $password"
  say "  (shown only now; change it under Settings, Account)."
  say "  Then add a model key under Settings, Model keys: the agent needs one."
fi
if [ -e local/datasheets/ulan.tsv ]; then
  say "  Reconciliation works for people (rule_based, Getty ULAN). For wikifier and llm_based, put"
  say "  their keys in deploy/local/echolot.env and run ./install.sh again."
fi
say "  Stop it: ./install.sh compose stop    More: ./install.sh --help"
