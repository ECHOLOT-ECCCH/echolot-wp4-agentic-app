#!/usr/bin/env bash
# Install Elexifier on a fresh Ubuntu/Debian server from the published images.
#
#   scp deploy/vm/install.sh you@server:   (once)
#   ssh you@server sudo ./install.sh
#
# Asks for: the site's domain (empty = plain http on the server's address), a GitHub user and
# a classic personal access token with read:packages (the images are private). Everything
# lives in /opt/elexifier; re-running is safe (an existing .env is kept). Non-interactive:
#   DOMAIN=dictionaries.example.org GHCR_USER=me GHCR_TOKEN=ghp_... sudo -E ./install.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/elexifier}"
IMAGE_PREFIX="${IMAGE_PREFIX:-ghcr.io/andrazrepar/elexifier-}"
IMAGE_TAG="${IMAGE_TAG:-main}"

if [ "$(id -u)" -ne 0 ]; then exec sudo -E "$0" "$@"; fi

say() { printf '\n== %s\n' "$*"; }

say "Docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker >/dev/null 2>&1 || true
docker compose version >/dev/null 2>&1 || { echo "docker compose (v2) is missing; install docker-compose-plugin"; exit 1; }
if [ -n "${SUDO_USER:-}" ]; then usermod -aG docker "$SUDO_USER" || true; fi

say "Registry login (private images on ghcr.io)"
if ! docker image inspect "${IMAGE_PREFIX}deploy:${IMAGE_TAG}" >/dev/null 2>&1 \
   && ! docker manifest inspect "${IMAGE_PREFIX}deploy:${IMAGE_TAG}" >/dev/null 2>&1; then
  : "${GHCR_USER:=$(read -rp 'GitHub user name: ' u && echo "$u")}"
  if [ -z "${GHCR_TOKEN:-}" ]; then read -rsp 'Classic personal access token (read:packages): ' GHCR_TOKEN; echo; fi
  printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
fi

say "Deployment bundle -> $APP_DIR"
mkdir -p "$APP_DIR"
cd "$APP_DIR"
docker pull "${IMAGE_PREFIX}deploy:${IMAGE_TAG}"
docker rm -f elexifier-bundle >/dev/null 2>&1 || true
docker create --name elexifier-bundle "${IMAGE_PREFIX}deploy:${IMAGE_TAG}" /none >/dev/null
docker cp elexifier-bundle:/bundle/. "$APP_DIR"/
docker rm elexifier-bundle >/dev/null
chmod +x "$APP_DIR"/*.sh

if [ ! -f .env ]; then
  say "Configuration"
  if [ -z "${DOMAIN+x}" ]; then
    read -rp "Site domain (DNS name pointing at this server; empty for plain http on its address): " DOMAIN
  fi
  if [ -n "$DOMAIN" ]; then
    SITE_ADDRESS="$DOMAIN"; SITE_URL="https://$DOMAIN"
  else
    ADDR="$(hostname -I 2>/dev/null | awk '{print $1}')"; ADDR="${ADDR:-localhost}"
    SITE_ADDRESS=":80"; SITE_URL="http://$ADDR"
  fi
  {
    python3 gen_secrets.py | grep -v '^# SITE_URL\|^# SITE_ADDRESS\|^# KEYCLOAK_URL'
    echo
    echo "# Deployment"
    echo "IMAGE_PREFIX=$IMAGE_PREFIX"
    echo "IMAGE_TAG=$IMAGE_TAG"
    echo "SITE_URL=$SITE_URL"
    echo "SITE_ADDRESS=$SITE_ADDRESS"
    echo "KEYCLOAK_URL=$SITE_URL/idp"
    echo "KEYCLOAK_PORT=127.0.0.1:8080"
  } > .env
  chmod 600 .env
  echo "wrote $APP_DIR/.env (keep it: it holds the encryption key for stored provider keys)"
else
  say "Keeping the existing .env"
fi

set -a; . ./.env; set +a
python3 patch_realm.py keycloak/realm-elexifier.json --client "${OIDC_CLIENT_ID:-elexifier-web}" \
  --secret "$OIDC_CLIENT_SECRET" --site "$SITE_URL"

say "Images"
docker pull "${IMAGE_PREFIX}sandbox:${IMAGE_TAG}"
docker compose pull

say "Start"
docker compose up -d --remove-orphans
for i in $(seq 1 60); do
  if docker compose exec -T api curl -fsS http://localhost:8000/healthz >/dev/null 2>&1; then break; fi
  sleep 3
done
docker compose ps

cat <<EOF

Elexifier is up.
  Site:              $SITE_URL
  Keycloak admin:    $SITE_URL/idp/admin/   (user and password: KEYCLOAK_ADMIN* in $APP_DIR/.env)
  Update later:      sudo $APP_DIR/update.sh            (or: sudo $APP_DIR/update.sh m4  to pin a tag)
  Logs:              cd $APP_DIR && docker compose logs -f --tail=100
Next: create user accounts in Keycloak (realm elexifier) or enable self-registration there; each
user adds their model provider key under Settings in the app.
EOF
