#!/usr/bin/env bash
# Update a server installed with install.sh: fetch the current deployment bundle and images for
# IMAGE_TAG (from .env; pass a tag to switch, e.g. `update.sh m4`), restart what changed.
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/elexifier}"
if [ "$(id -u)" -ne 0 ]; then exec sudo -E "$0" "$@"; fi
cd "$APP_DIR"
[ -f .env ] || { echo "no $APP_DIR/.env; run install.sh first"; exit 1; }
if [ -n "${1:-}" ]; then
  sed -i "s|^IMAGE_TAG=.*|IMAGE_TAG=$1|" .env
  grep -q '^IMAGE_TAG=' .env || echo "IMAGE_TAG=$1" >> .env
fi
set -a; . ./.env; set +a
IMAGE_PREFIX="${IMAGE_PREFIX:-ghcr.io/andrazrepar/elexifier-}"
IMAGE_TAG="${IMAGE_TAG:-main}"

echo "== bundle ${IMAGE_PREFIX}deploy:${IMAGE_TAG}"
docker pull "${IMAGE_PREFIX}deploy:${IMAGE_TAG}"
docker rm -f elexifier-bundle >/dev/null 2>&1 || true
docker create --name elexifier-bundle "${IMAGE_PREFIX}deploy:${IMAGE_TAG}" /none >/dev/null
docker cp elexifier-bundle:/bundle/. "$APP_DIR"/
docker rm elexifier-bundle >/dev/null
chmod +x "$APP_DIR"/*.sh
python3 patch_realm.py keycloak/realm-elexifier.json --client "${OIDC_CLIENT_ID:-elexifier-web}" \
  --secret "$OIDC_CLIENT_SECRET" --site "$SITE_URL" >/dev/null

echo "== images"
docker pull "${IMAGE_PREFIX}sandbox:${IMAGE_TAG}"
docker compose pull
echo "== restart what changed"
docker compose up -d --remove-orphans
for i in $(seq 1 60); do
  if docker compose exec -T api curl -fsS http://localhost:8000/healthz >/dev/null 2>&1; then break; fi
  sleep 3
done
./keycloak_ssl.sh
docker image prune -f >/dev/null
docker compose ps
