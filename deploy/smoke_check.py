"""Checks a running workbench end to end over HTTP (deploy/smoke.sh runs it against the compose
stack). Standard library only.

    python3 deploy/smoke_check.py http://127.0.0.1:8080 smoke@example.org <password>

It signs in, loads the app's pages, runs one chat turn with the test model (the server must
have ECHO_MODEL_PROVIDER=test), uploads a CSV, runs a script over three records through the
configured executor and checks the results. --api-only skips the checks of the web front
(Caddy), for an app reached directly.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from email.message import Message
from typing import Any

HEADER = {"X-Echolot-Request": "1"}
SCRIPT = 'def process(record, ctx):\n    return {"upper": record["name"].upper()}\n'
CSV = b"name,born\nAna,1901\nJon,1902\nMiren,1903\nIker,1904\n"


class Check:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.cookie = ""

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        raw: bytes | None = None,
        headers: dict[str, str] | None = None,
        csrf: bool = True,
    ) -> tuple[int, Message, bytes]:
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        merged = {**(HEADER if csrf else {}), **(headers or {})}
        if body is not None:
            merged["Content-Type"] = "application/json"
        if self.cookie:
            merged["Cookie"] = self.cookie
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=merged)
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()

    def json(self, method: str, path: str, body: Any = None, expect: int = 200) -> Any:
        status, _headers, content = self.request(method, path, body)
        if status != expect:  # quiet when it works: the run is polled many times
            ok(False, f"{method} {path} answers {expect}", f"got {status}: {content[:300]!r}")
        return json.loads(content) if content else None


def ok(condition: bool, what: str, detail: str = "") -> None:
    if not condition:
        print(f"FAIL  {what}" + (f": {detail}" if detail else ""))
        sys.exit(1)
    print(f"ok    {what}")


def web_front(check: Check) -> None:
    status, headers, page = check.request("GET", "/")
    ok(status == 200 and b'id="root"' in page, "the app's page loads")
    ok(headers.get("X-Content-Type-Options") == "nosniff", "security headers are set")
    ok("no-cache" in headers.get("Cache-Control", ""), "the page is revalidated on every load")
    for route in ("/login", "/settings", "/w/00000000-0000-0000-0000-000000000000"):
        status, _h, body = check.request("GET", route)
        ok(status == 200 and b'id="root"' in body, f"the client-side route {route} loads the app")
    asset = re.search(rb'src="(/assets/[^"]+\.js)"', page)
    ok(asset is not None, "the page names its script")
    assert asset is not None
    status, headers, _b = check.request("GET", asset.group(1).decode())
    ok(status == 200 and "immutable" in headers.get("Cache-Control", ""), "assets are cached")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("email")
    parser.add_argument("password")
    parser.add_argument("--api-only", action="store_true", help="skip the web front checks")
    args = parser.parse_args()
    check = Check(args.base_url)

    ok(check.json("GET", "/healthz")["status"] == "ok", "the app and its database are healthy")
    if not args.api_only:
        web_front(check)

    # --- signing in
    ok(check.request("GET", "/api/me")[0] == 401, "the API needs a session")
    login = {"email": args.email, "password": args.password}
    status, _h, _b = check.request("POST", "/api/auth/login", login, csrf=False)
    ok(status == 403, "a request without the CSRF header is refused")
    status, headers, _b = check.request("POST", "/api/auth/login", login)
    ok(status == 200, "signing in works")
    cookie = headers.get("Set-Cookie", "")
    flags = cookie.lower()
    ok("httponly" in flags and "samesite=lax" in flags, "the session cookie is HttpOnly, Lax")
    check.cookie = cookie.split(";", 1)[0]
    ok(check.json("GET", "/api/me")["email"] == args.email, "the session is recognised")

    # --- a workspace with a file
    wid = check.json("POST", "/api/workspaces", {"name": "Smoke"}, expect=201)["id"]
    status, _h, body = check.request(
        "POST",
        f"/api/workspaces/{wid}/files?name=people.csv",
        raw=CSV,
        headers={"Content-Type": "text/csv"},
    )
    ok(status == 201, "a CSV uploads", body[:200].decode(errors="replace"))
    fid = json.loads(body)["id"]

    # --- a chat turn, streamed through the front
    cid = check.json("POST", f"/api/workspaces/{wid}/conversations", {}, expect=201)["id"]
    turn = {
        "trigger": "submit-message",
        "id": "smoke",
        "messages": [{"id": "m1", "role": "user", "parts": [{"type": "text", "text": "hi"}]}],
    }
    status, headers, stream = check.request(
        "POST", f"/api/workspaces/{wid}/conversations/{cid}/chat", turn
    )
    ok(status == 200, "a chat turn is accepted", stream[:300].decode(errors="replace"))
    ok(headers.get("Content-Type", "").startswith("text/event-stream"), "the answer streams")
    ok(b'"type":"finish"' in stream, "the streamed answer finishes")

    # --- a run over three records, through the configured executor and back
    check.json("POST", f"/api/workspaces/{wid}/scripts", {"code": SCRIPT}, expect=201)
    run = check.json("POST", f"/api/workspaces/{wid}/runs", {"file_id": fid, "limit": 3}, 201)
    deadline = time.monotonic() + 180
    while run["status"] in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(1)
        run = check.json("GET", f"/api/workspaces/{wid}/runs/{run['id']}")
    ok(run["status"] == "done", f"the run finishes ({run['executor']})", json.dumps(run)[:500])
    ok(run["counts"].get("ok") == 3, "all three records succeed", json.dumps(run["counts"]))
    rows = check.json("GET", f"/api/workspaces/{wid}/runs/{run['id']}/rows")["rows"]
    ok([r["output"]["upper"] for r in rows] == ["ANA", "JON", "MIREN"], "the results are right")

    check.json("DELETE", f"/api/workspaces/{wid}", expect=204)
    check.request("POST", "/api/auth/logout")
    ok(check.request("GET", "/api/me")[0] == 401, "signing out ends the session")
    print("smoke check passed")


if __name__ == "__main__":
    main()
