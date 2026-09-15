"""Bootstrap a single-node Garage through its admin API (v2): layout, access key, bucket.

    python -m elexifier_core.garage_bootstrap

Environment: GARAGE_ADMIN_URL (default http://garage:3903), GARAGE_ADMIN_TOKEN, S3_BUCKET,
S3_ACCESS_KEY, S3_SECRET_KEY, GARAGE_CAPACITY_BYTES (default 100 GB). Idempotent.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import httpx

ADMIN_URL = os.environ.get("GARAGE_ADMIN_URL", "http://garage:3903").rstrip("/")
ADMIN_TOKEN = os.environ.get("GARAGE_ADMIN_TOKEN", "")
BUCKET = os.environ.get("S3_BUCKET", "elexifier")
ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "")
SECRET_KEY = os.environ.get("S3_SECRET_KEY", "")
CAPACITY = int(os.environ.get("GARAGE_CAPACITY_BYTES", str(100 * 1024**3)))


class Admin:
    def __init__(self) -> None:
        self._http = httpx.Client(
            base_url=ADMIN_URL, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}, timeout=15
        )

    def get(self, path: str, **params: Any) -> Any:
        resp = self._http.get(path, params=params or None)
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        resp = self._http.post(path, json=body if body is not None else {})
        resp.raise_for_status()
        return resp.json() if resp.content else None

    def wait(self, attempts: int = 60) -> dict[str, Any]:
        for _ in range(attempts):
            try:
                return self.get("/v2/GetClusterStatus")
            except (httpx.HTTPError, ValueError):
                time.sleep(2)
        raise SystemExit(f"garage admin API not reachable at {ADMIN_URL}")


def ensure_layout(admin: Admin, status: dict[str, Any]) -> None:
    nodes = status.get("nodes", [])
    if not nodes:
        raise SystemExit("garage reports no nodes")
    node = nodes[0]
    if node.get("role"):
        print(f"layout: node {node['id'][:16]} already has a role")
        return
    layout = admin.get("/v2/GetClusterLayout")
    admin.post(
        "/v2/UpdateClusterLayout",
        {"roles": [{"id": node["id"], "zone": "dc1", "capacity": CAPACITY, "tags": ["elexifier"]}]},
    )
    applied = admin.post("/v2/ApplyClusterLayout", {"version": int(layout["version"]) + 1})
    print(f"layout: applied version {applied['layout']['version']}")


def ensure_key(admin: Admin) -> None:
    try:
        info = admin.get("/v2/GetKeyInfo", id=ACCESS_KEY)
        print(f"key: {info['accessKeyId']} exists ({info.get('name')})")
        return
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in (400, 404):
            raise
    admin.post(
        "/v2/ImportKey",
        {"accessKeyId": ACCESS_KEY, "secretAccessKey": SECRET_KEY, "name": "elexifier"},
    )
    print(f"key: imported {ACCESS_KEY}")


def ensure_bucket(admin: Admin) -> str:
    try:
        info = admin.get("/v2/GetBucketInfo", globalAlias=BUCKET)
        print(f"bucket: {BUCKET} exists")
        return str(info["id"])
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in (400, 404):
            raise
    info = admin.post("/v2/CreateBucket", {"globalAlias": BUCKET})
    print(f"bucket: created {BUCKET}")
    return str(info["id"])


def ensure_permissions(admin: Admin, bucket_id: str) -> None:
    admin.post(
        "/v2/AllowBucketKey",
        {
            "bucketId": bucket_id,
            "accessKeyId": ACCESS_KEY,
            "permissions": {"read": True, "write": True, "owner": True},
        },
    )
    print(f"permissions: {ACCESS_KEY} may read/write/own {BUCKET}")


def main() -> int:
    if not (ADMIN_TOKEN and ACCESS_KEY and SECRET_KEY):
        print("GARAGE_ADMIN_TOKEN, S3_ACCESS_KEY and S3_SECRET_KEY are required", file=sys.stderr)
        return 2
    admin = Admin()
    status = admin.wait()
    ensure_layout(admin, status)
    ensure_key(admin)
    bucket_id = ensure_bucket(admin)
    ensure_permissions(admin, bucket_id)
    print("garage ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
