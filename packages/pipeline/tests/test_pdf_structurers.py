from __future__ import annotations

import io

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from elexifier_pipeline.dmlex.validator import validate_entries
from elexifier_pipeline.pdf.structurers import (
    DmlexEntryDraft,
    DraftExample,
    DraftSense,
    ExtractedEntry,
    LlmStructurer,
    RuleStructurer,
    StructureProfile,
    draft_to_dmlex,
    estimate_cost,
)


def test_draft_to_dmlex_is_valid_dmlex() -> None:
    draft = DmlexEntryDraft(
        headword="abacus",
        parts_of_speech=["n."],
        labels=["hist."],
        senses=[
            DraftSense(
                definitions=["a frame with beads used for counting"],
                translations=["računalo"],
                examples=[DraftExample(text="an old abacus", translations=["staro računalo"])],
            ),
            DraftSense(indicator="fig.", definitions=["any counting device"]),
        ],
        confidence=0.9,
    )
    entry = draft_to_dmlex(draft, entry_id="e1", target_language="sl")
    assert entry["headword"] == "abacus" and entry["partsOfSpeech"] == [{"tag": "n."}]
    assert entry["senses"][0]["id"] == "e1.s1"
    assert entry["senses"][0]["headwordTranslations"] == [{"text": "računalo", "langCode": "sl"}]
    assert entry["senses"][0]["examples"][0]["exampleTranslations"][0]["text"] == "staro računalo"
    result = validate_entries({"uri": "urn:t", "langCode": "en"}, [entry])
    assert result.report.valid, result.report.errors


async def test_rule_structurer_and_estimate() -> None:
    entries = [
        ExtractedEntry(span_id="p1-e1", page=1, bbox=(0, 0, 10, 10), text="abacus n. a frame"),
        ExtractedEntry(span_id="p1-e2", page=1, bbox=(0, 0, 10, 10), text="abandon"),
    ]
    out = [s async for s in RuleStructurer().structure(entries)]
    assert [s.entry["headword"] for s in out] == ["abacus", "abandon"]
    assert out[0].entry["senses"][0]["definitions"] == [{"text": "n. a frame"}]
    assert "senses" not in out[1].entry and out[0].confidence == 0.3

    free = await RuleStructurer().estimate(10, 200, True)
    assert free.usd_estimate == 0.0
    paid = estimate_cost(100, 400, True, price_in_per_m=3.0, price_out_per_m=15.0)
    assert paid.entries == 100 and paid.per_entry_input == 700 + 100 + 1100
    assert paid.usd_estimate == pytest.approx(
        paid.input_tokens / 1e6 * 3 + paid.output_tokens / 1e6 * 15, rel=1e-6
    )
    unknown = estimate_cost(5, 100, False)
    assert unknown.usd_estimate is None and "no prices" in unknown.notes[0]


async def test_llm_structurer_with_scripted_model() -> None:
    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # the scripted model answers with a JSON draft through the output tool
        from pydantic_ai.messages import ToolCallPart

        prompt = str(messages[-1].parts[0].content)  # type: ignore[union-attr]
        headword = "abacus" if "abacus" in prompt else "zzz"
        tool = info.output_tools[0]
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=tool.name,
                    args={
                        "headword": headword,
                        "parts_of_speech": ["n."],
                        "senses": [{"definitions": ["a frame with beads"]}],
                        "confidence": 0.8,
                        "notes": [],
                    },
                )
            ]
        )

    structurer = LlmStructurer(
        FunctionModel(script),
        profile=StructureProfile(source_language="en", target_languages=["sl"]),
        concurrency=2,
    )
    entries = [
        ExtractedEntry(span_id="p1-e1", page=1, bbox=(0, 0, 10, 10), text="abacus n. a frame"),
        ExtractedEntry(
            span_id="p1-e2", page=1, bbox=(0, 0, 10, 10), text="other", page_image_png=b"\x89PNG"
        ),
    ]
    out = [s async for s in structurer.structure(entries)]
    assert [s.span_id for s in out] == ["p1-e1", "p1-e2"]
    assert out[0].entry["headword"] == "abacus" and out[0].entry["id"] == "e1"
    assert out[1].entry["headword"] == "zzz" and out[1].entry["id"] == "e2"
    assert out[0].confidence == 0.8 and out[0].usage["requests"] >= 1
    assert all(s.error is None for s in out)
    _ = TextPart, io  # keep imports referenced
