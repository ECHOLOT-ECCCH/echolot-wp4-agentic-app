"""Entry-span annotations on PDF pages: proposals from segmenters, decisions and boxes by the
lexicographer (M2.2). Bounding boxes follow PLAN 7.10."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_agent.pdf_service import (
    SYNC_SEGMENT_MAX_PAGES,
    PdfServiceError,
    annotation_counts,
    page_rows,
    run_segmentation,
)
from elexifier_api import audit
from elexifier_api.authz import get_project_or_404
from elexifier_api.deps import CurrentStorage, CurrentUser, DbSession, State
from elexifier_api.schemas import (
    AnnotationBulk,
    AnnotationCreate,
    AnnotationOut,
    AnnotationPage,
    AnnotationUpdate,
    SegmentOut,
    SegmentRequest,
)
from elexifier_core.db.models import Annotation, AnnotationStatus, Document, ProjectRole
from elexifier_core.events import publish

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
