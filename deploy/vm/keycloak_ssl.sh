#!/usr/bin/env bash
# Align Keycloak with the site in .env, through kcadm inside the Keycloak container:
#  - "require SSL" of both realms follows the site's scheme: a plain-http pilot needs it off
#    (Keycloak refuses http from anything but localhost and private networks), an https site
#    puts it back to "external";
#  - the app's client accepts the site's URLs (redirect URIs, root URL) and uses the secret from
#    .env. The realm file is read only when Keycloak starts on an empty database, so a changed
#    SITE_URL or secret is applied here.
# Run from the deployment directory after the stack is up; install.sh and update.sh call it.
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/elexifier}"
cd "$APP_DIR"
set -a; . ./.env; set +a
site="${SITE_URL:-http://localhost}"
site="${site%/}"
case "$site" in
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
echo "keycloak: require SSL = $want (site $site)"

client="${OIDC_CLIENT_ID:-elexifier-web}"
cid=$(kc get clients -r elexifier --config /tmp/kcadm.config -q "clientId=$client" --fields id \
      | python3 -c 'import json,sys; rows=json.load(sys.stdin); print(rows[0]["id"] if rows else "")')
if [ -n "$cid" ]; then
  current=$(kc get "clients/$cid" -r elexifier --config /tmp/kcadm.config --fields redirectUris \
            | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin).get("redirectUris") or []))')
  merged=$(python3 -c '
import json, sys
uris, site = json.loads(sys.argv[1]), sys.argv[2]
for u in (f"{site}/*", f"{site}/auth/callback"):
    if u not in uris:
        uris.append(u)
print(json.dumps(uris))' "$current" "$site")
  kc update "clients/$cid" -r elexifier --config /tmp/kcadm.config \
     -s "redirectUris=$merged" -s "rootUrl=$site" -s 'webOrigins=["+"]'
  if [ -n "${OIDC_CLIENT_SECRET:-}" ]; then
    kc update "clients/$cid" -r elexifier --config /tmp/kcadm.config -s "secret=$OIDC_CLIENT_SECRET"
  fi
  echo "keycloak: client $client accepts $site"
else
  echo "keycloak: client $client not found in realm elexifier (nothing changed)" >&2
fi
if [ "$want" = none ]; then
  echo "note: without a domain the site runs on plain http; use it for a pilot only"
fi
