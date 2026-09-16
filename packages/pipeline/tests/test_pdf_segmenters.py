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


def test_headword_font_gives_one_span_per_entry(column_pages) -> None:
    spans, stats = RuleSegmenter().segment(column_pages)
    # auto mode finds the headword font (bold Helvetica only at line starts)
    assert stats.mode == "font" and stats.headword_font == "Helvetica-Bold"
    assert stats.spans == 130 and stats.continuations == 0
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


def test_bold_indent_and_font_modes(column_pages) -> None:
    spans, stats = RuleSegmenter({"mode": "bold"}).segment(column_pages)
    assert stats.spans == 130 and stats.mode == "bold"
    spans, stats = RuleSegmenter({"mode": "indent"}).segment(column_pages)
    assert stats.spans == 130 and stats.mode == "indent"
    assert spans[0].confidence < 0.7  # weaker evidence than typography
    spans, stats = RuleSegmenter({"mode": "font", "headword_font": "Helvetica-Bold"}).segment(
        column_pages
    )
    assert stats.spans == 130 and spans[0].headword_guess == "abacus"
    # an unknown font finds no entries at all: one continuation span per page and a warning
    spans, stats = RuleSegmenter({"mode": "font", "headword_font": "Courier"}).segment(
        column_pages[:1]
    )
    assert stats.spans == 1 and spans[0].continuation and stats.warnings
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


def test_review_scoring_flags_oddities_and_builds_a_sample(column_pages) -> None:
    from elexifier_pipeline.pdf.anomalies import score_review
    from elexifier_pipeline.pdf.models import EntrySpan

    spans, stats = RuleSegmenter().segment(column_pages)
    flags, summary = score_review(spans, column_pages, stats)
    assert summary.entries == 130 and summary.text_pages == 3
    assert summary.median_entries_per_page > 0 and summary.sample_pages == [1, 2, 3]
    assert summary.flagged_spans == 0 and summary.flagged_pages == []
    # a synthetic giant span with a numeric headword and a mid-page continuation get flagged
    giant = EntrySpan(
        id="p1-x1",
        page=1,
        line_ids=[f"p1-l{i}" for i in range(1, 40)],
        bboxes=[(0, 0, 10, 10)],
        headword_guess="12345",
        confidence=0.5,
        text="x" * 5000,
    )
    stray = EntrySpan(
        id="p2-x1",
        page=2,
        line_ids=["p2-l9"],
        bboxes=[(0, 0, 10, 10)],
        headword_guess=None,
        confidence=0.4,
        continuation=True,
        text="tail",
    )
    flags, summary = score_review([*spans, giant, stray], column_pages, stats)
    assert set(flags["p1-x1"]) >= {"very_long", "many_lines", "numeric_headword", "low_confidence"}
    assert flags["p2-x1"] == ["continuation_mid_page"]
    assert summary.flagged_spans == 2 and [f.page for f in summary.flagged_pages] == [1, 2]
    assert summary.reasons["very_long"] == 1


def test_letter_pages_only_step_forward() -> None:
    from elexifier_pipeline.pdf.anomalies import score_review
    from elexifier_pipeline.pdf.models import EntrySpan, Line, PageLayout, Word

    def page(n: int) -> PageLayout:
        word = Word(text="x", x0=0, top=0, x1=5, bottom=5, font="F", size=9)
        line = Line(id=f"p{n}-l1", words=[word], x0=0, top=0, x1=5, bottom=5)
        return PageLayout(page=n, width=100, height=100, lines=[line], text_chars=50)

    pages = [page(1), page(2), page(3)]
    heads = [
        ("abacus", 1),
        ("f.c", 1),
        ("academy", 1),
        ("acid", 1),
        ("bank", 2),
        ("bench", 2),
        ("bill", 2),
        ("Čas", 3),
        ("čip", 3),
        ("čist", 3),
    ]
    spans = [
        EntrySpan(
            id=f"s{i}", page=p, line_ids=[], bboxes=[(0, 0, 1, 1)], headword_guess=h, text="t" * 20
        )
        for i, (h, p) in enumerate(heads)
    ]
    _flags, summary = score_review(spans, pages)
    assert summary.letter_pages == [1, 2, 3]  # a, b, č: "f.c" among the a-words is ignored


def test_review_sample_spreads_over_reasons_letters_and_the_book() -> None:
    from elexifier_pipeline.pdf.anomalies import score_review
    from elexifier_pipeline.pdf.models import EntrySpan, Line, PageLayout, Word

    def page(n: int) -> PageLayout:
        word = Word(text="x", x0=0, top=0, x1=5, bottom=5, font="F", size=9)
        lines = [
            Line(id=f"p{n}-l{i}", words=[word], x0=0, top=i, x1=5, bottom=i + 5) for i in range(40)
        ]
        return PageLayout(page=n, width=100, height=100, lines=lines, text_chars=500)

    # 300 pages, normally 6 entries each; a letter every 12 pages; three kinds of odd pages
    pages = [page(n) for n in range(1, 301)]
    spans: list[EntrySpan] = []
    for n in range(1, 301):
        count = 6
        if n % 7 == 0:
            count = 1  # few entries
        elif n % 11 == 0:
            count = 0  # no entries (a continuation page)
        elif n % 13 == 0:
            count = 20  # many entries
        letter = chr(ord("a") + (n - 1) // 12)
        for i in range(count):
            spans.append(
                EntrySpan(
                    id=f"s{n}-{i}",
                    page=n,
                    line_ids=[],
                    bboxes=[(0, 0, 1, 1)],
                    headword_guess=f"{letter}word{i}",
                    text="t" * 120,
                )
            )
    _flags, summary = score_review(spans, pages, max_sample=40)
    sample = summary.sample_pages
    assert len(sample) == 40 and {1, 2, 299, 300} <= set(sample)
    assert len(set(sample) & set(summary.letter_pages)) >= 10  # a share for the letter changes
    kinds = {
        reason
        for flag in summary.flagged_pages
        if flag.page in sample
        for reason in flag.reasons
        if reason != "flagged_spans"
    }
    assert kinds == {"few_entries", "no_entries", "many_entries"}  # every kind of oddity
    assert sum(1 for p in sample if p > 200) >= 8  # the end of the book too, not just the start
