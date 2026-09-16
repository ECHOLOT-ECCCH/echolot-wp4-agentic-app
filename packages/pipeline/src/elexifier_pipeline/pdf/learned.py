"""A segmenter learned from the lexicographer's decisions (PLAN 7.7 ``TrainableSegmenter``).

Every body line is classified as the start of an entry or not by an averaged perceptron over
typographic features (the first word's font, weight, size and position, what the previous line
looked like, where the line sits). Trained on the pages the lexicographer reviewed, it gives a
second opinion on the pages nobody looked at: where it disagrees with the rule proposals, a
human should look. It never invents boxes on its own; the spans it builds follow the same
assembly as the rule segmenter.
"""

from __future__ import annotations

import json
import random
import statistics
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from elexifier_pipeline.pdf.models import EntrySpan, Line, PageLayout, SegmentationStats, Word
from elexifier_pipeline.pdf.segmenters import (
    TRAILING_PUNCTUATION,
    RuleParams,
    RuleSegmenter,
    _bboxes,
    font_family,
    is_symbol_font,
)

HELD_OUT_EVERY = 4  # with at least this many reviewed pages, every fourth one is held out
MAX_SCORE = 6.0


class TrainReport(BaseModel):
    pages: int  # reviewed pages used
    lines: int
    positives: int
    held_out_pages: int  # 0: the numbers are on the training pages themselves
    precision: float
    recall: float
    f1: float
    epochs: int
    features: int
    warnings: list[str] = Field(default_factory=list)


class LearnedParams(BaseModel):
    """Everything but the weights (which are stored next to them, not in run stats)."""

    model_config = {"extra": "forbid"}

    epochs: int = 12
    seed: int = 7
    indent_tolerance: float = 2.5
    skip_margin_share: float = 0.05
    skip_short_margin_lines_only: bool = True
    min_entry_chars: int = 2
    max_headword_words: int = 6


def _bucket(value: float, edges: Sequence[float]) -> str:
    for index, edge in enumerate(edges):
        if value < edge:
            return str(index)
    return str(len(edges))


def _first_char_class(text: str) -> str:
    char = text[:1]
    if not char:
        return "none"
    if char.isupper():
        return "upper"
    if char.islower():
        return "lower"
    if char.isdigit():
        return "digit"
    return "symbol"


def _end_class(prev: Line | None) -> str:
    if prev is None or not prev.words:
        return "none"
    last = prev.words[-1].text.rstrip()[-1:]
    if last in ".!?":
        return "period"
    if last == ";":
        return "semi"
    if last == ",":
        return "comma"
    if last == ":":
        return "colon"
    if last.isalnum():
        return "word"
    return "other"


def line_features(
    line: Line,
    prev: Line | None,
    page: PageLayout,
    edge: float,
    index: int,
    count: int,
    tolerance: float,
) -> list[str]:
    """The feature strings of one line; conjunctions let the linear model express rules such as
    "bold and flush left" or "the previous line ended with a period"."""
    first: Word = line.words[0]
    family = font_family(first.font)
    offset = line.x0 - edge
    flush = abs(offset) <= tolerance
    indent = "flush" if flush else ("neg" if offset < 0 else "in" + _bucket(offset, (8, 20, 40)))
    body = page.body_size
    size_rel = (
        "body"
        if body is None or abs(first.size - body) <= 0.6
        else ("bigger" if first.size > body else "smaller")
    )
    height = max(line.bottom - line.top, 1.0)
    if prev is None or prev.column != line.column:
        gap = "colstart"
        fill = "colstart"
    else:
        gap = "gap" + _bucket((line.top - prev.bottom) / height, (0.35, 1.0))
        fill = "fill" + _bucket((prev.x1 - edge) / max(line.x1 - edge, 1.0), (0.6, 0.9))
    prev_end = _end_class(prev)
    prev_same_font = (
        prev is not None and bool(prev.words) and font_family(prev.words[0].font) == family
    )
    first_class = _first_char_class(first.text)
    position = _bucket(index / max(count, 1), (0.34, 0.67))
    features = [
        "bias",
        f"font={family}",
        f"bold={int(first.bold)}",
        f"italic={int(first.italic)}",
        f"size={round(first.size * 2) / 2}",
        f"size_rel={size_rel}",
        f"indent={indent}",
        f"first={first_class}",
        f"prev_end={prev_end}",
        f"prev={fill}",
        f"prev={gap}",
        f"pos={position}",
        f"prev_same_font={int(prev_same_font)}",
        f"symbol={int(is_symbol_font(first.font))}",
        f"words={_bucket(len(line.words), (2, 5, 10))}",
        f"font={family}&flush={int(flush)}",
        f"bold={int(first.bold)}&flush={int(flush)}",
        f"prev_end={prev_end}&flush={int(flush)}",
        f"first={first_class}&font={family}",
        f"size_rel={size_rel}&flush={int(flush)}",
        f"prev={fill}&flush={int(flush)}",
        f"prev_same_font={int(prev_same_font)}&bold={int(first.bold)}",
    ]
    return features


