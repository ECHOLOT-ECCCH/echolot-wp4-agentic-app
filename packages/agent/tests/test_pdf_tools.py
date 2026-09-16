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
