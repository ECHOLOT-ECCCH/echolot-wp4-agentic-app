from __future__ import annotations

from pathlib import Path

import pytest

from elexifier_pipeline import registry
from elexifier_pipeline.pdf.layout import PdfplumberLayout
from elexifier_pipeline.pdf.learned import LearnedSegmenter
from elexifier_pipeline.pdf.models import EntrySpan
from elexifier_pipeline.pdf.segmenters import RuleSegmenter
from elexifier_pipeline.sample import COLUMNS_PDF


def _starts(spans: list[EntrySpan], page: int) -> set[str]:
    return {s.line_ids[0] for s in spans if s.page == page and not s.continuation}


def _f1(predicted: set[str], expected: set[str]) -> float:
    tp = len(predicted & expected)
    precision = tp / len(predicted) if predicted else 0.0
    recall = tp / len(expected) if expected else 0.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def test_learned_segmenter_reproduces_the_third_page(tmp_path: Path) -> None:
    pages = list(PdfplumberLayout().extract(COLUMNS_PDF))
    rule_spans, _ = RuleSegmenter().segment(pages)
    gold = [s for s in rule_spans if s.page in (1, 2)]

    segmenter = LearnedSegmenter()
    report = segmenter.train([p for p in pages if p.page in (1, 2)], gold)
    assert report.pages == 2 and report.positives == 102 and report.held_out_pages == 0
    assert report.f1 >= 0.95 and report.features > 10
    assert report.warnings and "training pages themselves" in report.warnings[0]

    spans, stats = segmenter.segment(pages)
    assert stats.mode == "learned" and stats.pages == 3 and all(s.source == "ml" for s in spans)
    assert _f1(_starts(spans, 3), _starts(rule_spans, 3)) >= 0.95
    learned_heads = [s.headword_guess for s in spans if s.page == 3][:3]
    rule_heads = [s.headword_guess for s in rule_spans if s.page == 3][:3]
    assert learned_heads == rule_heads
    assert 0.5 < stats.confidence_mean <= 1.0

    # save and load give the same segmentation; an untrained model refuses to segment
    path = tmp_path / "learned.json"
    segmenter.save(path)
    loaded = LearnedSegmenter()
    loaded.load(path)
    again, _ = loaded.segment(pages)
    assert [s.line_ids for s in again] == [s.line_ids for s in spans]
    assert loaded.report is not None and loaded.report.f1 == report.f1
    with pytest.raises(ValueError, match="no weights"):
        LearnedSegmenter().segment(pages)
    assert "learned" in registry.available("segmenter")
    via_registry = registry.get_segmenter("learned", {"weights": segmenter.weights})
    assert [s.line_ids for s in via_registry.segment(pages)[0]] == [s.line_ids for s in spans]


def test_learned_segmenter_holds_pages_out_and_ignores_undecided_lines() -> None:
    pages = list(PdfplumberLayout().extract(COLUMNS_PDF))
    rule_spans, _ = RuleSegmenter().segment(pages)
    # pretend the fixture has six pages: the three text pages twice, with fresh line ids
    doubled = []
    gold: list[EntrySpan] = []
    for shift in (0, 3):
        prefix = f"s{shift}-"
        for page in pages[:3]:
            doubled.append(
                page.model_copy(
                    update={
                        "page": page.page + shift,
                        "lines": [
                            line.model_copy(update={"id": prefix + line.id}) for line in page.lines
                        ],
                    }
                )
            )
            gold.extend(
                s.model_copy(
                    update={"page": s.page + shift, "line_ids": [prefix + i for i in s.line_ids]}
                )
                for s in rule_spans
                if s.page == page.page
            )
    report = LearnedSegmenter().train(doubled, gold)
    assert report.pages == 6 and report.held_out_pages == 1 and report.f1 >= 0.95
    assert not report.warnings

    # undecided proposals are neither positives nor negatives
    undecided = {s.line_ids[0] for s in rule_spans if s.page == 2}
    partial = LearnedSegmenter().train(
        pages[:2], [s for s in rule_spans if s.page in (1, 2)], ignore=undecided
    )
    assert partial.positives == 52 and partial.pages == 2
