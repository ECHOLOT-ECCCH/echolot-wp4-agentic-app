"""A tiny PDF writer for fixtures: standard Type1 fonts, absolute text placement, no images.

Enough to produce dictionary-like pages (columns, bold headwords, italic labels, hanging indents)
that exercise the layout extraction and the segmenter without a PDF library at test time.
"""

from __future__ import annotations

import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

FONTS = {"regular": "Helvetica", "bold": "Helvetica-Bold", "italic": "Helvetica-Oblique"}
_FONT_IDS = {"regular": "F1", "bold": "F2", "italic": "F3"}
_AVG_WIDTH = 0.52  # fallback advance width per point when a glyph is unknown


@dataclass
class TextRun:
    text: str
    style: str = "regular"  # regular | bold | italic


@dataclass
class Placed:
    x: float
    baseline: float  # PDF coordinates, origin bottom-left
    size: float
    runs: list[TextRun]


@dataclass
class PageSpec:
    width: float = 595.0
    height: float = 842.0
    items: list[Placed] = field(default_factory=list)

    def text(self, x: float, top: float, size: float, *runs: TextRun) -> None:
        """Place runs on one baseline; ``top`` is measured from the top like pdfplumber."""
        self.items.append(Placed(x, self.height - top - size * 0.8, size, list(runs)))


def _metrics(style: str) -> Mapping[str, float]:
    from pdfminer.fontmetrics import FONT_METRICS

    return FONT_METRICS[FONTS[style]][1]


def text_width(text: str, size: float, style: str = "regular") -> float:
    """Advance width from the standard-font metrics so runs sit exactly where a typesetter
    would put them (touching glyphs and spaces come out as in real PDFs)."""
    widths = _metrics(style)
    return sum(widths.get(char, _AVG_WIDTH * 1000) * size / 1000 for char in text)


def _escape(text: str) -> str:
    out = text.encode("cp1252", errors="replace").decode("cp1252")
    return out.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _content(page: PageSpec) -> bytes:
    parts = ["BT"]
    for item in page.items:
        x = item.x
        for run in item.runs:
            font = _FONT_IDS[run.style]
            parts.append(
                f"/{font} {item.size:.1f} Tf 1 0 0 1 {x:.2f} {item.baseline:.2f} Tm "
                f"({_escape(run.text)}) Tj"
            )
            x += text_width(run.text, item.size, run.style)
    parts.append("ET")
    return "\n".join(parts).encode("cp1252", errors="replace")


def write_pdf(pages: list[PageSpec], path: Path) -> None:
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")  # placeholder, filled after pages
    pages_obj = add(b"")
    font_ids = {
        style: add(
            f"<< /Type /Font /Subtype /Type1 /BaseFont /{name} "
            "/Encoding /WinAnsiEncoding >>".encode()
        )
        for style, name in FONTS.items()
    }
    resources = (
        "<< /Font << " + " ".join(f"/{_FONT_IDS[s]} {font_ids[s]} 0 R" for s in FONTS) + " >> >>"
    )
    page_ids = []
    for page in pages:
        stream = zlib.compress(_content(page))
        content = add(
            b"<< /Length "
            + str(len(stream)).encode()
            + b" /Filter /FlateDecode >>\nstream\n"
            + stream
            + b"\nendstream"
        )
        page_ids.append(
            add(
                f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 {page.width:.0f} "
                f"{page.height:.0f}] /Resources {resources} /Contents {content} 0 R >>".encode()
            )
        )
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode()
    objects[pages_obj - 1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{i} 0 R' for i in page_ids)}] "
        f"/Count {len(page_ids)} >>".encode()
    )

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    path.write_bytes(bytes(out))


# --- a dictionary-like fixture ------------------------------------------------------------------

