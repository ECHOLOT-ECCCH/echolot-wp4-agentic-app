from __future__ import annotations

from sqlalchemy import func, select

from elexifier_agent.agent import render_system_prompt
from elexifier_core.db.models import Annotation, Document

from .conftest import Fixture
from .test_xml_tools import call_tool


def test_pdf_prompt_section(pdf_fixture: Fixture) -> None:
    prompt = render_system_prompt(pdf_fixture.deps)
    assert "pdf_segment" in prompt and "xml_test_xpath" not in prompt
    assert "spec_update" in prompt  # the specification is kept for PDF dictionaries too


async def test_spec_tools_work_in_pdf_projects(pdf_fixture: Fixture) -> None:
    empty = await call_tool(pdf_fixture, "spec_read", {})
    assert empty["summary"].startswith("The conversion spec is empty")
    saved = await call_tool(
        pdf_fixture, "spec_update", {"markdown": "# Šega\n- headwords in Arial Bold 8 pt"}
    )
    assert saved["summary"] == "Conversion spec updated to version 1"
    again = await call_tool(pdf_fixture, "spec_read", {})
    assert again["data"]["version"] == 1 and "Arial Bold" in again["data"]["markdown"]


async def test_pdf_analyze_and_segment(pdf_fixture: Fixture) -> None:
    analysed = await call_tool(pdf_fixture, "pdf_analyze", {})
    data = analysed["data"]
    assert analysed["summary"].startswith("4 pages (3 with text)")
    assert data["columns"] == {"2": 3} and data["body_size"] == 9.5
    assert data["bold_start_share"] > 0.3 and data["segmentation"] is None
    assert data["headword_font"] == {"font": "Helvetica-Bold", "size": 9.5}
    assert data["line_start_fonts"][0]["font"] == "Helvetica-Bold"
    assert "Headword font: Helvetica-Bold" in analysed["summary"]
    assert data["fonts"][0]["font"] == "Helvetica" and data["sample_lines"][1].startswith("abacus")
    assert data["warnings"] and "no text layer" in data["warnings"][0]

    segmented = await call_tool(pdf_fixture, "pdf_segment", {})
    assert segmented["summary"].startswith("130 entry spans on 3 pages with the rule segmenter")
    sdata = segmented["data"]
    assert sdata["stats"]["mode"] == "font" and sdata["first_headwords"][:3] == [
        "abacus",
        "abandon",
        "abase",
    ]
    assert sdata["spans_per_page"] == {"1": 52, "2": 50, "3": 28}
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
    assert whole["summary"].startswith("89 line(s) in page 1")
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
                        "segmentation": "merged" if headword == "abase" else "ok",
                        "segmentation_reason": "the entry 'abbey' starts inside this span"
                        if headword == "abase"
                        else None,
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
    # the structurer's doubt about the third span: in the summary, the stats, the entry and
    # the span's flags (M2.10)
    assert "segmentation doubts: 1 merged" in llm["summary"]
    assert data["stats"]["segmentation"] == {"merged": 1}
    assert data["entries"][2]["segmentation"] == "merged"
    assert "abbey" in data["entries"][2]["segmentation_reason"]
    assert (
        data["entries"][0]["segmentation"] == "ok"
        and "segmentation_reason" not in data["entries"][0]
    )
    async with pdf_fixture.db.session() as db:
        runs = (
            (await db.execute(sa_select(ConversionRun).order_by(ConversionRun.created_at)))
            .scalars()
            .all()
        )
        assert [r.scope["structurer"] for r in runs] == ["rule", "llm"]
        rows = (
            (
                await db.execute(
                    sa_select(ConvertedEntry)
                    .where(ConvertedEntry.run_id == runs[1].id)
                    .order_by(ConvertedEntry.position)
                )
            )
            .scalars()
            .all()
        )
        assert all(r.annotation_id is not None and r.entry_id is None for r in rows)
        assert rows[1].confidence == 0.55 and rows[1].notes == ["low confidence"]
        doubt = [i for i in rows[2].issues if i["code"] == "segmentation"]
        assert doubt[0]["verdict"] == "merged" and rows[2].issue_count == 1
        assert doubt[0]["message"].startswith("merged: the entry 'abbey'")
        flagged = await db.get(Annotation, rows[2].annotation_id)
        assert flagged is not None and flagged.payload["flags"] == ["merged"]
        assert flagged.payload["structurer"]["run_id"] == str(runs[1].id)
        clean = await db.get(Annotation, rows[0].annotation_id)
        assert clean is not None and "flags" not in clean.payload
        usage = (await db.execute(sa_select(UsageRecord))).scalars().all()
        assert [u.purpose for u in usage] == ["structure"] and usage[0].model == "scripted"
        run_id = str(runs[1].id)
    stats = await call_tool(pdf_fixture, "run_stats", {"run_id": run_id})
    assert "segmentation doubts: 1 merged" in stats["summary"]
    assert stats["data"]["stats"]["segmentation"] == {"merged": 1}


