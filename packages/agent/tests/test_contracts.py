import json
from typing import Any, cast

from pydantic_ai.ui.vercel_ai.response_types import DataChunk

from elexifier_agent.contracts import (
    ArtifactRef,
    ToolResult,
    UiAction,
    error_result,
    to_tool_return,
)


def test_tool_return_shape_and_ui_actions() -> None:
    result = ToolResult(
        summary="Profiled 3 elements",
        data={"elements": ["entry", "form", "sense"]},
        artifacts=[ArtifactRef(file_id="f1", name="profile.json", kind="report")],
        ui_actions=[UiAction(type="open_file", file_id="f1"), UiAction(type="open_page", page=3)],
        cost={"input_tokens": 10},
    )
    tr = to_tool_return(result)
    assert tr.return_value == {
        "summary": "Profiled 3 elements",
        "data": {"elements": ["entry", "form", "sense"]},
        "artifacts": [{"file_id": "f1", "name": "profile.json", "kind": "report"}],
        "cost": {"input_tokens": 10},
    }
    assert tr.metadata is not None and len(tr.metadata) == 2
    chunk = tr.metadata[0]
    assert isinstance(chunk, DataChunk)
    assert chunk.type == "data-ui-action"
    assert chunk.data == {"type": "open_file", "file_id": "f1"}
    assert tr.metadata[1].data == {"type": "open_page", "page": 3}


def test_large_data_is_truncated_for_the_model() -> None:
    tr = to_tool_return(ToolResult(summary="big", data={"rows": ["x" * 100] * 1000}))
    data = cast(dict[str, Any], tr.return_value)["data"]
    assert data["truncated"] is True
    assert len(json.dumps(data)) < 30_000
    assert tr.metadata is None


def test_error_result() -> None:
    tr = error_result("no such file", file_id="abc")
    value = cast(dict[str, Any], tr.return_value)
    assert value["summary"].startswith("Error: no such file")
    assert value["data"] == {"error": "no such file", "file_id": "abc"}
