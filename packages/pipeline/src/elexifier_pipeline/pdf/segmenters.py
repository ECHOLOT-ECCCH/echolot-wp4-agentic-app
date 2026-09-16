"""Entry segmentation of page layouts (PLAN 7.7 ``Segmenter``).

``RuleSegmenter`` finds entry starts from typography: a line opens an entry when its first word is
bold, or larger than the body text, or (indent mode) when it sits at the column's left edge while
the entries' continuation lines are indented. Every threshold is a parameter so the agent and the
lexicographer can tune it; the defaults come from the pages' own font statistics.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from elexifier_pipeline.pdf.models import BBox, EntrySpan, Line, PageLayout, SegmentationStats

TRAILING_PUNCTUATION = ",.;:!?)]}»\"'"


class RuleParams(BaseModel):
    """Parameters of the rule segmenter. ``mode`` "auto" picks bold when enough lines start with
    a bold word, else size, else indent."""

    model_config = {"extra": "forbid"}

    mode: str = Field(default="auto", pattern="^(auto|bold|size|indent)$")
    headword_size_delta: float = 0.75  # points above the body size that count as "larger"
    bold_share_min: float = 0.03  # auto: minimum share of bold-start lines to trust bold
    bold_share_max: float = 0.7  # auto: above this bold is body text, not headwords
    indent_tolerance: float = 2.5  # points: a line this close to the column edge is "flush"
    skip_margin_share: float = 0.05  # lines within this share of the page height are heads/feet
    skip_short_margin_lines_only: bool = True  # only skip margin lines with few words
    min_entry_chars: int = 2
    max_headword_words: int = 6
    merge_continuations: bool = True  # column-first lines that are not starts join the previous


@dataclass
class _Candidate:
    line: Line
    start: bool
    reason: str
    confidence: float


def _headword_from(line: Line, mode: str, max_words: int) -> str | None:
    words = line.words
    if not words:
        return None
    if mode == "bold" and words[0].bold:
        picked = []
        for word in words[:max_words]:
            if not word.bold:
                break
            picked.append(word.text)
        text = " ".join(picked)
    elif mode == "size":
        size = words[0].size
        picked = []
        for word in words[:max_words]:
            if abs(word.size - size) > 0.1:
                break
            picked.append(word.text)
        text = " ".join(picked)
    else:
        text = words[0].text
    text = text.strip().rstrip(TRAILING_PUNCTUATION).strip()
    return text or None


class RuleSegmenter:
    name = "rule"

    def __init__(self, params: dict[str, Any] | RuleParams | None = None) -> None:
        self.params = params if isinstance(params, RuleParams) else RuleParams(**(params or {}))

    # -- analysis helpers -----------------------------------------------------------------

    @staticmethod
    def _body_lines(page: PageLayout, params: RuleParams) -> tuple[list[Line], int]:
        """Lines that carry entry text: running heads and page numbers are dropped."""
        margin = page.height * params.skip_margin_share
        kept: list[Line] = []
        skipped = 0
        for line in page.lines:
            in_margin = line.bottom <= margin or line.top >= page.height - margin
            if in_margin and (not params.skip_short_margin_lines_only or len(line.words) <= 8):
                skipped += 1
                continue
            kept.append(line)
        return kept, skipped

    @staticmethod
    def _bold_share(lines: Sequence[Line]) -> float:
        starts = [line for line in lines if line.words]
        if not starts:
            return 0.0
        return sum(1 for line in starts if line.words[0].bold) / len(starts)

    def resolve_mode(self, pages: Sequence[PageLayout]) -> tuple[str, float]:
        params = self.params
        lines = [line for page in pages for line in self._body_lines(page, params)[0]]
        share = self._bold_share(lines)
        if params.mode != "auto":
            return params.mode, share
        if params.bold_share_min <= share <= params.bold_share_max:
            return "bold", share
        body = statistics.median([p.body_size for p in pages if p.body_size]) if pages else None
        larger = [
            line
            for line in lines
            if line.words and body and line.words[0].size >= body + params.headword_size_delta
        ]
        if lines and 0.02 <= len(larger) / len(lines) <= 0.7:
            return "size", share
        return "indent", share

    # -- segmentation ---------------------------------------------------------------------

    def _is_start(self, line: Line, mode: str, page: PageLayout, column_edge: float) -> _Candidate:
        params = self.params
        if not line.words:
            return _Candidate(line, False, "empty", 0.0)
        first = line.words[0]
        flush = abs(line.x0 - column_edge) <= params.indent_tolerance
        if mode == "bold":
            if first.bold:
                return _Candidate(line, True, "bold", 0.95 if flush else 0.8)
            return _Candidate(line, False, "regular", 0.0)
        if mode == "size":
            body = page.body_size or first.size
            if first.size >= body + params.headword_size_delta:
                return _Candidate(line, True, "size", 0.9 if flush else 0.75)
            return _Candidate(line, False, "regular", 0.0)
        # indent mode: flush lines start entries, indented lines continue them
        if flush:
            return _Candidate(line, True, "flush", 0.6)
        return _Candidate(line, False, "indented", 0.0)

    def segment_page(self, page: PageLayout, mode: str) -> tuple[list[EntrySpan], int]:
        params = self.params
        lines, skipped = self._body_lines(page, params)
        edges = page.columns or ([min(line.x0 for line in lines)] if lines else [0.0])
        spans: list[EntrySpan] = []
        current: list[Line] = []
        current_conf = 0.0
        current_head: str | None = None
        current_continuation = False

        def close() -> None:
            nonlocal current, current_conf, current_head, current_continuation
            if not current:
                return
            text = " ".join(line.text for line in current)
            if len(text.strip()) >= params.min_entry_chars:
                spans.append(
                    EntrySpan(
                        id=f"p{page.page}-e{len(spans) + 1}",
                        page=page.page,
                        line_ids=[line.id for line in current],
                        bboxes=_bboxes(current),
                        headword_guess=current_head,
                        confidence=round(current_conf, 2),
                        source="rule",
                        continuation=current_continuation,
                        text=text[:2000],
                    )
                )
            current = []
            current_conf = 0.0
            current_head = None
            current_continuation = False

        previous_column = -1
        for line in lines:
            edge = edges[line.column] if line.column < len(edges) else edges[-1]
            candidate = self._is_start(line, mode, page, edge)
            new_column = line.column != previous_column
            previous_column = line.column
            if candidate.start:
                close()
                current = [line]
                current_conf = candidate.confidence
                current_head = _headword_from(line, mode, params.max_headword_words)
                continue
            if not current:
                # text before the first start on this page or column: a continuation
                current = [line]
                current_conf = 0.4
                current_continuation = True
                continue
            if new_column and not params.merge_continuations:
                close()
                current = [line]
                current_conf = 0.4
                current_continuation = True
                continue
            current.append(line)
        close()
        return spans, skipped

    def segment(
        self, pages: Sequence[PageLayout], params: dict[str, Any] | None = None
    ) -> tuple[list[EntrySpan], SegmentationStats]:
        if params:
            self.params = RuleParams(**{**self.params.model_dump(), **params})
        text_pages = [p for p in pages if not p.is_scanned and p.lines]
        mode, share = self.resolve_mode(text_pages)
        spans: list[EntrySpan] = []
        skipped_total = 0
        for page in text_pages:
            page_spans, skipped = self.segment_page(page, mode)
            spans.extend(page_spans)
            skipped_total += skipped
        stats = SegmentationStats(
            pages=len(text_pages),
            spans=len(spans),
            continuations=sum(1 for s in spans if s.continuation),
            lines=sum(len(p.lines) for p in text_pages),
            lines_skipped=skipped_total,
            bold_start_share=round(share, 3),
            mode=mode,
            confidence_mean=round(statistics.fmean([s.confidence for s in spans]), 3)
            if spans
            else 0.0,
        )
        stats.warnings = self._warnings(stats, text_pages, spans)
        return spans, stats

    @staticmethod
    def _warnings(
        stats: SegmentationStats, pages: Sequence[PageLayout], spans: Sequence[EntrySpan]
    ) -> list[str]:
        warnings: list[str] = []
        if not pages:
            warnings.append("no pages with a text layer")
            return warnings
        per_page = stats.spans / max(1, stats.pages)
        if per_page < 1.5:
            warnings.append(
                f"only {per_page:.1f} entries per page: entry starts are probably not "
                "recognised (try another mode or a smaller headword_size_delta)"
            )
        lines_per_span = stats.lines / max(1, stats.spans)
        if lines_per_span < 1.3 and stats.spans > 20:
            warnings.append(
                f"{lines_per_span:.1f} lines per entry: nearly every line starts an entry, "
                "the start rule is too loose"
            )
        if stats.mode == "indent":
            warnings.append(
                "indent mode: entries are recognised by flush-left lines only; check a page "
                "with multi-line entries"
            )
        if stats.continuations > stats.spans * 0.5 and stats.spans > 10:
            warnings.append("many continuation spans: entry starts are missed at column tops")
        return warnings


def _bboxes(lines: Sequence[Line]) -> list[BBox]:
    boxes: list[BBox] = []
    group: list[Line] = []
    for line in lines:
        if group and line.column != group[-1].column:
            boxes.append(_union(group))
            group = []
        group.append(line)
    if group:
        boxes.append(_union(group))
    return boxes


def _union(lines: Sequence[Line]) -> BBox:
    return (
        min(line.x0 for line in lines),
        min(line.top for line in lines),
        max(line.x1 for line in lines),
        max(line.bottom for line in lines),
    )


__all__ = ["RuleParams", "RuleSegmenter"]
