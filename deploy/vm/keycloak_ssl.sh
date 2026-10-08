#!/usr/bin/env bash
# Align Keycloak's "require SSL" setting with the site's scheme: a plain-http site (no domain
# yet, a pilot on a VPN) needs it off for both realms, otherwise Keycloak answers "HTTPS
# required" to every browser outside localhost; an https site puts it back to "external".
# Run from the deployment directory after the stack is up; install.sh and update.sh call it.
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/elexifier}"
cd "$APP_DIR"
set -a; . ./.env; set +a
case "${SITE_URL:-http://localhost}" in
  https://*) want=external ;;
  *)         want=none ;;
esac
kc() { docker compose exec -T keycloak /opt/keycloak/bin/kcadm.sh "$@"; }
for i in $(seq 1 60); do
  if kc config credentials --config /tmp/kcadm.config --server http://localhost:8080/idp \
       --realm master --user "${KEYCLOAK_ADMIN:-admin}" --password "${KEYCLOAK_ADMIN_PASSWORD:-admin}" \
       >/dev/null 2>&1; then break; fi
  sleep 3
done
for realm in master elexifier; do
  kc update "realms/$realm" --config /tmp/kcadm.config -s "sslRequired=$want"
done
echo "keycloak: require SSL = $want (site ${SITE_URL:-http://localhost})"
if [ "$want" = none ]; then
  echo "note: without a domain the site runs on plain http; use it for a pilot only"
fi
