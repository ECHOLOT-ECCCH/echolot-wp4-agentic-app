from __future__ import annotations

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.ui.vercel_ai import VercelAIAdapter

from elexifier_agent.runtime import with_orphan_tool_calls
from elexifier_agent.ui_messages import collect_tool_outputs, normalize_ui_messages


def _user(text: str, mid: str = "u1") -> dict:
    return {"id": mid, "role": "user", "parts": [{"type": "text", "text": text}]}


def _stale_assistant() -> dict:
    return {
        "id": "a1",
        "role": "assistant",
        "parts": [
            {"type": "text", "text": "Stored.", "state": "done"},
            {
                "type": "tool-run_conversion",
                "toolCallId": "call_full",
                "state": "approval-responded",
                "input": {"kind": "full"},
                "approval": {"id": "call_full", "approved": True},
            },
        ],
    }


def _server_output() -> dict:
    return {
        "id": "s1",
        "role": "assistant",
        "parts": [
            {
                "type": "tool-run_conversion",
                "toolCallId": "call_full",
                "state": "output-available",
                "input": {"kind": "full"},
                "output": {"summary": "Full run queued"},
            },
            {"type": "text", "text": "Queued.", "state": "done"},
        ],
    }


def test_live_approval_turn_is_left_alone() -> None:
    messages = [_user("go"), _stale_assistant()]
    assert normalize_ui_messages(messages) == messages


def test_stale_approval_takes_the_recorded_output_and_dedupes() -> None:
    messages = [_user("go"), _stale_assistant(), _server_output(), _user("next", "u2")]
    out = normalize_ui_messages(messages)
    part = out[1]["parts"][1]
    assert part["state"] == "output-available" and part["output"] == {"summary": "Full run queued"}
    assert part["approval"] == {"id": "call_full", "approved": True}
    # the server copy of the output is gone, its text stays
    assert [p["type"] for p in out[2]["parts"]] == ["text"]
    assert out[3]["role"] == "user"


def test_stale_approval_resolves_from_known_outputs() -> None:
    known = collect_tool_outputs([_server_output()])
    out = normalize_ui_messages([_user("go"), _stale_assistant(), _user("next", "u2")], known)
    assert out[1]["parts"][1]["state"] == "output-available"


def test_unanswered_and_interrupted_parts_get_final_states() -> None:
    interrupted = _stale_assistant()
    requested = {
        "id": "a2",
        "role": "assistant",
        "parts": [
            {
                "type": "tool-demo_requires_approval",
                "toolCallId": "call_demo",
                "state": "approval-requested",
                "input": {},
                "approval": {"id": "call_demo"},
            }
        ],
    }
    denied = _stale_assistant()
    denied["id"] = "a3"
    denied["parts"][1]["approval"] = {"id": "call_full", "approved": False, "reason": "no"}
    out = normalize_ui_messages(
        [
            _user("a"),
            interrupted,
            _user("b", "u2"),
            requested,
            _user("c", "u3"),
            denied,
            _user("d", "u4"),
        ]
    )
    assert out[1]["parts"][1]["state"] == "output-error"
    assert "interrupted" in out[1]["parts"][1]["errorText"]
    assert out[3]["parts"][0]["state"] == "output-denied"
    assert out[5]["parts"][1]["state"] == "output-denied"


def test_orphan_tool_returns_get_their_calls_back() -> None:
    history = [
        ModelRequest(parts=[UserPromptPart(content="go")]),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="run_conversion", args={"kind": "full"}, tool_call_id="c1")
            ]
        ),
    ]
    new = [
        ModelRequest(
            parts=[ToolReturnPart(tool_name="run_conversion", content={"ok": 1}, tool_call_id="c1")]
        ),
        ModelResponse(parts=[TextPart(content="done")]),
    ]
    dumped = VercelAIAdapter.dump_messages(
        with_orphan_tool_calls(new, [*history, *new]), sdk_version=7
    )
    states = [(p.type, getattr(p, "state", None)) for m in dumped for p in m.parts]
    assert ("tool-run_conversion", "output-available") in states
    # nothing to do when the calls are already there
    assert with_orphan_tool_calls([*history, *new], [*history, *new]) == [*history, *new]
    assert with_orphan_tool_calls(new[1:], [*history, *new]) == new[1:]
