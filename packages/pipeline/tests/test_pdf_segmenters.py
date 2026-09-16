from __future__ import annotations

import pytest

from elexifier_pipeline import registry
from elexifier_pipeline.pdf.layout import PdfplumberLayout
from elexifier_pipeline.pdf.models import EntrySpan
from elexifier_pipeline.pdf.segmenters import RuleParams, RuleSegmenter
from elexifier_pipeline.sample import COLUMNS_PDF, SAMPLE_PDF


@pytest.fixture(scope="module")
def column_pages():
    return list(PdfplumberLayout().extract(COLUMNS_PDF))


def test_bold_headwords_give_one_span_per_entry(column_pages) -> None:
    spans, stats = RuleSegmenter().segment(column_pages)
    assert stats.mode == "bold" and stats.spans == 130 and stats.continuations == 0
    assert stats.pages == 3 and stats.lines_skipped == 3  # running heads
    assert stats.warnings == []
    assert [s.headword_guess for s in spans[:4]] == ["abacus", "abandon", "abase", "abbey"]
    first = spans[0]
    assert first.id == "p1-e1" and first.page == 1 and first.line_ids == ["p1-l2", "p1-l3"]
    assert len(first.bboxes) == 1 and first.bbox[0] < 60 and first.confidence >= 0.9
    assert first.text.startswith("abacus n. a frame with beads")
    assert all(isinstance(s, EntrySpan) and s.source == "rule" for s in spans)
    # spans stay in reading order: page, column, top
    keys = [(s.page, s.bbox[0] > 300, s.bbox[1]) for s in spans]
    assert keys == sorted(keys)


def test_wrong_mode_is_visible_as_merged_spans(column_pages) -> None:
    spans, stats = RuleSegmenter({"mode": "size"}).segment(column_pages)
    assert stats.spans == 3  # one continuation blob per page
    assert all(s.continuation for s in spans)
    assert any("entry starts are probably not recognised" in w for w in stats.warnings)


def test_indent_mode_and_params_validation(column_pages) -> None:
    spans, stats = RuleSegmenter({"mode": "indent"}).segment(column_pages)
    assert stats.spans == 130 and stats.mode == "indent"
    assert spans[0].confidence < 0.7  # weaker evidence than typography
    with pytest.raises(ValueError):
        RuleParams(mode="magic")
    with pytest.raises(ValueError):
        RuleSegmenter({"unknown": 1})
    # params passed at segment time override the constructor's
    segmenter = RuleSegmenter()
    _, stats2 = segmenter.segment(column_pages, params={"mode": "size"})
    assert stats2.mode == "size" and segmenter.params.mode == "size"


def test_sample_pdf_without_bold_falls_back_to_indent() -> None:
    pages = list(PdfplumberLayout().extract(SAMPLE_PDF))
    spans, stats = RuleSegmenter().segment(pages)
    assert stats.mode == "indent"
    assert [s.headword_guess for s in spans] == ["abacus", "abandon", "abbey", "beacon", "bead"]


def test_registry_lists_and_builds_segmenters() -> None:
    assert "rule" in registry.available("segmenter")
    segmenter = registry.get_segmenter("rule", {"mode": "bold"})
    assert isinstance(segmenter, RuleSegmenter) and segmenter.params.mode == "bold"
    with pytest.raises(KeyError):
        registry.get_segmenter("nope")
