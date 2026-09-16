"""Parsed documents and their indexed entries."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_api import audit
from elexifier_api.authz import get_project_or_404
from elexifier_api.deps import CurrentStorage, CurrentUser, DbSession, State
from elexifier_api.jobs.documents import download_to_temp, entry_source
from elexifier_api.jobs.pdf import image_key
from elexifier_api.schemas import (
    DocumentCreate,
    DocumentDetailOut,
    DocumentOut,
    DocumentReparse,
    EntryDetailOut,
    EntryOut,
    EntryPage,
    PdfPageOut,
)
from elexifier_core.db.models import Document, DocumentStatus, Entry, File, PdfPage, ProjectRole
from elexifier_core.storage import NotFoundError
from elexifier_pipeline.pdf.layout import PdfplumberLayout

router = APIRouter(prefix="/api/projects/{project_id}", tags=["documents"])

XML_LIKE = ("application/xml", "text/xml", "application/tei+xml")


def is_xml_file(file: File) -> bool:
    name = file.name.lower()
    return file.mime in XML_LIKE or file.mime.endswith("+xml") or name.endswith((".xml", ".tei"))


def is_pdf_file(file: File) -> bool:
    return file.mime == "application/pdf" or file.name.lower().endswith(".pdf")


def document_type_for(file: File) -> str | None:
    if is_xml_file(file):
        return "xml"
    if is_pdf_file(file):
        return "pdf"
    return None


async def create_document_for_file(
    db: AsyncSession,
    state: State,
    file: File,
    *,
    entry_xpath: str | None = None,
    headword_xpath: str | None = None,
) -> Document:
    """Create a queued document for a file and defer its analysis job (XML parse or PDF layout)."""
    doc_type = document_type_for(file) or "xml"
    document = Document(
        project_id=file.project_id,
        source_file_id=file.id,
        type=doc_type,
        status=DocumentStatus.QUEUED.value,
        entry_xpath=entry_xpath,
        headword_xpath=headword_xpath,
    )
    db.add(document)
    await db.flush()
    await defer_parse(state, document)
    return document


async def defer_parse(state: State, document: Document) -> None:
    task = "documents.layout" if document.type == "pdf" else "documents.parse"
    job_id = await state.jobs.configure_task(task, queue="heavy").defer_async(
        document_id=str(document.id)
    )
    document.job_id = int(job_id) if job_id is not None else None


def _out(document: Document, file_name: str, detail: bool = False) -> DocumentOut:
    model = DocumentDetailOut if detail else DocumentOut
    data = {
        k: getattr(document, k)
        for k in DocumentOut.model_fields
        if k not in ("file_name", "page_count")
    }
    data["file_name"] = file_name
    data["page_count"] = (document.stats or {}).get("page_count")
    if detail:
        data["stats"] = document.stats
    return model(**data)


async def _document(
    db: AsyncSession, project_id: uuid.UUID, document_id: uuid.UUID
) -> tuple[Document, File]:
    document = await db.get(Document, document_id)
    if document is None or document.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    file = await db.get(File, document.source_file_id)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source file not found")
    return document, file


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(
    project_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> list[DocumentOut]:
    await get_project_or_404(db, project_id, user)
    rows = (
        await db.execute(
            select(Document, File.name)
            .join(File, File.id == Document.source_file_id)
            .where(Document.project_id == project_id)
            .order_by(Document.created_at.desc())
        )
    ).all()
    return [_out(d, name) for d, name in rows]


@router.post("/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def create_document(
    project_id: uuid.UUID, body: DocumentCreate, db: DbSession, user: CurrentUser, state: State
) -> DocumentOut:
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    file = await db.get(File, body.file_id)
    if file is None or file.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    if document_type_for(file) is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Only XML and PDF files can be analysed"
        )
    document = await create_document_for_file(
        db, state, file, entry_xpath=body.entry_xpath, headword_xpath=body.headword_xpath
    )
    audit.record(
        db, user.id, "document.created", document.id, project=str(project_id), file=str(file.id)
    )
    await db.flush()
    await db.refresh(document)
    return _out(document, file.name)


@router.get("/documents/{document_id}", response_model=DocumentDetailOut)
async def get_document(
    project_id: uuid.UUID, document_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> DocumentDetailOut:
    await get_project_or_404(db, project_id, user)
    document, file = await _document(db, project_id, document_id)
    out = _out(document, file.name, detail=True)
    assert isinstance(out, DocumentDetailOut)
    return out


@router.post("/documents/{document_id}/reparse", response_model=DocumentOut)
async def reparse_document(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    body: DocumentReparse,
    db: DbSession,
    user: CurrentUser,
    state: State,
) -> DocumentOut:
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    document, file = await _document(db, project_id, document_id)
    if document.status == DocumentStatus.PARSING.value:
        raise HTTPException(status.HTTP_409_CONFLICT, "The document is being parsed right now")
    if body.entry_xpath is not None:
        document.entry_xpath = body.entry_xpath or None
    if body.headword_xpath is not None:
        document.headword_xpath = body.headword_xpath or None
    document.status = DocumentStatus.QUEUED.value
    document.error = None
    await defer_parse(state, document)
    audit.record(db, user.id, "document.reparse", document.id, entry_xpath=document.entry_xpath)
    await db.flush()
    await db.refresh(document)
    return _out(document, file.name)


@router.get("/documents/{document_id}/pages", response_model=list[PdfPageOut])
async def list_pages(
    project_id: uuid.UUID, document_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> list[PdfPageOut]:
    await get_project_or_404(db, project_id, user)
    await _document(db, project_id, document_id)
    rows = (
        (
            await db.execute(
                select(PdfPage).where(PdfPage.document_id == document_id).order_by(PdfPage.page_no)
            )
        )
        .scalars()
        .all()
    )
    return [PdfPageOut.model_validate(p) for p in rows]


async def _page(db: AsyncSession, project_id: uuid.UUID, document_id: uuid.UUID, page_no: int):
    document, file = await _document(db, project_id, document_id)
    page = (
        await db.execute(
            select(PdfPage).where(PdfPage.document_id == document_id, PdfPage.page_no == page_no)
        )
    ).scalar_one_or_none()
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    return document, file, page


@router.get("/documents/{document_id}/pages/{page_no}/layout")
async def get_page_layout(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    page_no: int,
    db: DbSession,
    user: CurrentUser,
    storage: CurrentStorage,
) -> Response:
    """The extracted layout of one page (words, lines, blocks; PLAN 7.7 and 7.10)."""
    await get_project_or_404(db, project_id, user)
    _document_row, _file, page = await _page(db, project_id, document_id, page_no)
    try:
        body = b"".join([chunk async for chunk in storage.open(page.layout_s3_key)])
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Layout not found") from exc
    return Response(content=body, media_type="application/json")


@router.get("/documents/{document_id}/pages/{page_no}/image")
async def get_page_image(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    page_no: int,
    db: DbSession,
    user: CurrentUser,
    storage: CurrentStorage,
    dpi: int = Query(default=110, ge=36, le=300),
) -> Response:
    """PNG rendering of one page, rendered once per resolution and kept in storage."""
    await get_project_or_404(db, project_id, user)
    document, file, _page_row = await _page(db, project_id, document_id, page_no)
    key = image_key(project_id, document.id, page_no, dpi)
    try:
        png = b"".join([chunk async for chunk in storage.open(key)])
    except NotFoundError:
        path = await download_to_temp(storage, file.s3_key, suffix=".pdf")
        try:
            png = await asyncio.to_thread(PdfplumberLayout().render_page, path, page_no, dpi)
        finally:
            path.unlink(missing_ok=True)
        await storage.put(key, png, content_type="image/png")
    return Response(
        content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"}
    )


@router.get("/documents/{document_id}/entries", response_model=EntryPage)
async def list_entries(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    q: str | None = Query(default=None, max_length=200),
) -> EntryPage:
    await get_project_or_404(db, project_id, user)
    await _document(db, project_id, document_id)
    base = select(Entry).where(Entry.document_id == document_id)
    count = select(func.count()).select_from(Entry).where(Entry.document_id == document_id)
    if q:
        pattern = f"%{q.strip()}%"
        condition = Entry.headword.ilike(pattern) | Entry.snippet.ilike(pattern)
        base = base.where(condition)
        count = count.where(condition)
    total = (await db.execute(count)).scalar_one()
    rows = (
        (await db.execute(base.order_by(Entry.ordinal).offset(offset).limit(limit))).scalars().all()
    )
    return EntryPage(
        items=[EntryOut.model_validate(e) for e in rows], total=total, offset=offset, limit=limit
    )


@router.get("/entries/{entry_id}", response_model=EntryDetailOut)
async def get_entry(
    project_id: uuid.UUID,
    entry_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    storage: CurrentStorage,
) -> EntryDetailOut:
    await get_project_or_404(db, project_id, user)
    entry = await db.get(Entry, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entry not found")
    document = await db.get(Document, entry.document_id)
    if document is None or document.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entry not found")
    source = await entry_source(storage, db, entry)
    return EntryDetailOut(
        id=entry.id,
        document_id=entry.document_id,
        ordinal=entry.ordinal,
        headword=entry.headword,
        snippet=entry.snippet,
        source_xml=source.decode("utf-8", errors="replace"),
        source_ref=entry.source_ref,
    )
