"""Layout models (PLAN 7.7). Coordinates follow 7.10: PDF points, origin top-left,
(x0, top, x1, bottom); page width and height in points."""

from __future__ import annotations

from pydantic import BaseModel, Field

BBox = tuple[float, float, float, float]


class Word(BaseModel):
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    font: str
    size: float
    bold: bool = False
    italic: bool = False


class Line(BaseModel):
    id: str
    words: list[Word]
    x0: float
    top: float
    x1: float
    bottom: float
    column: int = 0

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def bbox(self) -> BBox:
        return (self.x0, self.top, self.x1, self.bottom)


class Block(BaseModel):
    id: str
    line_ids: list[str]
    x0: float
    top: float
    x1: float
    bottom: float
    column: int = 0


class FontStat(BaseModel):
    font: str
    size: float
    bold: bool
    italic: bool
    chars: int


class PageLayout(BaseModel):
    page: int  # 1-based
    width: float
    height: float
    rotation: int = 0
    lines: list[Line] = Field(default_factory=list)
    blocks: list[Block] = Field(default_factory=list)
    text_chars: int = 0
    is_scanned: bool = False  # no usable text layer (scan or empty page)
    columns: list[float] = Field(default_factory=list)  # left edges in reading order
    body_size: float | None = None  # font size carrying most characters
    fonts: list[FontStat] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


class EntrySpan(BaseModel):
    """A proposed dictionary entry on a page: which lines belong to it and where it is."""

    id: str
    page: int
    line_ids: list[str]
    bboxes: list[BBox]  # one box per contiguous run of lines in a column
    headword_guess: str | None = None
    confidence: float = 0.5
    source: str = "rule"  # rule | ml | user
    continuation: bool = False  # continues an entry that started on the previous page/column
    text: str = ""

    @property
    def bbox(self) -> BBox:
        """The box covering all parts (for lists; drawing uses ``bboxes``)."""
        xs0 = min(b[0] for b in self.bboxes)
        tops = min(b[1] for b in self.bboxes)
        xs1 = max(b[2] for b in self.bboxes)
        bottoms = max(b[3] for b in self.bboxes)
        return (xs0, tops, xs1, bottoms)


class SegmentationStats(BaseModel):
    pages: int = 0
    spans: int = 0
    continuations: int = 0
    lines: int = 0
    lines_skipped: int = 0  # running heads, page numbers
    bold_start_share: float = 0.0  # share of text lines starting with a bold word
    mode: str = "auto"  # font | bold | size | indent, as resolved
    headword_font: str | None = None  # font mode: the font that starts entries
    warnings: list[str] = Field(default_factory=list)
    confidence_mean: float = 0.0


__all__ = [
    "BBox",
    "Block",
    "EntrySpan",
    "FontStat",
    "Line",
    "PageLayout",
    "SegmentationStats",
    "Word",
]
