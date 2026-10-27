"""The service docs shipped in services/: they parse, and their example scripts keep the
contract and work against a fake of the service."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

from echolot_app.scripts import check_script
from echolot_app.service_docs import load_service_docs
from echolot_runner.harness import run
from echolot_runner.services import ServiceConfig

SERVICES = Path(__file__).parents[3] / "services"


def example_script(body: str) -> str:
    match = re.search(r"```python\n(.*?)```", body, re.S)
    assert match, "the doc has no python example"
    return match.group(1)


def test_the_shipped_docs_parse() -> None:
    docs = load_service_docs(SERVICES)
    assert "echolot" in docs
    assert docs["echolot"].description.startswith("Proposes Wikidata items")


def test_the_echolot_example_script_works() -> None:
    code = example_script(load_service_docs(SERVICES)["echolot"].body)
    assert check_script(code).services == ["echolot"]

    sent: list[dict[str, Any]] = []
    records = [
        {
            "uri": "u0",
            "name": 'Brian "B." O\'Doherty',
            "type": "person",
            "description": "Irish artist",
            "wikidata": "http://www.wikidata.org/entity/Q3777954",
        },
        {"uri": "u1", "name": "Ana", "type": "person", "description": "", "wikidata": "Q1"},
        {"uri": "u2", "name": "Jon", "type": "", "description": "", "wikidata": ""},
        {"uri": "u3", "name": "Nobody", "type": "person", "description": "", "wikidata": ""},
        {"uri": "u4", "name": "  ", "type": "person", "description": "", "wikidata": ""},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "app.test":
            if request.url.path.endswith("/input"):
                lines = "".join(
                    json.dumps({"_row": i, "record": r}) + "\n" for i, r in enumerate(records)
                )
                return httpx.Response(200, content=lines.encode())
            if request.url.path.endswith("/result"):
                handler.result = request.read()  # type: ignore[attr-defined]
            return httpx.Response(204)
        entity = json.loads(request.content)
        sent.append(entity)
        qid = {"u0": "Q3777954", "u1": "Q2", "u2": "Q9"}.get(entity["uri"])
        matches = (
            [
                {
                    "url": "http://en.wikipedia.org/wiki/X",
                    "wikidata_item_id": qid,
                    "title": entity["name"],
                    "score": 0.9,
                }
            ]
            if qid
            else []
        )
        return httpx.Response(200, json={"uri": entity["uri"], "matches": matches})

    base = "http://app.test/runner/runs/r"
    params = {
        "run_id": "r",
        "script": code,
        "services": ["echolot"],
        "token": "t",
        "input_url": f"{base}/input",
        "progress_url": f"{base}/progress",
        "result_url": f"{base}/result",
    }
    services = {"echolot": ServiceConfig(name="echolot", url="http://echolot.test", backoff_s=0)}
    client = httpx.Client(transport=httpx.MockTransport(handler))
    summary = run(params, http=client, services=services)
    assert summary == {**summary, "total": 5, "ok": 4, "error": 1}
    lines = [json.loads(x) for x in handler.result.decode().splitlines()]  # type: ignore[attr-defined]
    assert [x.get("verdict") for x in lines] == ["agree", "conflict", "new", "none", None]
    assert lines[4]["_error"] == "ValueError: the record has no name"
    assert sent[0]["name"] == "Brian B. O'Doherty"  # quotes stripped
    assert sent[0]["identifiers"] == {"wikidata": "http://www.wikidata.org/entity/Q3777954"}
    assert sent[2]["entity_type"] == "unknown"
