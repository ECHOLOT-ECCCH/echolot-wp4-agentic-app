"""Layout extraction with pdfplumber (words, lines, columns, blocks) and page rendering with
pypdfium2. Implements the ``PdfLayout`` protocol of PLAN 7.7."""

from __future__ import annotations

import io
import statistics
from collections import Counter
from collections.abc import Iterator, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

import pdfplumber
import pypdfium2 as pdfium

from elexifier_pipeline.pdf.models import Block, FontStat, Line, PageLayout, Word

SCAN_MIN_CHARS = 20
BOLD_MARKERS = ("bold", "black", "heavy", "semibold", "demibold", "extrabold", "ultrabold")
ITALIC_MARKERS = ("italic", "oblique", "slanted")


def _style(fontname: str) -> tuple[bool, bool]:
    lowered = fontname.lower()
    # subset prefixes look like "ABCDEF+Times-BoldItalic"; the style is in the family part
    return any(m in lowered for m in BOLD_MARKERS), any(m in lowered for m in ITALIC_MARKERS)


def _family(fontname: str) -> str:
    return fontname.split("+", 1)[-1]


def _word_from_chars(chars: list[dict[str, Any]]) -> Word:
    counts: Counter[tuple[str, float]] = Counter()
    for char in chars:
        if str(char.get("text", "")).strip():
            counts[(str(char.get("fontname", "")), round(float(char.get("size", 0.0)), 2))] += 1
    if counts:
        (fontname, size), _n = counts.most_common(1)[0]
    else:
        fontname = str(chars[0].get("fontname", ""))
        size = round(float(chars[0].get("size", 0.0)), 2)
    bold, italic = _style(fontname)
    return Word(
        text="".join(str(c.get("text", "")) for c in chars),
        x0=min(float(c["x0"]) for c in chars),
        top=min(float(c["top"]) for c in chars),
        x1=max(float(c["x1"]) for c in chars),
        bottom=max(float(c["bottom"]) for c in chars),
        font=fontname,
        size=size,
        bold=bold,
        italic=italic,
    )


def _words(raw: dict[str, Any]) -> list[Word]:
    """Words of one pdfplumber word. pdfplumber splits on spaces; a change of font family or
    size inside a word (a marker glyph glued to the next word, a bold headword touching its
    part of speech) splits it here. Subset changes of the same family do not: accented letters
    often come from a second subset ("ve" + "č" + "jega" is one word)."""
    chars = [c for c in (raw.get("chars") or []) if str(c.get("text", "")) != ""]
    if not chars:
        bold, italic = _style(str(raw.get("fontname", "")))
        return [
            Word(
                text=str(raw["text"]),
                x0=float(raw["x0"]),
                top=float(raw["top"]),
                x1=float(raw["x1"]),
                bottom=float(raw["bottom"]),
                font=str(raw.get("fontname", "")),
                size=round(float(raw.get("size", 0.0)), 2),
                bold=bold,
                italic=italic,
            )
        ]
    groups: list[list[dict[str, Any]]] = [[chars[0]]]
    for char in chars[1:]:
        previous = groups[-1][-1]
        same = (
            _family(str(char.get("fontname", ""))) == _family(str(previous.get("fontname", "")))
            and abs(float(char.get("size", 0.0)) - float(previous.get("size", 0.0))) < 0.3
        )
        if same:
            groups[-1].append(char)
        else:
            groups.append([char])
    return [
        _word_from_chars(group)
        for group in groups
        if "".join(str(c.get("text", "")) for c in group).strip()
    ]


def _rows(words: list[Word], tolerance: float) -> list[list[Word]]:
    """Cluster words sharing a baseline (by bottom edge)."""
    rows: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w.bottom, w.x0)):
        if rows and abs(rows[-1][-1].bottom - word.bottom) <= tolerance:
            rows[-1].append(word)
        else:
            rows.append([word])
    for row in rows:
        row.sort(key=lambda w: w.x0)
    return rows


def _segments(row: list[Word], gap: float) -> list[list[Word]]:
    """Split a baseline row where the horizontal gap is large (column boundary, running head)."""
    segments: list[list[Word]] = [[row[0]]]
    for previous, word in pairwise(row):
        if word.x0 - previous.x1 > gap:
            segments.append([word])
        else:
            segments[-1].append(word)
    return segments


def _column_edges(segments: list[list[Word]], page_width: float) -> list[float]:
    """Left edges of text columns from the distribution of segment starts."""
    if not segments:
        return []
    bucket = 4.0
    counts: Counter[float] = Counter(round(seg[0].x0 / bucket) * bucket for seg in segments)
    threshold = max(3, int(0.06 * len(segments)))
    peaks = sorted(x for x, n in counts.items() if n >= threshold)
    edges: list[float] = []
    for x in peaks:
        if edges and x - edges[-1] < page_width * 0.12:
            continue  # hanging indents and small shifts belong to the previous column
        edges.append(x)
    if not edges:
        edges = [min(seg[0].x0 for seg in segments)]
    return edges


