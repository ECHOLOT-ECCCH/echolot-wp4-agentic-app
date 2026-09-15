#!/bin/sh
# One-shot Garage bootstrap for a single node: layout, deterministic access key, bucket, permissions.
# Idempotent. The CLI connects using rpc_public_addr and rpc_secret from the shared config file.
set -eu
G="/garage -c /etc/garage.toml"
i=0
until $G status >/dev/null 2>&1; do
  i=$((i + 1)); [ "$i" -gt 60 ] && { echo "garage did not become reachable"; exit 1; }
  sleep 2
done
if $G layout show 2>/dev/null | grep -qi "no nodes currently have a role\|NO ROLE ASSIGNED\|no role"; then
  NODE_ID=$($G status | awk '/^==== HEALTHY NODES/{f=1;next} f && $1 ~ /^[0-9a-f]+$/ {print $1; exit}')
  $G layout assign -z dc1 -c 100G "$NODE_ID"
  $G layout apply --version 1
fi
if ! $G key info elexifier >/dev/null 2>&1; then
  $G key import -n elexifier --yes "$S3_ACCESS_KEY" "$S3_SECRET_KEY"
fi
if ! $G bucket info "$S3_BUCKET" >/dev/null 2>&1; then
  $G bucket create "$S3_BUCKET"
fi
$G bucket allow --read --write --owner "$S3_BUCKET" --key elexifier >/dev/null
echo "garage ready: bucket $S3_BUCKET, key elexifier"
