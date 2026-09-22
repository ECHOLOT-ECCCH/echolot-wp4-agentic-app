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


def test_context_chips_become_a_quoted_block_on_the_last_user_message() -> None:
    from elexifier_agent.ui_messages import ContextChip, attach_context

    chip = ContextChip(
        document_id="d1", page=3, bbox=(50, 100.4, 280, 130), text="abacus n. a frame", words=4
    )
    messages = [_user("what is this?")]
    out = attach_context(messages, [chip])
    parts = out[0]["parts"]
    assert parts[0]["type"] == "text" and parts[0]["text"] == "what is this?"
    assert parts[1]["type"] == "text"
    assert parts[1]["text"].startswith("[Selected on page 3, box (50, 100, 280, 130) in points]")
    assert "abacus n. a frame" in parts[1]["text"]
    assert parts[2]["type"] == "data-context" and parts[2]["data"]["page"] == 3
    # idempotent: the same chip is not attached twice (the browser resends the message list)
    assert attach_context(out, [chip]) == out
    # nothing happens when the last message is not from the user
    assert attach_context([_user("a"), _stale_assistant()], [chip])[-1] == _stale_assistant()


def test_entry_chips_quote_source_output_and_issues() -> None:
    from elexifier_agent.ui_messages import ContextChip, chip_text

    chip = ContextChip(
        kind="entry",
        entry_id="e1",
        ordinal=7,
        headword="babble",
        document_name="oxford.xml",
        origin="converted live with mapping v3",
        source_text="<se><hw>babble</hw></se>",
        output_xml='<entry id="e8"><headword>babble</headword></entry>',
        issues=["sense[1]: sense has no content"],
    )
    text = chip_text(chip)
    assert text.startswith("[Attached: entry 8 “babble” of oxford.xml; converted live")
    assert 'Source XML:\n"""\n<se><hw>babble</hw></se>\n"""' in text
    assert 'DMLex output:\n"""\n<entry id="e8">' in text
    assert text.endswith("Issues: sense[1]: sense has no content")
    span = ContextChip(
        kind="entry", source_kind="pdf", page=12, headword="bond", source_text="bond I sam."
    )
    assert "Text of the span on page 12:" in chip_text(span)
    import pytest

    with pytest.raises(ValueError, match="needs the source text or the output"):
        ContextChip(kind="entry", headword="x")
    with pytest.raises(ValueError, match="needs document_id, page and bbox"):
        ContextChip(kind="region", text="x")


def test_viewing_note_is_added_once_and_restored_on_the_next_turn() -> None:
    from elexifier_agent.ui_messages import attach_viewing, restore_attachments

    messages = [_user("is this right?")]
    noted = attach_viewing(
        messages, "entry 8  “babble” of oxford.xml", {"kind": "entry", "entry_id": "e1"}
    )
    parts = noted[0]["parts"]
    assert parts[1] == {"type": "text", "text": "[Looking at: entry 8 “babble” of oxford.xml]"}
    assert parts[2]["type"] == "data-viewing" and parts[2]["data"]["entry_id"] == "e1"
    assert parts[2]["data"]["description"] == "entry 8 “babble” of oxford.xml"
    assert attach_viewing(noted, "something else", {}) == noted  # once per message
    assert attach_viewing([_user("a"), _stale_assistant()], "x", {}) == [
        _user("a"),
        _stale_assistant(),
    ]
    assert attach_viewing(messages, "   ", {}) == messages

    # the browser resends its own copy of the message (without the server's parts): the stored
    # parts win when the browser's parts are their prefix, and nothing else is touched
    resent = [_user("is this right?"), _user("and this?", "u2")]
    restored = restore_attachments(resent, noted)
    assert restored[0]["parts"] == parts and restored[1] == _user("and this?", "u2")
    edited = [{**_user("is this wrong?"), "id": "u1"}]
    assert restore_attachments(edited, noted) == edited  # a changed message is not overwritten
    assert restore_attachments(resent, []) == resent
