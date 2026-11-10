"""The service docs shipped in services/: they parse, and their example scripts keep the
contract and work against a fake of the service."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from echolot_app.scripts import check_script
from echolot_app.service_docs import load_service_docs
from echolot_runner.harness import run
from echolot_runner.services import ServiceConfig

SERVICES = Path(__file__).parents[3] / "services"


def example_scripts(body: str) -> list[str]:
    scripts = re.findall(r"```python\n(.*?)```", body, re.S)
    assert scripts, "the doc has no python example"
    return scripts


def run_script(
    code: str,
    records: list[dict[str, Any]],
    reply: Callable[[dict[str, Any], str], list[dict[str, Any]]],
    script_params: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[tuple[str, dict[str, Any]]]]:
    """Runs a doc's script through the harness against a fake ECHOLOT: ``reply(entity,
    algorithm)`` gives the matches. Returns the summary, the result lines and what was sent."""
    sent: list[tuple[str, dict[str, Any]]] = []
    result: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "app.test":
            if request.url.path.endswith("/input"):
                lines = "".join(
                    json.dumps({"_row": i, "record": r}) + "\n" for i, r in enumerate(records)
                )
                return httpx.Response(200, content=lines.encode())
            if request.url.path.endswith("/result"):
                result.append(request.read())
            return httpx.Response(204)
        algorithm = request.url.params.get("algorithm", "wikifier")
        entity = json.loads(request.content)
        sent.append((algorithm, entity))
        matches = reply(entity, algorithm)
        body = {"uri": entity["uri"], "algorithm": algorithm, "matches": matches}
        return httpx.Response(200, json=body)

    base = "http://app.test/runner/runs/r"
    params = {
        "run_id": "r",
        "script": code,
        "services": ["echolot"],
        "token": "t",
        "input_url": f"{base}/input",
        "progress_url": f"{base}/progress",
        "result_url": f"{base}/result",
        "script_params": script_params or {},
        "concurrency": 1,
    }
    services = {"echolot": ServiceConfig(name="echolot", url="http://echolot.test", backoff_s=0)}
    client = httpx.Client(transport=httpx.MockTransport(handler))
    summary = run(params, http=client, services=services)
    lines = [json.loads(x) for x in result[0].decode().splitlines()]
    return summary, lines, sent


def match(qid: str | None, title: str, score: float = 0.95, url: str = "") -> dict[str, Any]:
    url = url or f"http://www.wikidata.org/entity/{qid}"
    return {"url": url, "wikidata_item_id": qid, "title": title, "score": score}


def test_the_shipped_docs_parse() -> None:
    docs = load_service_docs(SERVICES)
    assert "echolot" in docs
    assert docs["echolot"].description.startswith("Proposes Wikidata items")
    assert len(example_scripts(docs["echolot"].body)) == 2


def test_the_echolot_example_script_works() -> None:
    code = example_scripts(load_service_docs(SERVICES)["echolot"].body)[0]
    assert check_script(code).services == ["echolot"]
    records = [
        {
            "uri": "u0",
            "name": 'Brian "B." O\'Doherty',
            "type": "person",
            "description": "Irish artist",
            "wikidata": "http://www.wikidata.org/entity/Q3777954",
            "birth_year": 1928,
        },
        {"uri": "u1", "name": "Ana", "type": "Person", "description": "", "wikidata": "Q1"},
        {"uri": "u2", "name": "Jon", "type": "", "description": "", "wikidata": ""},
        {"uri": "u3", "name": "Nobody", "type": "person", "description": "", "wikidata": ""},
        {"uri": "u4", "name": "  ", "type": "person", "description": "", "wikidata": ""},
        {"uri": "u5", "name": "Iker", "type": "person", "description": "", "wikidata": ""},
    ]
    ulan = "http://vocab.getty.edu/ulan/500000001"

    def reply(entity: dict[str, Any], algorithm: str) -> list[dict[str, Any]]:
        qid = {"u0": "Q3777954", "u1": "Q2", "u2": "Q9"}.get(entity["uri"])
        if entity["uri"] == "u5":
            return [match(None, "Iker", url=ulan)]
        return [match(qid, entity["name"], 1.0)] if qid else []

    # the default algorithm
    summary, lines, sent = run_script(code, records, reply)
    assert summary == {**summary, "total": 6, "ok": 5, "error": 1}
    assert {algorithm for algorithm, _ in sent} == {"wikifier"}
    assert [x.get("verdict") for x in lines] == ["agree", "conflict", "new", "none", None, "no QID"]
    assert lines[4]["_error"] == "ValueError: the record has no name"
    assert lines[0]["top_score"] == 1.0
    assert lines[0]["matches"][0]["algorithm"] == "wikifier"
    entity = sent[0][1]
    assert entity["name"] == "Brian B. O'Doherty"  # quotes stripped
    assert entity["identifiers"] == {"wikidata": "http://www.wikidata.org/entity/Q3777954"}
    assert entity["information"] == {"birth_year": "1928"}  # a string, never a number
    assert [e["entity_type"] for _, e in sent[:3]] == ["person", "person", "unknown"]

    # the algorithm the run asks for; llm_based's score is not passed on as one
    _summary, lines, sent = run_script(code, records, reply, {"algorithm": "llm_based"})
    assert {algorithm for algorithm, _ in sent} == {"llm_based"}
    assert lines[0]["algorithm"] == "llm_based"
    assert lines[0]["top_score"] is None
    assert lines[0]["matches"][0]["score"] == 1.0  # kept in the list, shown as "selected"


def test_the_echolot_comparison_script_works() -> None:
    code = example_scripts(load_service_docs(SERVICES)["echolot"].body)[1]
    assert check_script(code).services == ["echolot"]
    found = {
        ("Ana", "wikifier"): "Q1",
        ("Ana", "llm_based"): "Q1",
        ("Ana", "rule_based"): "Q1",
        ("Jon", "wikifier"): "Q2",
        ("Miren", "wikifier"): "Q3",
        ("Miren", "rule_based"): "Q4",
    }

    def reply(entity: dict[str, Any], algorithm: str) -> list[dict[str, Any]]:
        qid = found.get((entity["name"], algorithm))
        return [match(qid, entity["name"])] if qid else []

    names = ["Ana", "Jon", "Miren", "Nobody"]
    records = [{"uri": f"u{i}", "name": n, "description": ""} for i, n in enumerate(names)]
    summary, lines, sent = run_script(code, records, reply)
    assert summary == {**summary, "total": 4, "ok": 4}
    assert [x["agreement"] for x in lines] == ["all agree", "some agree", "conflict", "none"]
    assert lines[2] == {
        **lines[2],
        "wikifier_qid": "Q3",
        "llm_based_qid": None,
        "rule_based_qid": "Q4",
        "llm_based": [],
    }
    assert lines[0]["rule_based"][0]["algorithm"] == "rule_based"
    assert [a for a, _ in sent[:3]] == ["wikifier", "llm_based", "rule_based"]
    assert all(e["entity_type"] == "person" for _, e in sent)

    # an algorithm left out by the run's script_params
    _summary, lines, sent = run_script(code, records, reply, {"algorithms": ["wikifier"]})
    assert {a for a, _ in sent} == {"wikifier"}
    assert "rule_based" not in lines[0]
