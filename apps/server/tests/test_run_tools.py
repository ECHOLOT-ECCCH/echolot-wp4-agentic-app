from __future__ import annotations

from sqlalchemy import func, select

from echolot_app.db import Run
from echolot_app.service_docs import parse_service_doc

from .conftest import UPPER, Live, call_gated_tool, call_tool


def test_service_doc_front_matter() -> None:
    doc = parse_service_doc(
        "---\nname: echolot\ndescription: 'Wikidata candidates'\n---\n# Hi\n", "x"
    )
    assert (doc.name, doc.description, doc.body) == ("echolot", "Wikidata candidates", "# Hi\n")
    assert parse_service_doc("# no front matter\n", "fallback").name == "fallback"


async def test_services_for_the_agent_and_the_ui(live: Live) -> None:
    listed = await call_tool(live.deps(), "list_services", {})
    assert listed["data"]["services"] == [{"name": "upper", "description": "Upper-cases a name"}]
    docs = await call_tool(live.deps(), "service_docs", {"name": "upper"})
    assert '502 on "boom"' in docs["data"]["documentation"]
    missing = await call_tool(live.deps(), "service_docs", {"name": "nope"})
    assert missing["summary"] == "Error: no service 'nope'; known: upper"
    api = await live.client.get("/api/services")
    assert api.json() == [{"name": "upper", "description": "Upper-cases a name"}]


async def test_a_sample_run_waits_and_shows_rows(live: Live) -> None:
    await live.script(UPPER)
    result = await call_gated_tool(
        live.deps(), "submit_run", {"file_id": live.file_id, "purpose": "try ten", "limit": 10}
    )
    assert result is not None
    assert result["summary"].endswith("done: 10 ok, 0 error(s) of 10")
    rows = result["data"]["rows"]
    assert len(rows) == 10
    assert rows[3]["input"]["name"] == "person 3" and rows[3]["output"]["upper"] == "PERSON 3"
    async with live.state.db.session() as db:
        run = (await db.execute(select(Run))).scalar_one()
    assert run.submitted_by == "agent" and run.purpose == "try ten"


async def test_a_full_run_then_status_results_and_retry(live: Live) -> None:
    await live.script(UPPER)
    started = await call_gated_tool(
        live.deps(), "submit_run", {"file_id": live.file_id, "purpose": "all of them"}
    )
    assert started is not None and "started on 50 records" in started["summary"]
    run_id = started["data"]["run_id"]
    status = await call_tool(live.deps(), "run_status", {"run_id": run_id, "wait_s": 60})
    assert status["summary"].endswith("done: 49 ok, 1 error(s) of 50")
    assert status["data"]["first_errors"] == [
        {"row": 17, "error": "ServiceError: 502 from upper: upstream failed (after 2 attempts)"}
    ]
    errors = await call_tool(live.deps(), "run_results", {"run_id": run_id, "status": "error"})
    assert errors["data"]["total"] == 1 and errors["data"]["rows"][0]["input"]["name"] == "boom"
    some = await call_tool(live.deps(), "run_results", {"run_id": run_id, "n": 500})
    assert len(some["data"]["rows"]) == 20

    retried = await call_gated_tool(live.deps(), "retry_failed", {"run_id": run_id})
    assert retried is not None and retried["summary"].endswith("retries 1 failed record(s)")
    final = await live.wait(retried["data"]["run_id"])
    assert final["parent_run_id"] == run_id and final["counts"]["total"] == 1


async def test_denied_runs_do_not_start(live: Live) -> None:
    await live.script(UPPER)
    result = await call_gated_tool(
        live.deps(), "submit_run", {"file_id": live.file_id, "purpose": "x"}, approve=False
    )
    assert result is None
    async with live.state.db.session() as db:
        assert (await db.execute(select(func.count()).select_from(Run))).scalar_one() == 0


async def test_cancel_and_bad_arguments(live: Live) -> None:
    await live.script(
        "import time\n\ndef process(record, ctx):\n    time.sleep(0.5)\n    return {}\n"
    )
    started = await call_gated_tool(
        live.deps(),
        "submit_run",
        {"file_id": live.file_id, "purpose": "slow", "concurrency": 1},
    )
    assert started is not None
    cancelled = await call_tool(live.deps(), "cancel_run", {"run_id": started["data"]["run_id"]})
    assert "cancel" in cancelled["summary"]
    again = await call_tool(live.deps(), "cancel_run", {"run_id": started["data"]["run_id"]})
    assert again["summary"] == f"Error: run {started['data']['run_id']} is already cancelled"

    bad_file = await call_gated_tool(live.deps(), "submit_run", {"file_id": "x", "purpose": "x"})
    assert bad_file is not None and bad_file["summary"].startswith("Error: bad run parameters")
    bad_run = await call_tool(live.deps(), "run_status", {"run_id": "nope"})
    assert bad_run["summary"] == "Error: 'nope' is not a run id"