def _column_of(x0: float, edges: list[float]) -> int:
    column = 0
    for index, edge in enumerate(edges):
        if x0 >= edge - 6.0:
            column = index
    return column


def build_page_layout(page: Any, number: int) -> PageLayout:
    """Layout of one pdfplumber page (1-based ``number``)."""
    raw_words = page.extract_words(
        keep_blank_chars=False, x_tolerance=2, y_tolerance=2, return_chars=True
    )
    words = [word for raw in raw_words if str(raw["text"]).strip() for word in _words(raw)]
    width, height = float(page.width), float(page.height)
    rotation = int(getattr(page, "rotation", 0) or 0)
    text_chars = sum(len(w.text) for w in words)
    layout = PageLayout(
        page=number,
        width=width,
        height=height,
        rotation=rotation,
        text_chars=text_chars,
        is_scanned=text_chars < SCAN_MIN_CHARS,
    )
    if not words:
        return layout

    sizes = [w.size for w in words if w.size > 0]
    median_size = statistics.median(sizes) if sizes else 10.0
    char_widths = [(w.x1 - w.x0) / max(1, len(w.text)) for w in words]
    median_char = statistics.median(char_widths) if char_widths else median_size * 0.5
    gap = max(8.0, 3.0 * median_char)
    rows = _rows(words, tolerance=max(2.0, 0.35 * median_size))
    segments = [seg for row in rows for seg in _segments(row, gap)]
    edges = _column_edges(segments, width)

    lines: list[Line] = []
    for seg in segments:
        lines.append(
            Line(
                id="",
                words=seg,
                x0=min(w.x0 for w in seg),
                top=min(w.top for w in seg),
                x1=max(w.x1 for w in seg),
                bottom=max(w.bottom for w in seg),
                column=_column_of(seg[0].x0, edges),
            )
        )
    lines.sort(key=lambda line: (line.column, line.top, line.x0))
    for index, line in enumerate(lines):
        line.id = f"p{number}-l{index + 1}"

    # blocks: runs of lines in one column without a large vertical gap
    pitches = [b.top - a.top for a, b in pairwise(lines) if a.column == b.column and b.top > a.top]
    pitch = statistics.median(pitches) if pitches else median_size * 1.3
    blocks: list[Block] = []
    current: list[Line] = []

    def close() -> None:
        if current:
            blocks.append(
                Block(
                    id=f"p{number}-b{len(blocks) + 1}",
                    line_ids=[line.id for line in current],
                    x0=min(line.x0 for line in current),
                    top=min(line.top for line in current),
                    x1=max(line.x1 for line in current),
                    bottom=max(line.bottom for line in current),
                    column=current[0].column,
                )
            )

    for line in lines:
        if current and (
            line.column != current[-1].column or line.top - current[-1].top > 1.8 * pitch
        ):
            close()
            current = []
        current.append(line)
    close()

    stats: Counter[tuple[str, float, bool, bool]] = Counter()
    for w in words:
        stats[(w.font, w.size, w.bold, w.italic)] += len(w.text)
    fonts = [
        FontStat(font=f, size=s, bold=b, italic=i, chars=n)
        for (f, s, b, i), n in stats.most_common(12)
    ]
    layout.lines = lines
    layout.blocks = blocks
    layout.columns = edges
    layout.body_size = fonts[0].size if fonts else None
    layout.fonts = fonts
    return layout


class PdfplumberLayout:
    """``PdfLayout`` implementation: pdfplumber for text, pypdfium2 for rendering."""

    name = "pdfplumber"

    def page_count(self, pdf_path: Path) -> int:
        with pdfplumber.open(str(pdf_path)) as pdf:
            return len(pdf.pages)

    def extract(self, pdf_path: Path, pages: Sequence[int] | None = None) -> Iterator[PageLayout]:
        """Yield layouts for the given 1-based pages (all pages when None), freeing each page."""
        with pdfplumber.open(str(pdf_path)) as pdf:
            numbers = list(pages) if pages is not None else range(1, len(pdf.pages) + 1)
            for number in numbers:
                page = pdf.pages[number - 1]
                try:
                    yield build_page_layout(page, number)
                finally:
                    page.flush_cache()
                    page.get_textmap.cache_clear()

    def render_page(self, pdf_path: Path, page: int, dpi: int = 110) -> bytes:
        """PNG bytes of one page at the given resolution."""
        document = pdfium.PdfDocument(str(pdf_path))
        try:
            pdf_page = document[page - 1]
            try:
                bitmap = pdf_page.render(scale=dpi / 72)  # type: ignore[arg-type]  # float ok
                image = bitmap.to_pil()
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                return buffer.getvalue()
            finally:
                pdf_page.close()
        finally:
            document.close()


__all__ = ["PdfplumberLayout", "build_page_layout"]
