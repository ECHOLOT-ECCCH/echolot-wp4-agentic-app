from __future__ import annotations

import httpx
import pytest

from echolot_app.agent.agent import workspace_block
from echolot_app.scripts import ScriptError, check_script

from .conftest import Workspace, call_tool

GOOD = """
def setup(ctx):
    ctx.log("ready")


def process(record, ctx):
    reply = ctx.service("echolot").post("/reconcile", json={"name": record["name"]})
    extra = ctx.service("wikidata").get("/w/api.php")
    again = ctx.service("echolot")
    return {"matches": reply["matches"]}
"""


def test_a_good_script_lists_its_services() -> None:
    checked = check_script(GOOD)
    assert checked.services == ["echolot", "wikidata"]
    assert checked.has_setup


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("", "the script is empty"),
        ("def process(record, ctx)\n    return {}\n", "syntax error at line 1"),
        ("def run(record, ctx):\n    return {}\n", "needs a top-level function: def process"),
        ("async def process(record, ctx):\n    return {}\n", "plain function (def), not async"),
        (
            "def process(record):\n    return {}\n",
            "process takes (record, ctx); it is defined with (record)",
        ),
        ("def process(r, c, extra):\n    return {}\n", "it is defined with (r, c, extra)"),
        ("def setup():\n    pass\ndef process(r, c):\n    return {}\n", "setup takes (ctx)"),
        (
            "NAME = 'echolot'\ndef process(r, ctx):\n    return ctx.service(NAME).get('/')\n",
            "line 3: ctx.service needs the service name as a literal string",
        ),
    ],
)
def test_contract_violations_are_precise(code: str, message: str) -> None:
    with pytest.raises(ScriptError, match=message.replace("(", r"\(").replace(")", r"\)")):
        check_script(code)


def test_optional_extra_parameters_are_allowed() -> None:
    assert check_script("def process(record, ctx, debug=False):\n    return {}\n").services == []


async def test_user_versions_through_the_api(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    base = f"/api/workspaces/{workspace.id}/scripts"
    assert (await client.get(f"{base}/latest")).status_code == 404
    first = await client.post(base, json={"code": GOOD, "note": "first"})
    assert first.status_code == 201
    assert first.json()["version"] == 1 and first.json()["author"] == "user"
    bad = await client.post(base, json={"code": "def process(record):\n    return {}\n"})
    assert bad.status_code == 422
    assert "process takes (record, ctx)" in bad.json()["detail"]
    second = await client.post(base, json={"code": GOOD.replace("ready", "go")})
    assert second.json()["version"] == 2
    assert [s["version"] for s in (await client.get(base)).json()] == [2, 1]
    assert "code" not in (await client.get(base)).json()[0]
    assert (await client.get(f"{base}/latest")).json()["version"] == 2
    assert "ready" in (await client.get(f"{base}/1")).json()["code"]
    assert (await client.get(f"{base}/9")).status_code == 404


async def test_agent_writes_and_reads_scripts(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    none_yet = await call_tool(workspace.deps, "script_read", {})
    assert none_yet["summary"] == "There is no script yet; write one."
    written = await call_tool(workspace.deps, "script_write", {"code": GOOD, "note": "v1"})
    assert written["summary"] == "Stored script version 1 (services: echolot, wikidata)"
    rejected = await call_tool(
        workspace.deps, "script_write", {"code": "def process(r):\n  pass\n", "note": "bad"}
    )
    assert rejected["summary"].startswith("Error: the script was not stored: process takes")
    # the user edits it in the editor: the agent sees version 2 by the user
    await client.post(
        f"/api/workspaces/{workspace.id}/scripts", json={"code": GOOD + "\n# edited\n"}
    )
    read = await call_tool(workspace.deps, "script_read", {})
    assert read["summary"] == "Script version 2, written by the user"
    assert read["data"]["code"].endswith("# edited\n")
    old = await call_tool(workspace.deps, "script_read", {"version": 1})
    assert old["data"]["author"] == "agent"
    block = await workspace_block(workspace.deps)
    assert "- Script: version 2, written by the user, uses echolot, wikidata" in block