def _headword_same_font(line: Line, max_words: int) -> str | None:
    first = line.words[0]
    picked = []
    for word in line.words[:max_words]:
        if font_family(word.font) != font_family(first.font) or word.bold != first.bold:
            break
        picked.append(word.text)
    text = " ".join(picked).strip().rstrip(TRAILING_PUNCTUATION).strip()
    return text or None


class LearnedSegmenter:
    """``TrainableSegmenter``: train on gold spans, segment, save and load."""

    name = "learned"

    def __init__(self, params: dict[str, Any] | LearnedParams | None = None) -> None:
        raw = dict(params) if isinstance(params, dict) else {}
        weights = raw.pop("weights", None)
        self.params = params if isinstance(params, LearnedParams) else LearnedParams(**raw)
        self.weights: dict[str, float] = dict(weights or {})
        self.report: TrainReport | None = None

    # -- features and scoring ----------------------------------------------------------------

    def _rule_params(self) -> RuleParams:
        p = self.params
        return RuleParams(
            indent_tolerance=p.indent_tolerance,
            skip_margin_share=p.skip_margin_share,
            skip_short_margin_lines_only=p.skip_short_margin_lines_only,
            min_entry_chars=p.min_entry_chars,
            max_headword_words=p.max_headword_words,
        )

    def page_examples(self, page: PageLayout) -> list[tuple[Line, list[str]]]:
        """Body lines of a page with their features, in reading order."""
        lines, _skipped = RuleSegmenter._body_lines(page, self._rule_params())
        lines = [line for line in lines if line.words]
        edges = page.columns or ([min(line.x0 for line in lines)] if lines else [0.0])
        out: list[tuple[Line, list[str]]] = []
        prev: Line | None = None
        for index, line in enumerate(lines):
            edge = edges[line.column] if line.column < len(edges) else edges[-1]
            out.append(
                (
                    line,
                    line_features(
                        line, prev, page, edge, index, len(lines), self.params.indent_tolerance
                    ),
                )
            )
            prev = line
        return out

    def score(self, features: Sequence[str]) -> float:
        weights = self.weights
        return sum(weights.get(f, 0.0) for f in features)

    # -- training --------------------------------------------------------------------------

    def train(
        self,
        pages: Sequence[PageLayout],
        gold: Sequence[EntrySpan],
        *,
        ignore: set[str] | frozenset[str] = frozenset(),
    ) -> TrainReport:
        """Learn entry starts from ``gold`` (the first line of every non-continuation span is a
        start, every other line is not); lines in ``ignore`` (undecided proposals) are left out.
        With enough pages, every fourth page is held out to measure the model, which is then
        trained again on all of them."""
        starts = {span.line_ids[0] for span in gold if span.line_ids and not span.continuation}
        gold_pages = {span.page for span in gold}
        used = [p for p in pages if p.page in gold_pages and not p.is_scanned and p.lines]
        per_page: dict[int, list[tuple[list[str], int]]] = {}
        for page in used:
            examples = [
                (features, 1 if line.id in starts else -1)
                for line, features in self.page_examples(page)
                if line.id not in ignore
            ]
            per_page[page.page] = examples
        ordered = sorted(per_page)
        warnings: list[str] = []
        total = sum(len(v) for v in per_page.values())
        positives = sum(1 for v in per_page.values() for _f, y in v if y > 0)
        if positives == 0:
            raise ValueError("no entry starts among the reviewed spans; nothing to learn")
        held_out = (
            ordered[HELD_OUT_EVERY - 1 :: HELD_OUT_EVERY] if len(ordered) >= HELD_OUT_EVERY else []
        )
        train_pages = [p for p in ordered if p not in held_out]
        metrics: tuple[float, float, float] | None = None
        if held_out:
            self.weights = self._fit([e for p in train_pages for e in per_page[p]])
            metrics = self._evaluate([e for p in held_out for e in per_page[p]])
        else:
            warnings.append(
                f"only {len(ordered)} reviewed page(s): the numbers below are on the training "
                "pages themselves; review a few more pages for an honest estimate"
            )
        self.weights = self._fit([e for p in ordered for e in per_page[p]])
        if metrics is None:
            metrics = self._evaluate([e for p in ordered for e in per_page[p]])
        precision, recall, f1 = metrics
        if positives < 20:
            warnings.append(f"only {positives} entry starts to learn from")
        self.report = TrainReport(
            pages=len(ordered),
            lines=total,
            positives=positives,
            held_out_pages=len(held_out),
            precision=round(precision, 3),
            recall=round(recall, 3),
            f1=round(f1, 3),
            epochs=self.params.epochs,
            features=len(self.weights),
            warnings=warnings,
        )
        return self.report

    def _fit(self, examples: Sequence[tuple[list[str], int]]) -> dict[str, float]:
        """Averaged perceptron: the average of the weight vector over every update survives the
        noise of the last examples seen."""
        rng = random.Random(self.params.seed)
        weights: dict[str, float] = defaultdict(float)
        accumulated: dict[str, float] = defaultdict(float)
        counter = 1
        order = list(range(len(examples)))
        for _epoch in range(self.params.epochs):
            rng.shuffle(order)
            for index in order:
                features, label = examples[index]
                predicted = 1 if sum(weights[f] for f in features) > 0 else -1
                if predicted != label:
                    for f in features:
                        weights[f] += label
                        accumulated[f] += counter * label
                counter += 1
        return {
            f: round(w - accumulated[f] / counter, 6)
            for f, w in weights.items()
            if abs(w - accumulated[f] / counter) > 1e-9
        }

    def _evaluate(self, examples: Sequence[tuple[list[str], int]]) -> tuple[float, float, float]:
        tp = fp = fn = 0
        for features, label in examples:
            predicted = self.score(features) > 0
            if predicted and label > 0:
                tp += 1
            elif predicted:
                fp += 1
            elif label > 0:
                fn += 1
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        return precision, recall, f1

    # -- segmentation ---------------------------------------------------------------------

    def segment(
        self, pages: Sequence[PageLayout], params: dict[str, Any] | None = None
    ) -> tuple[list[EntrySpan], SegmentationStats]:
        if params:
            raw = dict(params)
            weights = raw.pop("weights", None)
            if weights:
                self.weights = dict(weights)
            if raw:
                self.params = LearnedParams(**{**self.params.model_dump(), **raw})
        if not self.weights:
            raise ValueError("the learned segmenter has no weights yet; train it first")
        text_pages = [p for p in pages if not p.is_scanned and p.lines]
        rule_params = self._rule_params()
        spans: list[EntrySpan] = []
        skipped_total = 0
        lines_total = 0
        for page in text_pages:
            _lines, skipped = RuleSegmenter._body_lines(page, rule_params)
            skipped_total += skipped
            lines_total += len(page.lines)
            spans.extend(self._segment_page(page))
        stats = SegmentationStats(
            pages=len(text_pages),
            spans=len(spans),
            continuations=sum(1 for s in spans if s.continuation),
            lines=lines_total,
            lines_skipped=skipped_total,
            mode="learned",
            confidence_mean=round(statistics.fmean([s.confidence for s in spans]), 3)
            if spans
            else 0.0,
        )
        if spans and stats.pages and stats.spans / stats.pages < 1.5:
            stats.warnings.append(
                f"only {stats.spans / stats.pages:.1f} entries per page from the learned model"
            )
        return spans, stats

    def _segment_page(self, page: PageLayout) -> list[EntrySpan]:
        spans: list[EntrySpan] = []
        current: list[Line] = []
        current_conf = 0.0
        current_head: str | None = None
        continuation = False
        min_chars = self.params.min_entry_chars

        def close() -> None:
            nonlocal current, current_conf, current_head, continuation
            if current:
                text = " ".join(line.text for line in current)
                if len(text.strip()) >= min_chars:
                    spans.append(
                        EntrySpan(
                            id=f"p{page.page}-l{len(spans) + 1}",
                            page=page.page,
                            line_ids=[line.id for line in current],
                            bboxes=_bboxes(current),
                            headword_guess=current_head,
                            confidence=round(current_conf, 2),
                            source="ml",
                            continuation=continuation,
                            text=text[:2000],
                        )
                    )
            current = []
            current_conf = 0.0
            current_head = None
            continuation = False

        previous_column = -1
        for line, features in self.page_examples(page):
            score = self.score(features)
            new_column = line.column != previous_column
            previous_column = line.column
            if score > 0:
                close()
                current = [line]
                current_conf = 0.5 + min(score, MAX_SCORE) / (2 * MAX_SCORE)
                current_head = _headword_same_font(line, self.params.max_headword_words)
                continue
            if not current:
                current = [line]
                current_conf = 0.4
                continuation = True
                continue
            if new_column and not current:
                close()
            current.append(line)
        close()
        return spans

    # -- persistence -------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "segmenter": self.name,
            "params": self.params.model_dump(),
            "weights": self.weights,
            "report": self.report.model_dump() if self.report else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LearnedSegmenter:
        segmenter = cls(dict(data.get("params") or {}))
        segmenter.weights = dict(data.get("weights") or {})
        if data.get("report"):
            segmenter.report = TrainReport.model_validate(data["report"])
        return segmenter

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False), encoding="utf-8")

    def load(self, path: Path) -> None:
        loaded = self.from_dict(json.loads(path.read_text(encoding="utf-8")))
        self.params, self.weights, self.report = loaded.params, loaded.weights, loaded.report


__all__ = ["LearnedParams", "LearnedSegmenter", "TrainReport", "line_features"]
