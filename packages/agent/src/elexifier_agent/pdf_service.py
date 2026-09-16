"""PDF document services shared by the tools, the API and the worker: reading page layouts
from storage, profiling a document, and running a segmenter into the annotations table."""

from __future__ import annotations

import asyncio
import io
import tempfile
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_core.db.models import Annotation, AnnotationStatus, Document, File, PdfPage
from elexifier_core.events import publish
from elexifier_core.storage import NotFoundError, Storage, make_key
from elexifier_pipeline import registry
from elexifier_pipeline.pdf.layout import PdfplumberLayout
from elexifier_pipeline.pdf.models import BBox, EntrySpan, Line, PageLayout, SegmentationStats
from elexifier_pipeline.pdf.segmenters import RuleSegmenter

SYNC_SEGMENT_MAX_PAGES = 400
LAYOUT_CONCURRENCY = 8


class PdfServiceError(Exception):
    pass


async def page_rows(db: AsyncSession, document_id: uuid.UUID) -> list[PdfPage]:
    return list(
        (
            await db.execute(
                select(PdfPage).where(PdfPage.document_id == document_id).order_by(PdfPage.page_no)
            )
        )
        .scalars()
        .all()
    )


async def load_layout(storage: Storage, page: PdfPage) -> PageLayout:
    data = b"".join([chunk async for chunk in storage.open(page.layout_s3_key)])
    return PageLayout.model_validate_json(data)


async def load_layouts(
    storage: Storage, pages: list[PdfPage], *, concurrency: int = LAYOUT_CONCURRENCY
) -> list[PageLayout]:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(page: PdfPage) -> PageLayout:
        async with semaphore:
            return await load_layout(storage, page)

    return list(await asyncio.gather(*(one(p) for p in pages)))


def profile_layouts(pages: list[PageLayout]) -> dict[str, Any]:
    """Facts the agent needs before segmenting: columns, fonts, density, typography shares."""
    text_pages = [p for p in pages if not p.is_scanned]
    columns = Counter(len(p.columns) for p in text_pages)
    fonts: Counter[tuple[str, float, bool, bool]] = Counter()
    for page in text_pages:
        for stat in page.fonts:
            fonts[(stat.font, stat.size, stat.bold, stat.italic)] += stat.chars
    total_chars = sum(fonts.values()) or 1
    lines = [line for p in text_pages for line in p.lines]
    bold_starts = sum(1 for line in lines if line.words and line.words[0].bold)
    body_sizes = [p.body_size for p in text_pages if p.body_size]
    body = Counter(body_sizes).most_common(1)[0][0] if body_sizes else None
    segmenter = RuleSegmenter()
    start_fonts = segmenter.line_start_fonts(text_pages, segmenter.params)
    detected = segmenter.detect_headword_font(text_pages)
    return {
        "pages": len(pages),
        "text_pages": len(text_pages),
        "scanned_pages": len(pages) - len(text_pages),
        "columns": {str(k): v for k, v in sorted(columns.items())},
        "body_size": body,
        "lines_per_page": round(len(lines) / max(1, len(text_pages)), 1),
        "bold_start_share": round(bold_starts / max(1, len(lines)), 3),
        "headword_font": (
            {"font": detected["font"], "size": detected["size"]} if detected else None
        ),
        "line_start_fonts": [
            {
                "font": e["font"],
                "size": e["size"],
                "bold": e["bold"],
                "flush_start_share": e["flush_start_share"],
                "start_concentration": e["start_concentration"],
                "samples": e["samples"],
            }
            for e in start_fonts[:6]
        ],
        "fonts": [
            {
                "font": f,
                "size": s,
                "bold": b,
                "italic": i,
                "share": round(n / total_chars, 3),
            }
            for (f, s, b, i), n in fonts.most_common(10)
        ],
        "sample_lines": [line.text[:100] for page in text_pages[:1] for line in page.lines[:8]],
    }


async def annotation_counts(db: AsyncSession, document_id: uuid.UUID) -> dict[str, int]:
    rows = (
        await db.execute(
            select(Annotation.status, func.count())
            .where(Annotation.document_id == document_id)
            .group_by(Annotation.status)
        )
    ).all()
    return {status: count for status, count in rows}


