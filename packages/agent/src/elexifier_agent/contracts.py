"""Tool result contract shared by every tool (docs/PLAN.md 7.1).

``ToolResult.summary`` and ``data`` go to the model; ``ui_actions`` reach only the browser as
``data-ui-action`` chunks attached to the tool return metadata.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import ToolReturn
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

UiActionType = Literal[
    "open_file",
    "open_document",
    "open_entry",
    "open_page",
    "show_overlay",
    "open_mapping",
    "open_run",
    "open_spec",
    "open_artifact",
]

MAX_SUMMARY_CHARS = 500
MAX_DATA_CHARS = 24_000  # roughly 6,000 tokens


class UiAction(BaseModel):
    type: UiActionType
    file_id: str | None = None
    entry_id: str | None = None
    document_id: str | None = None
    page: int | None = None  # 1-based
    bbox: tuple[float, float, float, float] | None = None  # x0, top, x1, bottom; PDF points
    overlay_set_id: str | None = None
    mapping_version: int | None = None
    run_id: str | None = None


class ArtifactRef(BaseModel):
    file_id: str
    name: str
    kind: Literal["report", "sample", "code", "export", "log"] = "report"


class ToolResult(BaseModel):
    summary: str = Field(max_length=MAX_SUMMARY_CHARS)
    data: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    ui_actions: list[UiAction] = Field(default_factory=list)
    cost: dict[str, Any] | None = None


def ui_action_chunk(action: UiAction) -> DataChunk:
    return DataChunk(type="data-ui-action", data=action.model_dump(exclude_none=True))


def to_tool_return(result: ToolResult) -> ToolReturn:
    """Convert a ToolResult to what the tool function returns to Pydantic AI."""
    data = result.data
    serialized = json.dumps(data, default=str, ensure_ascii=False)
    if len(serialized) > MAX_DATA_CHARS:
        data = {
            "truncated": True,
            "note": (
                "The tool output was too large to include in full. "
                "Use narrower parameters or open the attached artifact."
            ),
            "preview": serialized[:MAX_DATA_CHARS],
        }
    return_value: dict[str, Any] = {"summary": result.summary, "data": data}
    if result.artifacts:
        return_value["artifacts"] = [a.model_dump() for a in result.artifacts]
    if result.cost:
        return_value["cost"] = result.cost
    return ToolReturn(
        return_value=return_value,
        metadata=[ui_action_chunk(a) for a in result.ui_actions] or None,
    )


def error_result(message: str, **data: Any) -> ToolReturn:
    """A tool result the model can act on (not an exception)."""
    return to_tool_return(
        ToolResult(summary=f"Error: {message}"[:MAX_SUMMARY_CHARS], data={"error": message, **data})
    )
