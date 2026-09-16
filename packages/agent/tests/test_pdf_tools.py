from __future__ import annotations

from sqlalchemy import func, select

from elexifier_agent.agent import render_system_prompt
from elexifier_core.db.models import Annotation, Document

from .conftest import Fixture
from .test_xml_tools import call_tool


def test_pdf_prompt_section(pdf_fixture: Fixture) -> None:
    prompt = render_system_prompt(pdf_fixture.deps)
    assert "pdf_segment" in prompt and "xml_test_xpath" not in prompt


async def test_pdf_analyze_and_segment(pdf_fixture: Fixture) -> None:
    analysed = await call_tool(pdf_fixture, "pdf_analyze", {})
    data = analysed["data"]
    assert analysed["summary"].startswith("4 pages (3 with text)")
    assert data["columns"] == {"1": 1, "2": 2} and data["body_size"] == 9.5
    assert data["bold_start_share"] > 0.3 and data["segmentation"] is None
    assert data["fonts"][0]["font"] == "Helvetica" and data["sample_lines"][1].startswith("abacus")
    assert data["warnings"] and "no text layer" in data["warnings"][0]

    segmented = await call_tool(pdf_fixture, "pdf_segment", {})
    assert segmented["summary"].startswith("130 entry spans on 3 pages with the rule segmenter")
    sdata = segmented["data"]
    assert sdata["stats"]["mode"] == "bold" and sdata["first_headwords"][:3] == [
        "abacus",
        "abandon",
        "abase",
    ]
    assert sdata["spans_per_page"] == {"1": 58, "2": 56, "3": 16}
    async with pdf_fixture.db.session() as db:
        count = (await db.execute(select(func.count()).select_from(Annotation))).scalar()
        assert count == 130
        first = (
            await db.execute(select(Annotation).order_by(Annotation.position).limit(1))
        ).scalar_one()
        assert first.status == "proposed" and first.payload["headword_guess"] == "abacus"
        assert len(first.bbox) == 4 and first.set_id is not None
        document = await db.get(Document, pdf_fixture.document_id)
        assert document is not None and document.entry_count == 130
        assert document.stats["segmentation"]["stats"]["spans"] == 130

    # a second run with a poor mode replaces the proposals
    poor = await call_tool(pdf_fixture, "pdf_segment", {"params": {"mode": "size"}})
    assert "Warnings:" in poor["summary"]
    async with pdf_fixture.db.session() as db:
        assert (await db.execute(select(func.count()).select_from(Annotation))).scalar() == 3

    again = await call_tool(pdf_fixture, "pdf_analyze", {})
    assert again["data"]["segmentation"]["stats"]["spans"] == 3
    assert again["data"]["annotations"] == {"proposed": 3}

    bad = await call_tool(pdf_fixture, "pdf_segment", {"params": {"mode": "magic"}})
    assert bad["summary"].startswith("Error")
    unknown = await call_tool(pdf_fixture, "pdf_segment", {"segmenter": "nope"})
    assert unknown["summary"].startswith("Error: unknown segmenter")


async def test_pdf_get_page_text_and_image(pdf_fixture: Fixture) -> None:
    whole = await call_tool(pdf_fixture, "pdf_get_page", {"page": 1})
    data = whole["data"]
    assert whole["summary"].startswith("133 line(s) in page 1")
    assert data["lines"][1]["text"].startswith("abacus n.") and data["lines"][1]["bold_start"]
    assert data["page_size"] == [595.0, 842.0]

    region = await call_tool(
        pdf_fixture, "pdf_get_page", {"page": 1, "bbox": [40, 45, 300, 70], "with_image": True}
    )
    assert region["data"]["lines"] and all(line["bbox"][1] < 70 for line in region["data"]["lines"])
    assert "image is attached" in region["summary"]

    missing = await call_tool(pdf_fixture, "pdf_get_page", {"page": 9})
    assert missing["summary"].startswith("Error: page 9 does not exist")
    bad = await call_tool(pdf_fixture, "pdf_get_page", {"page": 1, "bbox": [10, 10, 5, 5]})
    assert bad["summary"].startswith("Error: bbox")
    scanned = await call_tool(pdf_fixture, "pdf_get_page", {"page": 4})
    assert "no text layer" in scanned["summary"]


