"""PDF document services shared by the tools, the API and the worker: reading page layouts
from storage, profiling a document, and running a segmenter into the annotations table."""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_core.db.models import Annotation, AnnotationStatus, Document, PdfPage
from elexifier_core.events import publish
from elexifier_core.storage import Storage
from elexifier_pipeline import registry
from elexifier_pipeline.pdf.models import EntrySpan, PageLayout, SegmentationStats

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
    return {
        "pages": len(pages),
        "text_pages": len(text_pages),
        "scanned_pages": len(pages) - len(text_pages),
        "columns": {str(k): v for k, v in sorted(columns.items())},
        "body_size": body,
        "lines_per_page": round(len(lines) / max(1, len(text_pages)), 1),
        "bold_start_share": round(bold_starts / max(1, len(lines)), 3),
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
    for position, span in enumerate(spans):
        db.add(
            Annotation(
                document_id=document.id,
                page_no=span.page,
                bbox=list(span.bbox),
                label="entry",
                entry_span_id=span.id,
                source=span.source,
                status=AnnotationStatus.PROPOSED.value,
                payload={
                    "bboxes": [list(b) for b in span.bboxes],
                    "line_ids": span.line_ids,
                    "headword_guess": span.headword_guess,
                    "confidence": span.confidence,
                    "continuation": span.continuation,
                    "text": span.text,
                },
                set_id=set_id,
                position=position,
                created_by=created_by,
            )
        )
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


__all__ = [
    "SYNC_SEGMENT_MAX_PAGES",
    "PdfServiceError",
    "annotation_counts",
    "load_layout",
    "load_layouts",
    "page_rows",
    "profile_layouts",
    "run_segmentation",
]
