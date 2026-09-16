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


__all__ = ["BBox", "Block", "FontStat", "Line", "PageLayout", "Word"]