async def test_page_continuations_are_joined_to_their_entry(pdf_fixture: Fixture) -> None:
    """A synthetic continuation: the first span of page 2 is marked as continuing the last span
    of page 1; structuring sees one text and skips the continuation itself."""
    from sqlalchemy import select as sa_select

    from elexifier_agent.structuring import full_texts, usable_spans
    from elexifier_core.db.models import Annotation, Document

    await call_tool(pdf_fixture, "pdf_segment", {})
    async with pdf_fixture.db.session() as db:
        document = await db.get(Document, pdf_fixture.document_id)
        assert document is not None
        rows = (
            (
                await db.execute(
                    sa_select(Annotation).order_by(Annotation.page_no, Annotation.position)
                )
            )
            .scalars()
            .all()
        )
        last_p1 = [r for r in rows if r.page_no == 1][-1]
        first_p2 = next(r for r in rows if r.page_no == 2)
        first_p2.payload = {
            **first_p2.payload,
            "continuation": True,
            "continues": str(last_p1.id),
            "text": "tail of the entry",
        }
        last_p1.payload = {**last_p1.payload, "continued_by": str(first_p2.id)}
        await db.commit()
        spans = await usable_spans(db, document)
        assert first_p2.id not in {s.id for s in spans} and len(spans) == 129
        texts = await full_texts(db, spans)
        assert texts[last_p1.id].endswith("tail of the entry")
        # a rejected continuation is not joined
        first_p2.status = "rejected"
        await db.commit()
        texts = await full_texts(db, spans)
        assert not texts[last_p1.id].endswith("tail of the entry")


async def test_pdf_review_summary_proposes_a_sample(pdf_fixture: Fixture) -> None:
    early = await call_tool(pdf_fixture, "pdf_review_summary", {})
    assert early["summary"].startswith("Error: no segmentation yet")

    await call_tool(pdf_fixture, "pdf_segment", {})
    review = await call_tool(pdf_fixture, "pdf_review_summary", {})
    assert review["summary"].startswith("130 entries on 3 pages (median 50 per page)")
    assert "Proposals still undecided: 130" in review["summary"]
    data = review["data"]
    assert data["sample_pages"] == [1, 2, 3] and data["counts"] == {"proposed": 130}
    assert data["letter_pages"] == [1] and data["reasons"] == {}
    async with pdf_fixture.db.session() as db:
        document = await db.get(Document, pdf_fixture.document_id)
        assert document is not None
        stored = document.stats["review"]
        assert stored["flagged_spans"] == 0 and stored["median_span_chars"] > 40


async def test_pdf_train_segmenter_gives_a_second_opinion_and_applies(pdf_fixture: Fixture) -> None:
    from sqlalchemy import update

    early = await call_tool(pdf_fixture, "pdf_train_segmenter", {})
    assert early["summary"].startswith("Error: no reviewed spans yet")

    await call_tool(pdf_fixture, "pdf_segment", {})
    async with pdf_fixture.db.session() as db:  # the lexicographer accepts pages 1 and 2
        await db.execute(
            update(Annotation).where(Annotation.page_no.in_([1, 2])).values(status="accepted")
        )
        await db.commit()
    trained = await call_tool(pdf_fixture, "pdf_train_segmenter", {})
    data = trained["data"]
    assert trained["summary"].startswith("Trained on 2 reviewed pages (102 entry starts")
    assert data["report"]["f1"] >= 0.95 and data["unreviewed_pages"] == 1
    assert data["disagreements"] == 0 and data["pages_flagged"] == []
    async with pdf_fixture.db.session() as db:
        document = await db.get(Document, pdf_fixture.document_id)
        assert document is not None
        info = document.stats["learned_segmenter"]
        assert info["reviewed_pages"] == [1, 2] and info["report"]["positives"] == 102
        stored = b"".join([c async for c in pdf_fixture.storage.open(info["key"])])
        assert b'"weights"' in stored

    # applied: page 3 gets the learned spans, pages 1-2 keep their accepted rule spans
    applied = await call_tool(pdf_fixture, "pdf_train_segmenter", {"apply": True})
    assert applied["data"]["applied_pages"] == 1 and applied["data"]["applied_spans"] == 28
    assert "28 learned spans replaced the proposals on 1 pages" in applied["summary"]
    async with pdf_fixture.db.session() as db:
        rows = (await db.execute(select(Annotation).order_by(Annotation.position))).scalars().all()
        page3 = [r for r in rows if r.page_no == 3]
        assert len(page3) == 28 and all(r.source == "ml" and r.status == "proposed" for r in page3)
        assert all(r.payload["learned"] == {"kind": "applied"} for r in page3)
        assert all(r.status == "accepted" and r.source == "rule" for r in rows if r.page_no < 3)
        assert page3[0].payload["headword_guess"] == "abase102"

    # the learned segmenter runs through pdf_segment too
    again = await call_tool(pdf_fixture, "pdf_segment", {"segmenter": "learned"})
    assert again["summary"].startswith("130 entry spans on 3 pages with the learned segmenter")
    assert again["data"]["stats"]["mode"] == "learned"
