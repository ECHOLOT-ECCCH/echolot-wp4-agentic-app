"""Checks a running workbench end to end over HTTP (deploy/smoke.sh runs it against the compose
stack). Standard library only.

    python3 deploy/smoke_check.py http://127.0.0.1:8080 smoke@example.org <password>

It signs in, loads the app's pages, runs one chat turn with the test model (the server must
have ECHO_MODEL_PROVIDER=test), uploads a CSV, runs a script over three records through the
configured executor and checks the results. --api-only skips the checks of the web front
(Caddy), for an app reached directly. --runner-offline also runs a script that tries to reach
the internet and expects no connection (the runner on the internal network of
docker-compose.prefect.yml). --runner-limits also runs a script that allocates memory without
end and expects that run to fail and the next one to work (the runner's memory limit).
--sandbox also checks the script sandbox (docs/PLAN.md M6.4): a script finds no key, token or
credential, reaches no address, still calls a service (the app's /healthz as the service "app"
in services.toml) through the runner, and cannot read or signal a run beside it.
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
# opens a connection to a public address, not a name, so that DNS does not decide the outcome
PROBE = (
    "import socket\n"
    "def process(record, ctx):\n"
    "    try:\n"
    '        socket.create_connection(("1.1.1.1", 443), timeout=5).close()\n'
    '        return {"internet": "reached"}\n'
    "    except OSError as exc:\n"
    '        return {"internet": "blocked", "why": type(exc).__name__}\n'
)
# takes memory until the runner's limit ends its run
HOG = (
    "def process(record, ctx):\n"
    "    hog = []\n"
    "    while True:\n"
    "        hog.append(bytearray(64 << 20))\n"
)
CSV = b"name,born\nAna,1901\nJon,1902\nMiren,1903\nIker,1904\n"
# what a script finds and reaches in the sandbox; flat values, so they come back as they are
SNOOP = (
    "import os, socket\n"
    "WORDS = ('KEY', 'AUTH', 'TOKEN', 'PASSWORD', 'SECRET')\n"
    "def secret(names):\n"
    "    return [n for n in names if any(w in n for w in WORDS)]\n"
    "def process(record, ctx):\n"
    "    others = []\n"
    "    for pid in os.listdir('/proc'):\n"
    "        if pid.isdigit() and int(pid) != os.getpid():\n"
    "            try:\n"
    "                raw = open(f'/proc/{pid}/environ', 'rb').read()\n"
    "            except OSError:\n"
    "                continue\n"
    "            names = [kv.split(b'=', 1)[0].decode() for kv in raw.split(b'\\0') if kv]\n"
    "            others += [f'{pid}:{n}' for n in secret(names)]\n"
    "    out = {'uid': os.getuid(), 'own': ','.join(secret(os.environ)),\n"
    "           'others': ','.join(others)}\n"
    "    for name, host, port in (('internet', '1.1.1.1', 443), ('app', 'app', 8000),\n"
    "                             ('prefect', 'prefect', 4200)):\n"
    "        try:\n"
    "            socket.create_connection((host, port), timeout=3).close()\n"
    "            out[name] = 'reached'\n"
    "        except OSError:\n"
    "            out[name] = 'blocked'\n"
    "    out['service'] = ctx.service('app').get('/healthz')['status']\n"
    "    return out\n"
)
# two runs side by side: the first leaves a private file and its pid where the second looks
NEIGHBOUR = "/tmp/smoke-neighbour.json"
FIRST = (
    "import json, os, time\n"
    "def process(record, ctx):\n"
    "    with open('private.txt', 'w') as fh:\n"
    "        fh.write('mine')\n"
    "    info = {'pid': os.getpid(), 'file': os.path.abspath('private.txt')}\n"
    f"    with open({NEIGHBOUR!r}, 'w') as fh:\n"
    "        json.dump(info, fh)\n"
    f"    os.chmod({NEIGHBOUR!r}, 0o644)\n"
    "    time.sleep(20)\n"
    "    return {'pid': info['pid']}\n"
)
SECOND = (
    "import json, os, time\n"
    "def attempt(what):\n"
    "    try:\n"
    "        what()\n"
    "        return 'allowed'\n"
    "    except ProcessLookupError:\n"
    "        return 'gone'\n"
    "    except OSError:\n"
    "        return 'refused'\n"
    "def process(record, ctx):\n"
    "    for _ in range(60):\n"
    f"        if os.path.exists({NEIGHBOUR!r}):\n"
    "            break\n"
    "        time.sleep(0.5)\n"
    f"    info = json.load(open({NEIGHBOUR!r}))\n"
    "    return {'read': attempt(lambda: open(info['file']).read()),\n"
    "            'signal': attempt(lambda: os.kill(info['pid'], 0)),\n"
    "            'proc': attempt(lambda: open(f\"/proc/{info['pid']}/environ\", 'rb').read())}\n"
)


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


def start(check: Check, wid: str, fid: str, code: str, limit: int) -> dict[str, Any]:
    """Save ``code`` as the workspace's script and start a run of it over ``limit`` records."""
    check.json("POST", f"/api/workspaces/{wid}/scripts", {"code": code}, expect=201)
    return check.json("POST", f"/api/workspaces/{wid}/runs", {"file_id": fid, "limit": limit}, 201)


