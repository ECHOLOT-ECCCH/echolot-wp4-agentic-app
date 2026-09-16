from __future__ import annotations

import json
from typing import Any, cast

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from elexifier_agent.agent import build_agent, render_system_prompt
from elexifier_pipeline.sample import OXFORD_MAPPING

from .conftest import Fixture


async def call_tool(fixture: Fixture, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run one tool call through a scripted model and return its result dict."""
    captured: list[dict[str, Any]] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        assert any(t.name == name for t in info.function_tools), f"tool {name} not registered"
        for message in messages:
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name == name:
                    captured.append(cast(dict[str, Any], part.content))
        if not captured:
            return ModelResponse(parts=[ToolCallPart(tool_name=name, args=args)])
        return ModelResponse(parts=[TextPart(content="done")])

    agent = build_agent(fixture.deps.project.source_type, FunctionModel(script))
    result = await agent.run(f"call {name}", deps=fixture.deps)
    assert result.output == "done"
    assert len(captured) == 1
    return captured[0]


def test_xml_prompt_section_is_included(fixture: Fixture) -> None:
    prompt = render_system_prompt(fixture.deps)
    assert "The mapping format" in prompt
    assert "xml_test_xpath" in prompt


async def test_profile(fixture: Fixture) -> None:
    result = await call_tool(fixture, "xml_profile", {})
    assert result["summary"].startswith("102 entries under /oxford/se")
    data = result["data"]
    assert data["entry_candidates"][0]["tag"] == "se"
    tags = {e["tag"] for e in data["elements"]}
    assert {"se", "hw", "tr", "s2"} <= tags


async def test_samples_search_and_xpath(fixture: Fixture) -> None:
    samples = await call_tool(fixture, "xml_sample_entries", {"ordinals": [1], "n": 1})
    entry = samples["data"]["entries"][0]
    assert entry["headword"] == "b" and entry["xml"].startswith("<se>")
    assert "<hw>b</hw>" in entry["xml"]

    spread = await call_tool(fixture, "xml_sample_entries", {"n": 4, "strategy": "spread"})
    assert len(spread["data"]["entries"]) == 4

    search = await call_tool(fixture, "xml_search", {"query": "baby boom"})
    assert search["data"]["total"] == 2
    assert [h["headword"] for h in search["data"]["hits"]] == ["baby boom", "baby boomer"]

    tested = await call_tool(fixture, "xml_test_xpath", {"xpath": "hg/ps", "n": 5})
    assert "matched in" in tested["summary"]
    assert all("values" in r for r in tested["data"]["results"])

    bad = await call_tool(fixture, "xml_test_xpath", {"xpath": "hg/["})
    assert bad["summary"].startswith("Error: invalid XPath")


async def test_distinct_values(fixture: Fixture) -> None:
    result = await call_tool(fixture, "xml_distinct_values", {"xpath": "hg/ps", "limit": 5})
    data = result["data"]
    assert data["entries_scanned"] == 102
    values = {v["value"]: v["count"] for v in data["values"]}
    assert "sam." in values and values["sam."] > 40


async def test_mapping_write_get_explain(fixture: Fixture) -> None:
    mapping = json.loads(OXFORD_MAPPING.read_text(encoding="utf-8"))
    empty = await call_tool(fixture, "mapping_get", {})
    assert empty["summary"].startswith("No mapping yet")

    stored = await call_tool(fixture, "mapping_write", {"mapping": mapping, "note": "first draft"})
    assert stored["data"]["stored"] is True and stored["data"]["version"] == 1

    broken = dict(mapping)
    broken["children"] = [dict(mapping["children"][0], textVals=[])]  # no headword
    rejected = await call_tool(fixture, "mapping_write", {"mapping": json.dumps(broken)})
    assert rejected["data"]["stored"] is False
    assert any("headword" in i["message"] for i in rejected["data"]["issues"])

    current = await call_tool(fixture, "mapping_get", {})
    assert (
        current["data"]["version"] == 1
        and "lexicographicResource <- /oxford" in current["data"]["summary"]
    )
    explained = await call_tool(fixture, "mapping_explain", {})
    assert "entry <- se" in explained["data"]["explanation"]


async def test_spec_round_trip(fixture: Fixture) -> None:
    assert (await call_tool(fixture, "spec_read", {}))["summary"].startswith(
        "The conversion spec is empty"
    )
    updated = await call_tool(
        fixture, "spec_update", {"markdown": "# Oxford\n\n- entries are <se>"}
    )
    assert updated["data"]["version"] == 1
    read = await call_tool(fixture, "spec_read", {})
    assert read["data"]["markdown"].startswith("# Oxford")
    assert (await call_tool(fixture, "spec_update", {"markdown": "  "}))["summary"].startswith(
        "Error"
    )
