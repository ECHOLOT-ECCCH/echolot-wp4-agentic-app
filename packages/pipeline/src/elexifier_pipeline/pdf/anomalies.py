"""Oddities in a segmentation, scored without a model (PLAN M2.9).

A rule segmenter is consistent, so its mistakes look alike: a page with far fewer entries than
usual (merged entries), far more (something taken for a headword), a giant span, a headword that
is a number. Scoring these against the document's own statistics gives the lexicographer a short
list of pages and spans to look at instead of the whole book.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from collections import Counter
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

from elexifier_pipeline.pdf.models import EntrySpan, PageLayout, SegmentationStats

DEFAULT_SAMPLE_PAGES = 40
EDGE_PAGES = 2  # pages at the start and at the end always go into the sample


class PageFlag(BaseModel):
    page: int
    reasons: list[str]
    entries: int
    lines: int


class ReviewSummary(BaseModel):
    pages_total: int
    text_pages: int
    entries: int
    continuations: int
    median_entries_per_page: float
    median_span_chars: float
    flagged_spans: int
    flagged_pages: list[PageFlag]
    sample_pages: list[int]  # what to review: edges, letter changes, flagged pages
    reasons: dict[str, int] = Field(default_factory=dict)
    letter_pages: list[int] = Field(default_factory=list)

    def describe(self) -> str:
        return (
            f"{self.entries:,} entries on {self.text_pages:,} text pages, median "
            f"{self.median_entries_per_page:g} per page; {self.flagged_spans:,} flagged spans on "
            f"{len(self.flagged_pages)} pages; review sample of {len(self.sample_pages)} pages."
        )


_NUMERIC = re.compile(r"^[\d\W_]+$")


def _first_letter(headword: str | None) -> str | None:
    if not headword:
        return None
    for char in headword:
        if char.isalpha():
            base = unicodedata.normalize("NFD", char)[0].lower()
            return base
    return None


def _percentile(values: list[float], share: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
    return ordered[index]


def _spread(pages: Sequence[int], n: int) -> list[int]:
    """``n`` pages evenly spaced through ``pages`` (all of them when there are fewer)."""
    if n <= 0 or not pages:
        return []
    if len(pages) <= n:
        return list(pages)
    step = len(pages) / n
    return [pages[min(len(pages) - 1, int(i * step + step / 2))] for i in range(n)]


def _quotas(groups: dict[str, list[int]], room: int) -> dict[str, int]:
    """Share ``room`` between the groups: equal shares, leftovers to the groups with pages."""
    quotas = {kind: 0 for kind in groups}
    left = max(0, room)
    while left > 0:
        open_kinds = [k for k, pages in groups.items() if quotas[k] < len(pages)]
        if not open_kinds:
            break
        share = max(1, left // len(open_kinds))
        for kind in open_kinds:
            add = min(share, len(groups[kind]) - quotas[kind], left)
            quotas[kind] += add
            left -= add
            if left == 0:
                break
    return quotas


def score_review(
    spans: Sequence[EntrySpan],
    pages: Sequence[PageLayout],
    stats: SegmentationStats | None = None,
    *,
    headword_font: str | None = None,
    max_sample: int = DEFAULT_SAMPLE_PAGES,
) -> tuple[dict[str, list[str]], ReviewSummary]:
    """Flags per span id and the document-level review summary."""
    text_pages = [p for p in pages if not p.is_scanned and p.lines]
    page_numbers = sorted(p.page for p in pages)
    lines_by_page = {p.page: len(p.lines) for p in text_pages}
    entries = [s for s in spans if not s.continuation]
    per_page: Counter[int] = Counter(s.page for s in entries)
    entry_counts = [per_page.get(p.page, 0) for p in text_pages]
    median_entries = statistics.median(entry_counts) if entry_counts else 0.0
    lengths = [len(s.text) for s in entries]
    median_chars = statistics.median(lengths) if lengths else 0.0
    p99_chars = _percentile([float(v) for v in lengths], 0.99)
    line_counts = [len(s.line_ids) for s in entries]
    p99_lines = _percentile([float(v) for v in line_counts], 0.99)

    flags: dict[str, list[str]] = {}
    reasons: Counter[str] = Counter()

    def flag(span: EntrySpan, reason: str) -> None:
        flags.setdefault(span.id, []).append(reason)
        reasons[reason] += 1

    first_span_on_page: dict[int, EntrySpan] = {}
    for span in spans:
        first_span_on_page.setdefault(span.page, span)
    for span in spans:
        if span.continuation:
            if first_span_on_page.get(span.page) is not span:
                flag(span, "continuation_mid_page")
            continue
        text_len = len(span.text)
        if lengths and text_len > p99_chars and text_len > 3 * median_chars:
            flag(span, "very_long")
        if line_counts and len(span.line_ids) > p99_lines and len(span.line_ids) > 3:
            flag(span, "many_lines")
        if text_len < 8:
            flag(span, "very_short")
        head = (span.headword_guess or "").strip()
        if not head:
            flag(span, "no_headword")
        elif _NUMERIC.match(head):
            flag(span, "numeric_headword")
        elif len(head.split()) > 4:
            flag(span, "long_headword")
        if span.confidence < 0.7:
            flag(span, "low_confidence")

    page_flags: list[PageFlag] = []
    for page in text_pages:
        n = per_page.get(page.page, 0)
        page_reasons: list[str] = []
        lines = lines_by_page.get(page.page, 0)
        if n == 0:
            page_reasons.append("no_entries")
        elif (
            median_entries
            and n < 0.4 * median_entries
            and lines >= 0.5 * statistics.median(list(lines_by_page.values()) or [0])
        ):
            page_reasons.append("few_entries")
        elif median_entries and n > 2.5 * median_entries:
            page_reasons.append("many_entries")
        span_reasons = {r for s in spans if s.page == page.page for r in flags.get(s.id, [])}
        if span_reasons:
            page_reasons.append("flagged_spans")
        if page_reasons:
            for reason in page_reasons:
                if reason != "flagged_spans":
                    reasons[reason] += 1
            page_flags.append(
                PageFlag(page=page.page, reasons=page_reasons, entries=n, lines=lines)
            )

    # letter changes in the headword sequence (one page per first letter)
    letter_pages: list[int] = []
    current_letter: str | None = None
    letters = [_first_letter(s.headword_guess) for s in entries]
    window = 5
    for index, span in enumerate(entries):
        letter = letters[index]
        if not letter or letter == current_letter:
            continue
        # a new letter counts only when the next few entries agree: abbreviations such as
        # "f.c" among the a-words must not look like a new letter
        ahead = [x for x in letters[index : index + window] if x]
        agreed = sum(1 for x in ahead if x == letter) * 2 > len(ahead)
        if len(ahead) < 3 or agreed:  # the tail of the book, or the next entries agree
            current_letter = letter
            if span.page not in letter_pages:
                letter_pages.append(span.page)

    # the sample: the edges of the book, then the letter changes and every kind of oddity,
    # each spread through the book, so that a 1,000-page dictionary is checked in 40 pages
    sample: list[int] = []

    def take(page: int) -> None:
        if page not in sample and page in lines_by_page and len(sample) < max_sample:
            sample.append(page)

    for page in page_numbers[:EDGE_PAGES] + page_numbers[-EDGE_PAGES:]:
        take(page)
    letter_room = max(0, (max_sample - len(sample)) // 3)
    for page in _spread([p for p in letter_pages if p not in sample], letter_room):
        take(page)
    by_reason: dict[str, list[int]] = {}
    for entry in page_flags:
        if entry.page in sample:
            continue
        kind = next((r for r in entry.reasons if r != "flagged_spans"), "flagged_spans")
        by_reason.setdefault(kind, []).append(entry.page)
    for kind, kind_pages in by_reason.items():
        by_reason[kind] = sorted(set(kind_pages))
    for kind, quota in _quotas(by_reason, max_sample - len(sample)).items():
        for page in _spread(by_reason[kind], quota):
            take(page)
    for page in letter_pages:  # room left: the remaining letter changes
        take(page)
    summary = ReviewSummary(
        pages_total=len(pages),
        text_pages=len(text_pages),
        entries=len(entries),
        continuations=len(spans) - len(entries),
        median_entries_per_page=float(median_entries),
        median_span_chars=float(median_chars),
        flagged_spans=len(flags),
        flagged_pages=page_flags,
        sample_pages=sorted(sample),
        reasons=dict(reasons),
        letter_pages=letter_pages[:60],
    )
    return flags, summary


def flags_for_summary(flags: dict[str, list[str]]) -> dict[str, Any]:
    return {span_id: list(reasons) for span_id, reasons in flags.items()}


__all__ = ["DEFAULT_SAMPLE_PAGES", "PageFlag", "ReviewSummary", "score_review"]