_WORDS = [
    "abacus",
    "abandon",
    "abase",
    "abbey",
    "abbot",
    "abdicate",
    "abdomen",
    "abduct",
    "aberrant",
    "abet",
    "abhor",
    "abide",
    "ability",
    "abject",
    "ablaze",
    "able",
    "abnormal",
    "aboard",
    "abode",
    "abolish",
    "abound",
    "about",
    "above",
    "abrasive",
    "abreast",
    "abridge",
    "abroad",
    "abrupt",
    "abscond",
    "absent",
    "absolute",
    "absorb",
    "abstain",
    "abstract",
    "absurd",
    "abundant",
    "abuse",
    "abyss",
    "academy",
    "accede",
    "accent",
    "accept",
    "access",
    "accident",
    "acclaim",
    "account",
    "accrue",
    "accuse",
    "achieve",
    "acid",
]
_POS = ["n.", "v.", "adj.", "adv."]
_DEFS = [
    "a frame with beads used for counting and calculation",
    "to leave behind completely and finally",
    "to lower in rank, office, prestige or esteem",
    "a monastery or convent under an abbot or abbess",
    "the part of the body between the chest and the pelvis, containing the stomach",
    "deviating from what is normal or usual, typically in an unwelcome way",
]


def dictionary_pages(
    entries: int = 130,
    *,
    columns: int = 2,
    body_size: float = 9.5,
    per_page_lines: int = 45,
    scanned_last_page: bool = True,
) -> list[PageSpec]:
    """Two-column dictionary pages: bold headword, italic part of speech, wrapped definition
    with a hanging indent, entries flowing column by column and page by page."""
    width, height = 595.0, 842.0
    margin, gutter = 50.0, 18.0
    column_width = (width - 2 * margin - gutter * (columns - 1)) / columns
    line_height = body_size * 1.35
    indent = 12.0
    pages: list[PageSpec] = []
    page = PageSpec(width, height)
    column = 0
    line_no = 0

    def flush_column() -> None:
        nonlocal column, line_no, page
        column += 1
        line_no = 0
        if column >= columns:
            pages.append(page)
            page = PageSpec(width, height)
            column = 0

    for index in range(entries):
        headword = _WORDS[index % len(_WORDS)] + ("" if index < len(_WORDS) else str(index))
        pos = _POS[index % len(_POS)]
        definition = _DEFS[index % len(_DEFS)]
        words = definition.split()
        # first line: headword + pos + as much definition as fits
        x0 = margin + column * (column_width + gutter)
        lines: list[list[TextRun]] = []
        first = [TextRun(headword + " ", "bold"), TextRun(pos + " ", "italic")]
        used = text_width(headword + " ", body_size, "bold") + text_width(
            pos + " ", body_size, "italic"
        )
        current = first
        max_width = column_width
        for word in words:
            w = text_width(word + " ", body_size)
            if (used + w > max_width and current is not first) or (used + w > max_width):
                lines.append(current)
                current = []
                used = indent
                max_width = column_width
            current.append(TextRun(word + " ", "regular"))
            used += w
        lines.append(current)
        if line_no + len(lines) > per_page_lines:
            flush_column()
            x0 = margin + column * (column_width + gutter)
        for i, runs in enumerate(lines):
            top = margin + line_no * line_height
            page.text(x0 + (indent if i > 0 else 0), top, body_size, *runs)
            line_no += 1
        line_no += 0  # no extra gap between entries: the segmenter must rely on typography
    if page.items:
        pages.append(page)
    # running heads
    for number, spec in enumerate(pages, start=1):
        spec.text(margin, 22, 8.0, TextRun(f"Sample columns dictionary  -  {number}", "regular"))
    if scanned_last_page:
        pages.append(PageSpec(width, height))  # no text layer at all
    return pages


if __name__ == "__main__":  # python -m elexifier_pipeline.sample.pdfgen
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("columns-dictionary.pdf")
    write_pdf(dictionary_pages(), target)
    print(f"wrote {target} ({target.stat().st_size} bytes)")
