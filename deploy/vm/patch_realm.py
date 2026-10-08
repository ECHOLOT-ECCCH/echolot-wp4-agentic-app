#!/usr/bin/env python3
"""Adjust the Keycloak realm file for a deployment before Keycloak imports it (first start):
the app's client secret and redirect URIs for the site, and no development users.

    patch_realm.py REALM.json --client elexifier-web --secret ... --site https://example.org
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_LOCAL = re.compile(r"^https?://localhost([:/]|$)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("realm", type=Path)
    parser.add_argument("--client", default="elexifier-web")
    parser.add_argument("--secret", required=True)
    parser.add_argument("--site", required=True, help="public URL of the site, e.g. https://x.org")
    parser.add_argument("--keep-dev-users", action="store_true")
    args = parser.parse_args()

    realm = json.loads(args.realm.read_text(encoding="utf-8"))
    site = args.site.rstrip("/")
    clients = [c for c in realm.get("clients", []) if c.get("clientId") == args.client]
    if not clients:
        raise SystemExit(f"client {args.client!r} not found in {args.realm}")
    for client in clients:
        client["secret"] = args.secret
        client["rootUrl"] = site
        uris = [u for u in client.get("redirectUris", []) if not _LOCAL.match(u)]
        for uri in (f"{site}/*", f"{site}/auth/callback"):
            if uri not in uris:
                uris.append(uri)
        client["redirectUris"] = uris
        client["webOrigins"] = ["+"]
    if not args.keep_dev_users:
        realm["users"] = [
            u
            for u in realm.get("users", [])
            if not str(u.get("email", "")).endswith("@example.com")
        ]
    args.realm.write_text(json.dumps(realm, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"realm {realm.get('realm')}: client {args.client} set for {site}, "
        f"{len(realm.get('users', []))} user(s) kept"
    )


if __name__ == "__main__":
    main()
