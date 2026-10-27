"""The CLI as the executors start it: a real subprocess against real HTTP servers."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from .conftest import TOKEN


class Fake(BaseHTTPRequestHandler):
    records: list[dict[str, Any]] = []
    result: bytes = b""
    progress: list[dict[str, int]] = []

    def log_message(self, format: str, *args: Any) -> None:  # keep test output quiet
        pass

    def _authorized(self) -> bool:
        if self.path.startswith("/runner/") and self.headers["Authorization"] != f"Bearer {TOKEN}":
            self.send_response(404)
            self.end_headers()
            return False
        return True

    def _body(self) -> bytes:
        if self.headers.get("Transfer-Encoding") == "chunked":
            data = b""
            while size := int(self.rfile.readline().strip(), 16):
                data += self.rfile.read(size)
                self.rfile.readline()
            self.rfile.readline()
            return data
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_GET(self) -> None:
        if not self._authorized():
            return
        body = "".join(
            json.dumps({"_row": i, "record": r}) + "\n" for i, r in enumerate(self.records)
        )
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body.encode())

    def do_POST(self) -> None:
        if not self._authorized():
            return
        data = json.loads(self._body())
        if self.path.endswith("/progress"):
            type(self).progress.append(data)
            self.send_response(204)
            self.end_headers()
            return
        reply = json.dumps({"name": data["name"].upper()}).encode()  # the fake service
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def do_PUT(self) -> None:
        if not self._authorized():
            return
        type(self).result = self._body()
        self.send_response(204)
        self.end_headers()


@pytest.fixture
def server() -> Iterator[str]:
    Fake.records = [{"name": f"person {i}"} for i in range(1000)]
    Fake.result, Fake.progress = b"", []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()


def test_cli_runs_a_script_over_a_thousand_records(server: str, tmp_path: Path) -> None:
    (tmp_path / "services.toml").write_text(f'[upper]\nurl = "{server}/upper"\n', encoding="utf-8")
    script = (
        "def process(record, ctx):\n"
        '    return {"upper": ctx.service("upper").post("/", json=record)["name"]}\n'
    )
    params = {
        "run_id": "cli1",
        "script": script,
        "services": ["upper"],
        "input_url": f"{server}/runner/runs/cli1/input",
        "progress_url": f"{server}/runner/runs/cli1/progress",
        "result_url": f"{server}/runner/runs/cli1/result",
        "token": TOKEN,
        "concurrency": 4,
    }
    (tmp_path / "params.json").write_text(json.dumps(params), encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-m", "echolot_runner.cli", str(tmp_path / "params.json")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={"PATH": "", "RUNNER_SERVICES_FILE": str(tmp_path / "services.toml")},
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    summary = json.loads(done.stdout)
    assert summary["total"] == 1000 and summary["ok"] == 1000
    lines = [json.loads(line) for line in Fake.result.decode().splitlines()]
    assert lines[999]["upper"] == "PERSON 999"
    assert Fake.progress[-1]["done"] == 1000


def test_cli_reports_a_failed_run(server: str, tmp_path: Path) -> None:
    params = {
        "run_id": "cli2",
        "script": "def process(record, ctx)\n",
        "input_url": f"{server}/runner/runs/cli2/input",
        "progress_url": f"{server}/runner/runs/cli2/progress",
        "result_url": f"{server}/runner/runs/cli2/result",
        "token": TOKEN,
    }
    (tmp_path / "params.json").write_text(json.dumps(params), encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-m", "echolot_runner.cli", str(tmp_path / "params.json")],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 1
    assert "run failed: the script does not load: SyntaxError" in done.stderr
