"""Housekeeping for AI SDK UI messages stored per conversation.

The browser sends the whole message list on every turn. After a human-in-the-loop approval the
client keeps the approved tool part in state ``approval-responded`` while the tool's output
arrives in the continuation; the Vercel AI adapter treats every ``approval-responded`` part as a
pending decision and fails the next turn ("no unprocessed tool calls"). ``normalize_ui_messages``
resolves those stale parts against the outputs the server recorded, so a conversation can go on
after approvals, reloads and interrupted turns.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

UiMessage = dict[str, Any]
FINAL_STATES = ("output-available", "output-error", "output-denied")


def _tool_parts(message: UiMessage) -> list[dict[str, Any]]:
    return [
        p
        for p in message.get("parts", [])
        if isinstance(p, dict)
        and str(p.get("type", "")).startswith("tool-")
        and p.get("toolCallId")
    ]


def collect_tool_outputs(messages: list[UiMessage]) -> dict[str, dict[str, Any]]:
    """toolCallId -> the part holding its final state (output, error or denial)."""
    outputs: dict[str, dict[str, Any]] = {}
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for part in _tool_parts(message):
            if part.get("state") in FINAL_STATES:
                outputs[str(part["toolCallId"])] = part
    return outputs


def _last_assistant_index(messages: list[UiMessage]) -> int:
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "assistant":
            return index
    return -1


def normalize_ui_messages(
    messages: list[UiMessage], known_outputs: dict[str, dict[str, Any]] | None = None
) -> list[UiMessage]:
    """Return a copy where tool parts that were answered earlier carry their final state.

    - ``approval-responded`` parts outside the last assistant message take the output recorded
      for the same tool call (from this list or ``known_outputs``); duplicates of that output in
      later messages are removed. Denied ones become ``output-denied``; approved ones with no
      recorded output become ``output-error`` (the turn was interrupted before the tool ran).
    - ``approval-requested`` / ``input-available`` parts outside the last assistant message that
      never got an answer become ``output-denied`` (the lexicographer moved on).
    - Parts in the last assistant message are left alone: they are the live decision.
    """
    messages = copy.deepcopy(messages)
    outputs = dict(known_outputs or {})
    outputs.update(collect_tool_outputs(messages))
    last_assistant = _last_assistant_index(messages)
    is_last_turn = _is_live_approval_turn(messages, last_assistant)
    resolved: set[str] = set()

    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        live = index == last_assistant and is_last_turn
        for part in _tool_parts(message):
            state = part.get("state")
            call_id = str(part["toolCallId"])
            if state in FINAL_STATES or live:
                continue
            recorded = outputs.get(call_id)
            if recorded is not None and recorded is not part:
                for key in ("state", "output", "errorText", "approval", "providerExecuted"):
                    if key in recorded:
                        part[key] = recorded[key]
                    elif key in part and key != "approval":
                        del part[key]
                resolved.add(call_id)
            elif state == "approval-responded":
                approval = part.get("approval") or {}
                if approval.get("approved") is True:
                    part["state"] = "output-error"
                    part["errorText"] = "The tool did not run: the turn was interrupted."
                else:
                    part["state"] = "output-denied"
            elif state in ("approval-requested", "input-available", "input-streaming"):
                part["state"] = "output-denied"
                part.setdefault("approval", {}).update(
                    {"approved": False, "reason": "not answered"}
                )

    if resolved:
        # drop the standalone copies of outputs that were merged into their original parts
        cleaned: list[UiMessage] = []
        for index, message in enumerate(messages):
            if message.get("role") != "assistant":
                cleaned.append(message)
                continue
            parts = message.get("parts", [])
            kept = []
            seen_here: set[str] = set()
            for part in parts:
                call_id = str(part.get("toolCallId") or "")
                if (
                    call_id in resolved
                    and str(part.get("type", "")).startswith("tool-")
                    and _first_occurrence(messages, call_id) != (index, id(part))
                ):
                    continue
                if call_id and call_id in seen_here:
                    continue
                if call_id:
                    seen_here.add(call_id)
                kept.append(part)
            message["parts"] = kept
            if kept:
                cleaned.append(message)
        messages = cleaned
    return messages


def _first_occurrence(messages: list[UiMessage], call_id: str) -> tuple[int, int]:
    for index, message in enumerate(messages):
        for part in message.get("parts", []):
            if str(part.get("toolCallId") or "") == call_id:
                return index, id(part)
    return -1, 0


def _is_live_approval_turn(messages: list[UiMessage], last_assistant: int) -> bool:
    """True when the request is an approval response: the last message is that assistant
    message (nothing was sent after it)."""
    return last_assistant >= 0 and last_assistant == len(messages) - 1


# --- context chips (selection on a PDF page sent with a message) ------------------------------

MAX_CHIP_CHARS = 4000
MAX_CHIPS = 6


class ContextChip(BaseModel):
    """A region the lexicographer selected on a page, attached to the next message."""

    model_config = ConfigDict(extra="ignore")

    document_id: str
    page: int = Field(ge=1)
    bbox: tuple[float, float, float, float]
    text: str = Field(max_length=MAX_CHIP_CHARS)
    words: int = 0
    file_id: str | None = None
    label: str | None = None  # what the UI showed, e.g. "page 3, 12 words"


def chip_text(chip: ContextChip) -> str:
    box = ", ".join(str(round(v)) for v in chip.bbox)
    return f'[Selected on page {chip.page}, box ({box}) in points]\n"""\n{chip.text.strip()}\n"""'


def attach_context(messages: list[UiMessage], chips: list[ContextChip]) -> list[UiMessage]:
    """Add the chips to the last user message: one text part the model sees, and a
    ``data-context`` part the UI can render as chips again after a reload."""
    if not chips or not messages or messages[-1].get("role") != "user":
        return messages
    messages = copy.deepcopy(messages)
    last = messages[-1]
    parts = list(last.get("parts", []))
    already = {
        json.dumps(p.get("data"), sort_keys=True)
        for p in parts
        if p.get("type") == "data-context" and isinstance(p.get("data"), dict)
    }
    for chip in chips[:MAX_CHIPS]:
        data = chip.model_dump(mode="json")
        if json.dumps(data, sort_keys=True) in already:
            continue
        parts.append({"type": "text", "text": chip_text(chip)})
        parts.append({"type": "data-context", "data": data})
    last["parts"] = parts
    return messages


__all__ = [
    "ContextChip",
    "attach_context",
    "chip_text",
    "collect_tool_outputs",
    "normalize_ui_messages",
]
