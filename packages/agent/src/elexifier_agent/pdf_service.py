"""PDF document services shared by the tools, the API and the worker: reading page layouts
from storage, profiling a document, and running a segmenter into the annotations table."""

from __future__ import annotations

import asyncio
import io
import json
import tempfile
import uuid
from collections import Counter
from dataclasses import dataclass
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
from elexifier_pipeline.pdf.anomalies import score_review
from elexifier_pipeline.pdf.layout import PdfplumberLayout
from elexifier_pipeline.pdf.learned import LearnedSegmenter, TrainReport
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
    if segmenter_name == "learned":
        segmenter: Any = await load_learned_segmenter(storage, document)
    else:
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
    flags, review = await asyncio.to_thread(score_review, spans, layouts, stats)
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
            "flags": flags.get(span.id, []),
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
        "review": review.model_dump(),
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


# --- the learned segmenter: trained on decisions, a second opinion on the rest (M3.7) ----------

LEARNED_MODEL_NAME = "learned-segmenter.json"
LEARNED_FLAG = "learned_disagrees"
DECIDED = (AnnotationStatus.ACCEPTED.value, AnnotationStatus.EDITED.value)


@dataclass
class LearnedOutcome:
    report: TrainReport
    model_key: str
    unreviewed_pages: int
    disagreements: list[dict[str, Any]]
    pages_flagged: list[int]
    applied: bool
    applied_pages: int = 0
    applied_spans: int = 0


def learned_model_key(document: Document) -> str:
    return make_key(str(document.project_id), "derived", str(document.id), LEARNED_MODEL_NAME)


async def load_learned_segmenter(storage: Storage, document: Document) -> LearnedSegmenter:
    info = (document.stats or {}).get("learned_segmenter") or {}
    key = info.get("key")
    if not key:
        raise PdfServiceError(
            "no learned segmenter for this document yet; train it first (pdf_train_segmenter)"
        )
    raw = b"".join([chunk async for chunk in storage.open(str(key))])
    return LearnedSegmenter.from_dict(json.loads(raw.decode("utf-8")))


async def gold_from_annotations(
    db: AsyncSession, document_id: uuid.UUID
) -> tuple[list[EntrySpan], set[str], set[int]]:
    """The lexicographer's decisions as gold: accepted, edited and drawn spans on the pages
    with at least one decision; the first lines of undecided proposals on those pages are
    unknown (ignored); everything else on those pages is a negative."""
    rows = (
        (
            await db.execute(
                select(Annotation).where(
                    Annotation.document_id == document_id, Annotation.label == "entry"
                )
            )
        )
        .scalars()
        .all()
    )
    decided_pages = {
        r.page_no
        for r in rows
        if r.source == "user" or r.status in (*DECIDED, AnnotationStatus.REJECTED.value)
    }
    gold: list[EntrySpan] = []
    unknown: set[str] = set()
    for row in rows:
        if row.page_no not in decided_pages:
            continue
        payload = row.payload or {}
        line_ids = [str(i) for i in payload.get("line_ids") or []]
        if row.status == AnnotationStatus.REJECTED.value:
            continue  # its lines stay negatives
        if row.status == AnnotationStatus.PROPOSED.value and row.source != "user":
            if line_ids:
                unknown.add(line_ids[0])
            continue
        if not line_ids:
            continue
        gold.append(
            EntrySpan(
                id=str(row.id),
                page=row.page_no,
                line_ids=line_ids,
                bboxes=[tuple(b) for b in payload.get("bboxes") or [row.bbox]],  # type: ignore[misc]
                headword_guess=payload.get("headword_guess"),
                confidence=float(payload.get("confidence") or 1.0),
                source=row.source,
                continuation=bool(payload.get("continuation")),
            )
        )
    return gold, unknown, decided_pages


