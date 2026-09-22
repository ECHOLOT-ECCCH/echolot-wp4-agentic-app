"""Entry-span annotations on PDF pages: proposals from segmenters, decisions and boxes by the
lexicographer (M2.2). Bounding boxes follow PLAN 7.10."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_agent.catalog import curated_info
from elexifier_agent.pdf_service import (
    SYNC_SEGMENT_MAX_PAGES,
    PdfServiceError,
    annotation_counts,
    load_layout,
    page_rows,
    run_segmentation,
)
from elexifier_agent.structuring import (
    StructuringError,
    estimate_for,
    pick_spans,
    queue_full_structuring,
    structure_sample,
    usable_spans,
)
from elexifier_api import audit
from elexifier_api.authz import get_project_or_404
from elexifier_api.deps import (
    CurrentSecretBox,
    CurrentSettings,
    CurrentStorage,
    CurrentUser,
    DbSession,
    State,
)
from elexifier_api.models_resolver import resolve_model
from elexifier_api.schemas import (
    AcceptRest,
    AnnotationBulk,
    AnnotationCreate,
    AnnotationMerge,
    AnnotationOut,
    AnnotationPage,
    AnnotationSplit,
    AnnotationUpdate,
    ReviewOut,
    RunDetailOut,
    SegmentOut,
    SegmentRequest,
    StructureProfileIn,
    StructureProfileOut,
    StructureRequest,
)
from elexifier_core.db.models import Annotation, AnnotationStatus, Document, ProjectRole
from elexifier_core.events import publish
from elexifier_pipeline.pdf.structurers import StructureProfile

router = APIRouter(prefix="/api/projects/{project_id}", tags=["annotations"])


async def _pdf_document(
    db: AsyncSession, project_id: uuid.UUID, document_id: uuid.UUID
) -> Document:
    document = await db.get(Document, document_id)
    if document is None or document.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if document.type != "pdf":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Not a PDF document")
    return document


async def _refresh_entry_count(db: AsyncSession, document: Document) -> None:
    counts = await annotation_counts(db, document.id)
    document.entry_count = sum(n for s, n in counts.items() if s != AnnotationStatus.REJECTED.value)
    await publish(db, document.project_id, "annotations.changed", {"document_id": str(document.id)})


@router.get("/documents/{document_id}/annotations", response_model=AnnotationPage)
async def list_annotations(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    page_no: int | None = Query(default=None, ge=1),
    status_filter: str | None = Query(default=None, alias="status"),
    flagged: bool = Query(default=False, description="only spans with review flags"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=2000),
) -> AnnotationPage:
    await get_project_or_404(db, project_id, user)
    await _pdf_document(db, project_id, document_id)
    base = select(Annotation).where(Annotation.document_id == document_id)
    if page_no is not None:
        base = base.where(Annotation.page_no == page_no)
    if status_filter:
        base = base.where(Annotation.status.in_(status_filter.split(",")))
    if flagged:
        base = base.where(_flagged_condition())
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        (
            await db.execute(
                base.order_by(Annotation.page_no, Annotation.position, Annotation.created_at)
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return AnnotationPage(
        items=[AnnotationOut.model_validate(a) for a in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/documents/{document_id}/annotations",
    response_model=AnnotationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_annotation(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    body: AnnotationCreate,
    db: DbSession,
    user: CurrentUser,
) -> AnnotationOut:
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    document = await _pdf_document(db, project_id, document_id)
    pages = await page_rows(db, document.id)
    if body.page_no > len(pages):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No such page")
    last = (
        await db.execute(
            select(func.coalesce(func.max(Annotation.position), -1)).where(
                Annotation.document_id == document.id
            )
        )
    ).scalar_one()
    annotation = Annotation(
        document_id=document.id,
        page_no=body.page_no,
        bbox=body.bbox,
        label=body.label,
        entry_span_id=f"u{uuid.uuid4().hex[:8]}",
        source="user",
        status=AnnotationStatus.ACCEPTED.value,
        payload={"bboxes": [body.bbox], **body.payload},
        position=last + 1,
        created_by=user.id,
    )
    db.add(annotation)
    await db.flush()
    await _refresh_entry_count(db, document)
    audit.record(db, user.id, "annotation.created", annotation.id, document=str(document.id))
    await db.flush()
    await db.refresh(annotation)
    return AnnotationOut.model_validate(annotation)


@router.get("/annotations/{annotation_id}", response_model=AnnotationOut)
async def get_annotation(
    project_id: uuid.UUID, annotation_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> AnnotationOut:
    await get_project_or_404(db, project_id, user)
    annotation = await db.get(Annotation, annotation_id)
    document = await db.get(Document, annotation.document_id) if annotation else None
    if annotation is None or document is None or document.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Annotation not found")
    return AnnotationOut.model_validate(annotation)


@router.patch("/annotations/{annotation_id}", response_model=AnnotationOut)
async def update_annotation(
    project_id: uuid.UUID,
    annotation_id: uuid.UUID,
    body: AnnotationUpdate,
    db: DbSession,
    user: CurrentUser,
) -> AnnotationOut:
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    annotation = await db.get(Annotation, annotation_id)
    document = await db.get(Document, annotation.document_id) if annotation else None
    if annotation is None or document is None or document.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Annotation not found")
    changed_geometry = False
    if body.bbox is not None and body.bbox != annotation.bbox:
        payload = dict(annotation.payload or {})
        payload.setdefault("original_bbox", annotation.bbox)
        payload["bboxes"] = [body.bbox]
        annotation.payload = payload
        annotation.bbox = body.bbox
        changed_geometry = True
    if body.label is not None:
        annotation.label = body.label
    if body.payload is not None:
        annotation.payload = {**(annotation.payload or {}), **body.payload}
    if body.status is not None:
        annotation.status = body.status
    elif changed_geometry and annotation.source != "user":
        annotation.status = AnnotationStatus.EDITED.value
    annotation.updated_at = datetime.now(UTC)
    await db.flush()
    await _refresh_entry_count(db, document)
    await db.flush()
    await db.refresh(annotation)
    return AnnotationOut.model_validate(annotation)


@router.delete("/annotations/{annotation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_annotation(
    project_id: uuid.UUID, annotation_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> None:
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    annotation = await db.get(Annotation, annotation_id)
    document = await db.get(Document, annotation.document_id) if annotation else None
    if annotation is None or document is None or document.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Annotation not found")
    await db.delete(annotation)
    await db.flush()
    await _refresh_entry_count(db, document)
    audit.record(db, user.id, "annotation.deleted", annotation_id, document=str(document.id))
    await db.flush()


@router.post("/documents/{document_id}/annotations/bulk", response_model=dict[str, int])
async def bulk_decide(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    body: AnnotationBulk,
    db: DbSession,
    user: CurrentUser,
) -> dict[str, int]:
    """Accept or reject many spans at once: by ids, a page, or the whole document."""
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    document = await _pdf_document(db, project_id, document_id)
    statement = update(Annotation).where(Annotation.document_id == document.id)
    if body.ids:
        statement = statement.where(Annotation.id.in_(body.ids))
    if body.page_no is not None:
        statement = statement.where(Annotation.page_no == body.page_no)
    if body.only_status:
        statement = statement.where(Annotation.status == body.only_status)
    result = await db.execute(
        statement.values(status=body.status, updated_at=datetime.now(UTC)).returning(Annotation.id)
    )
    updated = len(result.scalars().all())
    await _refresh_entry_count(db, document)
    audit.record(db, user.id, "annotation.bulk", document.id, status=body.status, count=updated)
    await db.flush()
    return {"updated": updated, **(await annotation_counts(db, document.id))}


@router.post("/documents/{document_id}/segment", response_model=SegmentOut)
async def segment_document(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    body: SegmentRequest,
    db: DbSession,
    user: CurrentUser,
    storage: CurrentStorage,
    state: State,
) -> SegmentOut:
    """Run a segmenter over the document: synchronously for small documents, as a job above
    SYNC_SEGMENT_MAX_PAGES pages."""
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    document = await _pdf_document(db, project_id, document_id)
    pages = await page_rows(db, document.id)
    if not pages:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "The layout is not extracted yet"
        )
    if len(pages) > SYNC_SEGMENT_MAX_PAGES:
        job_id = await state.jobs.configure_task("pdf.segment", queue="heavy").defer_async(
            document_id=str(document.id),
            segmenter=body.segmenter,
            params=body.params,
            user_id=str(user.id),
        )
        return SegmentOut(
            document_id=document.id, queued=True, job_id=int(job_id) if job_id else None
        )
    try:
        _spans, stats, set_id = await run_segmentation(
            db,
            storage,
            document,
            segmenter_name=body.segmenter,
            params=body.params,
            created_by=user.id,
        )
    except PdfServiceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    audit.record(db, user.id, "document.segmented", document.id, segmenter=body.segmenter)
    await db.flush()
    payload: dict[str, Any] = stats.model_dump()
    return SegmentOut(document_id=document.id, queued=False, set_id=set_id, stats=payload)


def _bbox_of(boxes: list[list[float]]) -> list[float]:
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


async def _lines_of(
    storage: Any, db: AsyncSession, document: Document, annotation: Annotation
) -> list[dict[str, Any]]:
    """The layout lines inside a span (by id when the segmenter recorded them, else by box)."""
    pages = {p.page_no: p for p in await page_rows(db, document.id)}
    page = pages.get(annotation.page_no)
    if page is None:
        return []
    layout = await load_layout(storage, page)
    wanted = set((annotation.payload or {}).get("line_ids") or [])
    lines = []
    for line in layout.lines:
        inside = line.id in wanted
        if not wanted:
            x0, top, x1, bottom = annotation.bbox
            inside = (
                line.top >= top - 1 and line.bottom <= bottom + 1 and line.x0 < x1 and line.x1 > x0
            )
        if inside:
            lines.append(
                {
                    "id": line.id,
                    "x0": line.x0,
                    "top": line.top,
                    "x1": line.x1,
                    "bottom": line.bottom,
                    "column": line.column,
                    "text": line.text,
                }
            )
    return lines


def _span_payload(lines: list[dict[str, Any]], headword: str | None) -> dict[str, Any]:
    boxes: list[list[float]] = []
    group: list[dict[str, Any]] = []
    for line in lines:
        if group and line["column"] != group[-1]["column"]:
            boxes.append(_bbox_of([[g["x0"], g["top"], g["x1"], g["bottom"]] for g in group]))
            group = []
        group.append(line)
    if group:
        boxes.append(_bbox_of([[g["x0"], g["top"], g["x1"], g["bottom"]] for g in group]))
    return {
        "bboxes": boxes,
        "line_ids": [line["id"] for line in lines],
        "headword_guess": headword,
        "text": " ".join(line["text"] for line in lines)[:2000],
        "confidence": 1.0,
    }


@router.post("/annotations/{annotation_id}/split", response_model=list[AnnotationOut])
async def split_annotation(
    project_id: uuid.UUID,
    annotation_id: uuid.UUID,
    body: AnnotationSplit,
    db: DbSession,
    user: CurrentUser,
    storage: CurrentStorage,
) -> list[AnnotationOut]:
    """Split one span into two at a vertical position; both halves become the lexicographer's."""
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    annotation = await db.get(Annotation, annotation_id)
    document = await db.get(Document, annotation.document_id) if annotation else None
    if annotation is None or document is None or document.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Annotation not found")
    lines = await _lines_of(storage, db, document, annotation)
    x0, top, x1, bottom = annotation.bbox
    if lines:
        # the line under the click point starts the second half
        first = [line for line in lines if line["bottom"] <= body.y]
        second = [line for line in lines if line["bottom"] > body.y]
        if not first or not second:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Split point leaves one half empty"
            )
        halves = [
            (
                _bbox_of([[ln["x0"], ln["top"], ln["x1"], ln["bottom"]] for ln in first]),
                _span_payload(first, (annotation.payload or {}).get("headword_guess")),
            ),
            (
                _bbox_of([[ln["x0"], ln["top"], ln["x1"], ln["bottom"]] for ln in second]),
                _span_payload(
                    second, second[0]["text"].split(" ")[0] if second[0]["text"] else None
                ),
            ),
        ]
    else:
        if not (top + 2 < body.y < bottom - 2):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Split point outside the box"
            )
        halves = [
            ([x0, top, x1, body.y], {"bboxes": [[x0, top, x1, body.y]]}),
            ([x0, body.y, x1, bottom], {"bboxes": [[x0, body.y, x1, bottom]]}),
        ]
    # the halves take the span's place in reading order: everything after it moves up one
    await db.execute(
        update(Annotation)
        .where(Annotation.document_id == document.id, Annotation.position > annotation.position)
        .values(position=Annotation.position + 1)
    )
    created: list[Annotation] = []
    for offset, (bbox, payload) in enumerate(halves):
        row = Annotation(
            document_id=document.id,
            page_no=annotation.page_no,
            bbox=bbox,
            label=annotation.label,
            entry_span_id=f"u{uuid.uuid4().hex[:8]}",
            source="user",
            status=AnnotationStatus.ACCEPTED.value,
            payload={**payload, "split_from": str(annotation.id)},
            set_id=annotation.set_id,
            position=annotation.position + offset,
            created_by=user.id,
        )
        db.add(row)
        created.append(row)
    await db.delete(annotation)
    await db.flush()
    await _refresh_entry_count(db, document)
    audit.record(db, user.id, "annotation.split", annotation_id, document=str(document.id))
    await db.flush()
    for row in created:
        await db.refresh(row)
    return [AnnotationOut.model_validate(r) for r in created]