async def run_segmentation(
    db: AsyncSession,
    storage: Storage,
    document: Document,
    *,
    segmenter_name: str = "rule",
    params: dict[str, Any] | None = None,
    created_by: uuid.UUID | None = None,
    layouts: list[PageLayout] | None = None,
) -> tuple[list[EntrySpan], SegmentationStats, uuid.UUID]:
    """Segment a PDF document and replace its proposed rule annotations. Commits nothing."""
    try:
        segmenter = registry.get_segmenter(segmenter_name, params)
    except KeyError as exc:  # str(KeyError) quotes the message
        raise PdfServiceError(str(exc.args[0]) if exc.args else str(exc)) from exc
    except ValueError as exc:
        raise PdfServiceError(str(exc)) from exc
    pages = await page_rows(db, document.id)
    if not pages:
        raise PdfServiceError("the document has no extracted pages yet")
    if layouts is None:
        layouts = await load_layouts(storage, pages)
    spans, stats = await asyncio.to_thread(segmenter.segment, layouts)
    set_id = uuid.uuid4()
    # proposed spans of earlier runs are replaced; decisions (accepted, rejected, edited) and
    # the lexicographer's own boxes stay
    await db.execute(
        delete(Annotation).where(
            Annotation.document_id == document.id,
            Annotation.source != "user",
            Annotation.status == AnnotationStatus.PROPOSED.value,
        )
    )
    # a span that continues an entry from the previous page is linked to it both ways, so that
    # structuring can put the pieces back together
    rows: list[Annotation] = []
    for position, span in enumerate(spans):
        payload: dict[str, Any] = {
            "bboxes": [list(b) for b in span.bboxes],
            "line_ids": span.line_ids,
            "headword_guess": span.headword_guess,
            "confidence": span.confidence,
            "continuation": span.continuation,
            "text": span.text,
        }
        row = Annotation(
            id=uuid.uuid4(),
            document_id=document.id,
            page_no=span.page,
            bbox=list(span.bbox),
            label="entry",
            entry_span_id=span.id,
            source=span.source,
            status=AnnotationStatus.PROPOSED.value,
            payload=payload,
            set_id=set_id,
            position=position,
            created_by=created_by,
        )
        if span.continuation and rows and rows[-1].page_no < span.page:
            previous = rows[-1]
            payload["continues"] = str(previous.id)
            previous.payload = {**previous.payload, "continued_by": str(row.id)}
        rows.append(row)
        db.add(row)
    await db.flush()
    counts = await annotation_counts(db, document.id)
    document.stats = {
        **(document.stats or {}),
        "segmentation": {
            "set_id": str(set_id),
            "segmenter": segmenter_name,
            "params": segmenter.params.model_dump() if hasattr(segmenter, "params") else params,
            "stats": stats.model_dump(),
            "at": datetime.now(UTC).isoformat(),
        },
    }
    document.entry_count = sum(
        n for status, n in counts.items() if status != AnnotationStatus.REJECTED.value
    )
    await publish(
        db,
        document.project_id,
        "annotations.changed",
        {"document_id": str(document.id), "set_id": str(set_id), "spans": len(spans)},
    )
    await db.flush()
    return spans, stats, set_id


def image_key(project_id: uuid.UUID, document_id: uuid.UUID, page_no: int, dpi: int) -> str:
    return make_key(
        str(project_id), "derived", str(document_id), f"page-{page_no:05d}-{dpi}dpi.png"
    )


async def page_image(
    storage: Storage, document: Document, file: File, page_no: int, dpi: int = 110
) -> bytes:
    """PNG of one page, rendered once per resolution and kept in storage."""
    key = image_key(document.project_id, document.id, page_no, dpi)
    try:
        return b"".join([chunk async for chunk in storage.open(key)])
    except NotFoundError:
        pass
    with tempfile.NamedTemporaryFile(prefix="elexifier-", suffix=".pdf", delete=False) as handle:
        async for chunk in storage.open(file.s3_key):
            handle.write(chunk)
        path = Path(handle.name)
    try:
        png = await asyncio.to_thread(PdfplumberLayout().render_page, path, page_no, dpi)
    finally:
        await anyio.Path(path).unlink(missing_ok=True)
    await storage.put(key, png, content_type="image/png")
    return png


def crop_png(png: bytes, bbox: BBox, dpi: int, *, padding_pt: float = 6.0) -> bytes:
    """Crop a rendered page to a pdfplumber box (points) with a little padding."""
    from PIL import Image

    scale = dpi / 72
    with Image.open(io.BytesIO(png)) as image:
        left = max(0, int((bbox[0] - padding_pt) * scale))
        top = max(0, int((bbox[1] - padding_pt) * scale))
        right = min(image.width, int((bbox[2] + padding_pt) * scale))
        bottom = min(image.height, int((bbox[3] + padding_pt) * scale))
        if right <= left or bottom <= top:
            return png
        out = io.BytesIO()
        image.crop((left, top, right, bottom)).save(out, format="PNG")
        return out.getvalue()


def lines_in_region(layout: PageLayout, bbox: BBox | None) -> list[Line]:
    """Lines whose box overlaps ``bbox`` (all lines when None), in reading order."""
    if bbox is None:
        return list(layout.lines)
    x0, top, x1, bottom = bbox
    picked = [
        line
        for line in layout.lines
        if line.x1 > x0 and line.x0 < x1 and line.bottom > top and line.top < bottom
    ]
    return picked


__all__ = [
    "SYNC_SEGMENT_MAX_PAGES",
    "PdfServiceError",
    "annotation_counts",
    "crop_png",
    "image_key",
    "lines_in_region",
    "load_layout",
    "load_layouts",
    "page_image",
    "page_rows",
    "profile_layouts",
    "run_segmentation",
]