async def train_learned_segmenter(
    db: AsyncSession,
    storage: Storage,
    document: Document,
    *,
    apply: bool = False,
    created_by: uuid.UUID | None = None,
) -> LearnedOutcome:
    """Train on the reviewed pages, store the model, and compare it with the proposals on the
    unreviewed pages: disagreements are flagged; with ``apply`` the proposals there are
    replaced by the learned spans. Commits nothing."""
    pages = await page_rows(db, document.id)
    if not pages:
        raise PdfServiceError("the document has no extracted pages yet")
    gold, unknown, decided_pages = await gold_from_annotations(db, document.id)
    if not gold:
        raise PdfServiceError(
            "no reviewed spans yet: accept, reject or draw entry boxes on a few pages first"
        )
    layouts = await load_layouts(storage, pages)
    segmenter = LearnedSegmenter()
    try:
        report = await asyncio.to_thread(
            segmenter.train,
            [layout for layout in layouts if layout.page in decided_pages],
            gold,
            ignore=unknown,
        )
    except ValueError as exc:
        raise PdfServiceError(str(exc)) from exc
    key = learned_model_key(document)
    await storage.put(
        key, json.dumps(segmenter.to_dict(), ensure_ascii=False).encode("utf-8"), "application/json"
    )
    spans, _stats = await asyncio.to_thread(segmenter.segment, layouts)
    learned_starts: dict[int, set[str]] = {}
    for span in spans:
        if span.line_ids and not span.continuation:
            learned_starts.setdefault(span.page, set()).add(span.line_ids[0])

    # the second opinion: proposals on the pages nobody reviewed
    current = (
        (
            await db.execute(
                select(Annotation).where(
                    Annotation.document_id == document.id,
                    Annotation.label == "entry",
                    Annotation.status == AnnotationStatus.PROPOSED.value,
                    Annotation.source != "user",
                )
            )
        )
        .scalars()
        .all()
    )
    for row in current:  # a new opinion replaces the old flags
        flags = [f for f in (row.payload or {}).get("flags") or [] if f != LEARNED_FLAG]
        if flags != list((row.payload or {}).get("flags") or []):
            row.payload = {**row.payload, "flags": flags}
    by_page: dict[int, list[Annotation]] = {}
    for row in current:
        if row.page_no not in decided_pages:
            by_page.setdefault(row.page_no, []).append(row)
    disagreements: list[dict[str, Any]] = []

    def flag(row: Annotation, kind: str, line_id: str | None = None) -> None:
        payload = dict(row.payload or {})
        flags = list(payload.get("flags") or [])
        if LEARNED_FLAG not in flags:
            flags.append(LEARNED_FLAG)
        payload["flags"] = flags
        payload["learned"] = {"kind": kind, **({"line_id": line_id} if line_id else {})}
        row.payload = payload
        disagreements.append(
            {"page": row.page_no, "kind": kind, "annotation_id": str(row.id), "line_id": line_id}
        )

    for page_no, rows in by_page.items():
        learned = learned_starts.get(page_no, set())
        current_starts: dict[str, Annotation] = {}
        owner: dict[str, Annotation] = {}
        for row in rows:
            payload = row.payload or {}
            line_ids = [str(i) for i in payload.get("line_ids") or []]
            for line_id in line_ids:
                owner[line_id] = row
            if line_ids and not payload.get("continuation"):
                current_starts[line_ids[0]] = row
        for start, row in current_starts.items():
            if start not in learned:
                flag(row, "not_a_start")
        for start in sorted(learned - set(current_starts)):
            row = owner.get(start)
            if row is not None and start != (row.payload or {}).get("line_ids", [None])[0]:
                flag(row, "missed_start", start)
    pages_flagged = sorted({d["page"] for d in disagreements})

    applied_pages = applied_spans = 0
    if apply and by_page:
        set_id = uuid.uuid4()
        position = (
            await db.execute(
                select(func.max(Annotation.position)).where(Annotation.document_id == document.id)
            )
        ).scalar_one() or 0
        for page_no, rows in by_page.items():
            for row in rows:
                await db.delete(row)
            for span in [s for s in spans if s.page == page_no]:
                position += 1
                applied_spans += 1
                db.add(
                    Annotation(
                        id=uuid.uuid4(),
                        document_id=document.id,
                        page_no=span.page,
                        bbox=list(span.bbox),
                        label="entry",
                        entry_span_id=span.id,
                        source="ml",
                        status=AnnotationStatus.PROPOSED.value,
                        payload={
                            "bboxes": [list(b) for b in span.bboxes],
                            "line_ids": span.line_ids,
                            "headword_guess": span.headword_guess,
                            "confidence": span.confidence,
                            "continuation": span.continuation,
                            "text": span.text,
                            "flags": [],
                            "learned": {"kind": "applied"},
                        },
                        set_id=set_id,
                        position=position,
                        created_by=created_by,
                    )
                )
            applied_pages += 1
        disagreements = []  # the proposals they concerned are gone
        pages_flagged = []
    await db.flush()

    stats = dict(document.stats or {})
    review = dict(stats.get("review") or {})
    if review and pages_flagged:
        sample = [int(p) for p in review.get("sample_pages") or []]
        counts = Counter(d["page"] for d in disagreements)
        for page_no, _n in counts.most_common():
            if len(sample) >= 50:
                break
            if page_no not in sample:
                sample.append(page_no)
        review["sample_pages"] = sorted(sample)
        review["reasons"] = {**(review.get("reasons") or {}), LEARNED_FLAG: len(disagreements)}
        stats["review"] = review
    stats["learned_segmenter"] = {
        "key": key,
        "report": report.model_dump(),
        "at": datetime.now(UTC).isoformat(),
        "reviewed_pages": sorted(decided_pages),
        "unreviewed_pages": len(by_page),
        "disagreements": len(disagreements),
        "pages_flagged": pages_flagged[:60],
        "applied": apply,
        "applied_pages": applied_pages,
    }
    if apply and applied_pages:
        stats["segmentation"] = {
            **(stats.get("segmentation") or {}),
            "segmenter": "learned",
            "applied_pages": applied_pages,
            "at": datetime.now(UTC).isoformat(),
        }
    document.stats = stats
    counts_now = await annotation_counts(db, document.id)
    document.entry_count = sum(
        n for status, n in counts_now.items() if status != AnnotationStatus.REJECTED.value
    )
    await publish(
        db,
        document.project_id,
        "annotations.changed",
        {"document_id": str(document.id), "learned": True, "flagged": len(disagreements)},
    )
    await db.flush()
    return LearnedOutcome(
        report=report,
        model_key=key,
        unreviewed_pages=len(by_page),
        disagreements=disagreements,
        pages_flagged=pages_flagged,
        applied=apply,
        applied_pages=applied_pages,
        applied_spans=applied_spans,
    )


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