@router.post("/annotations/{annotation_id}/merge", response_model=AnnotationOut)
async def merge_annotation(
    project_id: uuid.UUID,
    annotation_id: uuid.UUID,
    body: AnnotationMerge,
    db: DbSession,
    user: CurrentUser,
    storage: CurrentStorage,
) -> AnnotationOut:
    """Merge two spans on the same page into one (the lexicographer's, accepted)."""
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    first = await db.get(Annotation, annotation_id)
    second = await db.get(Annotation, body.with_id)
    document = await db.get(Document, first.document_id) if first else None
    if (
        first is None
        or second is None
        or document is None
        or document.project_id != project_id
        or second.document_id != first.document_id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Annotation not found")
    if first.page_no != second.page_no:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Spans must be on the same page")
    a, b = sorted([first, second], key=lambda r: (r.position, r.bbox[1]))
    lines = await _lines_of(storage, db, document, a) + await _lines_of(storage, db, document, b)
    if lines:
        payload = _span_payload(lines, (a.payload or {}).get("headword_guess"))
    else:
        boxes = [
            *((a.payload or {}).get("bboxes") or [a.bbox]),
            *((b.payload or {}).get("bboxes") or [b.bbox]),
        ]
        payload = {"bboxes": boxes, "headword_guess": (a.payload or {}).get("headword_guess")}
    merged = Annotation(
        document_id=document.id,
        page_no=a.page_no,
        bbox=_bbox_of([list(box) for box in payload["bboxes"]]),
        label=a.label,
        entry_span_id=f"u{uuid.uuid4().hex[:8]}",
        source="user",
        status=AnnotationStatus.ACCEPTED.value,
        payload={**payload, "merged_from": [str(a.id), str(b.id)]},
        set_id=a.set_id,
        position=a.position,
        created_by=user.id,
    )
    db.add(merged)
    await db.delete(a)
    await db.delete(b)
    await db.flush()
    await _refresh_entry_count(db, document)
    audit.record(db, user.id, "annotation.merge", merged.id, document=str(document.id))
    await db.flush()
    await db.refresh(merged)
    return AnnotationOut.model_validate(merged)


@router.post("/documents/{document_id}/structure", response_model=RunDetailOut)
async def structure_document(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    body: StructureRequest,
    db: DbSession,
    user: CurrentUser,
    storage: CurrentStorage,
    box: CurrentSecretBox,
    settings: CurrentSettings,
    state: State,
) -> RunDetailOut:
    """Structure reviewed spans into DMLex: a sample now, or the whole document as a job (the
    UI's buttons; the agent uses the pdf_structure tool). The model is the project's unless one
    is given."""
    project, _role = await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    document = await _pdf_document(db, project_id, document_id)
    spans = await usable_spans(db, document)
    if body.span_ids:
        wanted = set(body.span_ids)
        chosen = [s for s in spans if s.id in wanted]
    else:
        chosen = pick_spans(spans, body.n, body.strategy)
    if not chosen:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No usable entry spans")
    stored = dict((project.settings or {}).get("structure_profile") or {})
    merged = {
        **stored,
        **{k: v for k, v in (body.profile or {}).items() if v not in (None, "", [])},
    }
    try:
        profile = StructureProfile.model_validate(merged)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Invalid profile: {exc}"
        ) from exc
    model = None
    model_spec: dict[str, Any] = {}
    info = None
    if body.structurer == "llm":
        spec, model = await resolve_model(
            db, user=user, project=project, requested=body.model, box=box, settings=settings
        )
        model_spec = spec.model_dump()
        info = curated_info(spec.provider, spec.model_id)
    with_images = (
        body.with_images if body.with_images is not None else bool(info and info.supports_vision)
    )
    if body.kind == "full":

        async def defer(name: str, kwargs: dict[str, Any]) -> int | None:
            job_id = await state.jobs.configure_task(name, queue="heavy").defer_async(**kwargs)
            return int(job_id) if job_id is not None else None

        estimate = estimate_for(
            spans,
            with_images=with_images and body.structurer == "llm",
            price_in_per_m=info.price_in_per_m if info else None,
            price_out_per_m=info.price_out_per_m if info else None,
        )
        if body.structurer == "rule":
            estimate = estimate.model_copy(
                update={"input_tokens": 0, "output_tokens": 0, "usd_estimate": 0.0}
            )
        try:
            queued = await queue_full_structuring(
                db,
                document=document,
                profile=profile,
                structurer_name=body.structurer,
                with_images=with_images and body.structurer == "llm",
                triggered_by=user.id,
                model_spec=model_spec,
                project_id=project_id,
                estimate=estimate,
                defer=defer,
            )
        except StructuringError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        if body.profile:
            project.settings = {**(project.settings or {}), "structure_profile": merged}
            await publish(db, project_id, "project.updated", {"what": "structure_profile"})
        audit.record(db, user.id, "run.structure_full", queued.id, project=str(project_id))
        await db.flush()
        return RunDetailOut.model_validate(queued)
    try:
        outcome = await structure_sample(
            db,
            storage,
            document=document,
            spans=chosen,
            profile=profile,
            model=model,
            structurer_name=body.structurer,
            with_images=with_images and body.structurer == "llm",
            triggered_by=user.id,
            model_spec=model_spec,
            project_id=project_id,
        )
    except StructuringError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if body.profile:
        project.settings = {**(project.settings or {}), "structure_profile": merged}
        await publish(db, project_id, "project.updated", {"what": "structure_profile"})
    audit.record(
        db,
        user.id,
        "run.structure",
        outcome.run.id,
        project=str(project_id),
        structurer=body.structurer,
    )
    await db.flush()
    return RunDetailOut.model_validate(outcome.run)


