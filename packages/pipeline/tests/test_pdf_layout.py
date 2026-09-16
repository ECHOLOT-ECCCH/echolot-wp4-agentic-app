from __future__ import annotations

from pathlib import Path

from elexifier_pipeline.pdf.layout import PdfplumberLayout, build_page_layout
from elexifier_pipeline.pdf.models import PageLayout
from elexifier_pipeline.sample import COLUMNS_PDF, SAMPLE_PDF
from elexifier_pipeline.sample.pdfgen import PageSpec, TextRun, dictionary_pages, write_pdf


def test_columns_fixture_layout() -> None:
    layout = PdfplumberLayout()
    assert layout.page_count(COLUMNS_PDF) == 4
    pages = list(layout.extract(COLUMNS_PDF))
    assert [p.page for p in pages] == [1, 2, 3, 4]
    first = pages[0]
    assert (first.width, first.height, first.rotation) == (595.0, 842.0, 0)
    assert len(first.columns) == 2 and first.columns[0] < 60 < 300 < first.columns[1]
    assert first.body_size == 9.5 and first.text_chars > 2500 and not first.is_scanned
    # reading order: column 0 top to bottom, then column 1
    columns = [line.column for line in first.lines]
    assert columns == sorted(columns)
    tops = [line.top for line in first.lines if line.column == 0]
    assert tops == sorted(tops)
    # the running head is its own line, the first entry starts with a bold headword
    assert first.lines[0].text.startswith("Sample columns dictionary")
    entry_line = first.lines[1]
    assert entry_line.words[0].text == "abacus" and entry_line.words[0].bold
    assert entry_line.words[1].text == "n." and entry_line.words[1].italic
    assert not entry_line.words[2].bold
    # continuation lines keep the column of their entry (hanging indent)
    assert first.lines[2].column == 0 and first.lines[2].x0 > entry_line.x0
    assert all(line.id == f"p1-l{i + 1}" for i, line in enumerate(first.lines))
    assert first.blocks and all(b.line_ids for b in first.blocks)
    assert first.fonts[0].chars > first.fonts[1].chars
    # the last page has no text layer
    last = pages[-1]
    assert last.is_scanned and last.lines == [] and last.text_chars == 0
    # page selection
    only = list(layout.extract(COLUMNS_PDF, pages=[3]))
    assert len(only) == 1 and only[0].page == 3 and len(only[0].columns) == 2


def test_sample_pdf_and_rendering(tmp_path: Path) -> None:
    layout = PdfplumberLayout()
    pages = list(layout.extract(SAMPLE_PDF))
    assert len(pages) == 2 and pages[0].lines[1].text.startswith("abacus n.")
    png = layout.render_page(SAMPLE_PDF, 1, dpi=36)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 1000
    # a generated page with an explicit layout round-trips through the extractor
    spec = PageSpec()
    spec.text(72, 100, 12, TextRun("Hello ", "bold"), TextRun("world", "regular"))
    path = tmp_path / "one.pdf"
    write_pdf([spec], path)
    page = next(iter(layout.extract(path)))
    assert isinstance(page, PageLayout)
    assert page.lines[0].text == "Hello world" and page.lines[0].words[0].bold
    assert abs(page.lines[0].top - 100) < 3


def test_dictionary_pages_scale() -> None:
    pages = dictionary_pages(entries=400, columns=3, scanned_last_page=False)
    assert len(pages) >= 4 and all(p.items for p in pages)


def test_build_page_layout_handles_empty_words() -> None:
    class FakePage:
        width = 100.0
        height = 200.0
        rotation = 90

        def extract_words(self, **_: object) -> list[dict[str, object]]:
            return []

    layout = build_page_layout(FakePage(), 7)
    assert layout.page == 7 and layout.is_scanned and layout.rotation == 90