async def test_structure_sample_with_rule_and_scripted_llm(pdf_fixture: Fixture) -> None:
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel
    from sqlalchemy import select as sa_select

    from elexifier_core.db.models import ConversionRun, ConvertedEntry, Project, UsageRecord

    nothing = await call_tool(pdf_fixture, "pdf_structure", {"n": 3})
    assert nothing["summary"].startswith("Error: no entry spans")

    await call_tool(pdf_fixture, "pdf_segment", {})
    estimate = await call_tool(pdf_fixture, "pdf_estimate_cost", {"with_images": False})
    assert estimate["data"]["entries"] == 130 and estimate["data"]["usd_estimate"] is None
    assert "130 entry spans (0 reviewed)" in estimate["summary"]

    # the free baseline works without a model
    rule = await call_tool(
        pdf_fixture,
        "pdf_structure",
        {"n": 4, "structurer": "rule", "profile": {"source_language": "en", "contents": "test"}},
    )
    assert rule["summary"].startswith("Structured 4 of 4 spans with the rule structurer")
    assert rule["data"]["entries"][0]["headword"] == "abacus"
    assert rule["data"]["entries"][0]["dmlex"]["senses"][0]["definitions"][0]["text"].startswith(
        "n. a frame"
    )
    async with pdf_fixture.db.session() as db:
        project = await db.get(Project, pdf_fixture.project_id)
        assert (
            project is not None and project.settings["structure_profile"]["source_language"] == "en"
        )

    # the model path: a scripted structurer model answers the output tool
    def structurer_script(messages, info: AgentInfo) -> ModelResponse:  # type: ignore[no-untyped-def]
        prompt = str(messages[-1].parts[0].content)  # type: ignore[union-attr]
        headword = prompt.split(":\n", 1)[1].split()[0]
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=info.output_tools[0].name,
                    args={
                        "headword": headword,
                        "parts_of_speech": ["n."],
                        "senses": [{"definitions": ["something"], "translations": ["nekaj"]}],
                        "confidence": 0.55 if headword == "abandon" else 0.9,
                        "notes": ["low confidence"] if headword == "abandon" else [],
                    },
                )
            ]
        )

    pdf_fixture.deps.model = FunctionModel(structurer_script)
    pdf_fixture.deps.model_spec = {"provider": "test", "model_id": "scripted"}
    llm = await call_tool(
        pdf_fixture,
        "pdf_structure",
        {"n": 3, "strategy": "first", "profile": {"target_languages": ["sl"]}},
    )
    data = llm["data"]
    assert llm["summary"].startswith("Structured 3 of 3 spans with the llm structurer"), data[
        "entries"
    ][0]
    assert [e["headword"] for e in data["entries"]] == ["abacus", "abandon", "abase"]
    assert data["entries"][1]["confidence"] == 0.55 and data["stats"]["low_confidence"] == 1
    assert data["entries"][0]["dmlex"]["senses"][0]["headwordTranslations"] == [
        {"text": "nekaj", "langCode": "sl"}
    ]
    assert data["profile"]["source_language"] == "en" and data["profile"]["target_languages"] == [
        "sl"
    ]
    assert data["stats"]["usage"]["requests"] == 3
    async with pdf_fixture.db.session() as db:
        runs = (
            (await db.execute(sa_select(ConversionRun).order_by(ConversionRun.created_at)))
            .scalars()
            .all()
        )
        assert [r.scope["structurer"] for r in runs] == ["rule", "llm"]
        rows = (
            (await db.execute(sa_select(ConvertedEntry).where(ConvertedEntry.run_id == runs[1].id)))
            .scalars()
            .all()
        )
        assert all(r.annotation_id is not None and r.entry_id is None for r in rows)
        assert rows[1].confidence == 0.55 and rows[1].notes == ["low confidence"]
        usage = (await db.execute(sa_select(UsageRecord))).scalars().all()
        assert [u.purpose for u in usage] == ["structure"] and usage[0].model == "scripted"