def _flagged_condition() -> Any:
    return (
        func.jsonb_array_length(func.coalesce(Annotation.payload["flags"], text("'[]'::jsonb"))) > 0
    )


@router.get("/documents/{document_id}/review", response_model=ReviewOut)
async def review_summary(
    project_id: uuid.UUID, document_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> ReviewOut:
    """What to look at instead of every page: the sample and the flagged pages and spans."""
    await get_project_or_404(db, project_id, user)
    document = await _pdf_document(db, project_id, document_id)
    counts = await annotation_counts(db, document.id)
    proposed = select(func.count()).where(
        Annotation.document_id == document.id,
        Annotation.status == AnnotationStatus.PROPOSED.value,
    )
    remaining = (await db.execute(proposed)).scalar_one()
    flagged = (await db.execute(proposed.where(_flagged_condition()))).scalar_one()
    flagged_total = (
        await db.execute(
            select(func.count()).where(Annotation.document_id == document.id, _flagged_condition())
        )
    ).scalar_one()
    return ReviewOut(
        document_id=document.id,
        summary=(document.stats or {}).get("review"),
        counts=counts,
        remaining_proposed=remaining,
        remaining_flagged_proposed=flagged,
        flagged_total=flagged_total,
    )


@router.post("/documents/{document_id}/annotations/accept-rest", response_model=dict[str, int])
async def accept_rest(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    body: AcceptRest,
    db: DbSession,
    user: CurrentUser,
) -> dict[str, int]:
    """Accept every remaining proposal (optionally all but the flagged ones)."""
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    document = await _pdf_document(db, project_id, document_id)
    statement = (
        update(Annotation)
        .where(
            Annotation.document_id == document.id,
            Annotation.status == AnnotationStatus.PROPOSED.value,
        )
        .values(status=AnnotationStatus.ACCEPTED.value, updated_at=datetime.now(UTC))
        .returning(Annotation.id)
    )
    if body.skip_flagged:
        statement = statement.where(~_flagged_condition())
    updated = len((await db.execute(statement)).scalars().all())
    await _refresh_entry_count(db, document)
    audit.record(db, user.id, "annotation.accept_rest", document.id, count=updated)
    await db.flush()
    return {"updated": updated, **(await annotation_counts(db, document.id))}


# --- the structure profile (what the structurer is told about the dictionary) -----------------


def _profile_out(settings: dict[str, Any] | None) -> StructureProfileOut:
    stored = dict((settings or {}).get("structure_profile") or {})
    try:
        profile = StructureProfile.model_validate(stored)
    except ValidationError:
        profile = StructureProfile()
    return StructureProfileOut(**profile.model_dump(), stored=bool(stored))


@router.get("/structure-profile", response_model=StructureProfileOut)
async def get_structure_profile(
    project_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> StructureProfileOut:
    """The profile the agent agreed with the lexicographer (defaults when none is stored)."""
    project, _role = await get_project_or_404(db, project_id, user)
    return _profile_out(project.settings)


@router.put("/structure-profile", response_model=StructureProfileOut)
async def put_structure_profile(
    project_id: uuid.UUID, body: StructureProfileIn, db: DbSession, user: CurrentUser
) -> StructureProfileOut:
    """Replace the profile. The next sample or full structuring uses it (and, as the profile is
    part of the cache key, calls the model again for every entry)."""
    project, _role = await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    if project.source_type != "pdf":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "The structure profile belongs to PDF projects"
        )
    profile = StructureProfile(
        source_language=body.source_language.strip() or "unknown",
        target_languages=[t.strip() for t in body.target_languages if t.strip()],
        contents=body.contents.strip(),
        entry_id_prefix=body.entry_id_prefix.strip() or "e",
    )
    project.settings = {**(project.settings or {}), "structure_profile": profile.model_dump()}
    audit.record(db, user.id, "profile.saved", project.id, project=str(project_id))
    await publish(db, project_id, "project.updated", {"what": "structure_profile"})
    return StructureProfileOut(**profile.model_dump(), stored=True)