def start_and_wait(check: Check, wid: str, fid: str, code: str, limit: int) -> dict[str, Any]:
    """Save ``code`` as the workspace's script, run it over ``limit`` records until it ends."""
    return wait(check, wid, start(check, wid, fid, code, limit))


def wait(check: Check, wid: str, run: dict[str, Any]) -> dict[str, Any]:
    deadline = time.monotonic() + 180
    while run["status"] in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(1)
        run = check.json("GET", f"/api/workspaces/{wid}/runs/{run['id']}")
    return run


def run_script(check: Check, wid: str, fid: str, code: str, limit: int) -> list[dict[str, Any]]:
    """Run ``code`` over ``limit`` records, check that every record succeeds, return the rows."""
    run = start_and_wait(check, wid, fid, code, limit)
    ok(run["status"] == "done", f"the run finishes ({run['executor']})", json.dumps(run)[:500])
    counts = run["counts"]
    ok(counts.get("ok") == limit, f"all {limit} record(s) succeed", json.dumps(counts))
    return check.json("GET", f"/api/workspaces/{wid}/runs/{run['id']}/rows")["rows"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("email")
    parser.add_argument("password")
    parser.add_argument("--api-only", action="store_true", help="skip the web front checks")
    parser.add_argument(
        "--runner-offline", action="store_true", help="expect the runner to have no route out"
    )
    parser.add_argument(
        "--runner-limits", action="store_true", help="expect the runner to have a memory limit"
    )
    parser.add_argument(
        "--sandbox", action="store_true", help="expect scripts to run in the script sandbox"
    )
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
    rows = run_script(check, wid, fid, SCRIPT, 3)
    ok([r["output"]["upper"] for r in rows] == ["ANA", "JON", "MIREN"], "the results are right")

    # --- a script that tries to leave the runner's network
    if args.runner_offline:
        [row] = run_script(check, wid, fid, PROBE, 1)
        ok(
            row["output"].get("internet") == "blocked",
            "a script cannot reach the internet",
            json.dumps(row["output"]),
        )

    # --- a script that takes memory without end, then a run that should not notice
    if args.runner_limits:
        run = start_and_wait(check, wid, fid, HOG, 1)
        ok(run["status"] == "failed", "a run that exhausts the memory fails", json.dumps(run)[:500])
        rows = run_script(check, wid, fid, SCRIPT, 3)
        ok(rows[0]["output"].get("upper") == "ANA", "the next run works")

    # --- the script sandbox: nothing secret, no address, but the runner's service calls
    if args.sandbox:
        [row] = run_script(check, wid, fid, SNOOP, 1)
        out = row["output"]
        detail = json.dumps(out)
        ok(out["uid"] not in (0, 10001), "a script runs under a uid of its own", detail)
        ok(not out["own"] and not out["others"], "a script finds no key or credential", detail)
        blocked = all(out[k] == "blocked" for k in ("internet", "app", "prefect"))
        ok(blocked, "a script reaches no address", detail)
        ok(out["service"] == "ok", "a script's service call goes through the runner", detail)
        first = start(check, wid, fid, FIRST, 1)
        second = wait(check, wid, start(check, wid, fid, SECOND, 1))
        first = wait(check, wid, first)
        rows = check.json("GET", f"/api/workspaces/{wid}/runs/{second['id']}/rows")["rows"]
        out = rows[0]["output"] if rows else {}
        apart = out == {"read": "refused", "signal": "refused", "proc": "refused"}
        ok(apart and first["status"] == "done", "a script cannot reach a run beside it", str(out))

    check.json("DELETE", f"/api/workspaces/{wid}", expect=204)
    check.request("POST", "/api/auth/logout")
    ok(check.request("GET", "/api/me")[0] == 401, "signing out ends the session")
    print("smoke check passed")


if __name__ == "__main__":
    main()
