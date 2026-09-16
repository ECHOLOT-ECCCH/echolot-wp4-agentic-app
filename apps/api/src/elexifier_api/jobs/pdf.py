"""PDF layout job: extract every page's layout with pdfplumber, store one JSON per page in
storage (kind ``derived``), record the pages, and publish progress."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import delete

from elexifier_api.jobs.documents import _set_status, download_to_temp
from elexifier_api.jobs.runtime import job_database, job_storage
from elexifier_core.db.models import Document, DocumentStatus, File, PdfPage
from elexifier_core.events import publish
from elexifier_core.jobs import QUEUE_HEAVY, app
from elexifier_core.storage import make_key
from elexifier_pipeline.pdf.layout import PdfplumberLayout
from elexifier_pipeline.pdf.models import PageLayout

log = structlog.get_logger(__name__)

CHUNK_PAGES = 10
PROGRESS_EVERY_PAGES = 10


class LayoutError(Exception):
    pass


def layout_key(project_id: uuid.UUID, document_id: uuid.UUID, page_no: int) -> str:
    return make_key(str(project_id), "derived", str(document_id), f"page-{page_no:05d}.json")


def image_key(project_id: uuid.UUID, document_id: uuid.UUID, page_no: int, dpi: int) -> str:
    return make_key(
        str(project_id), "derived", str(document_id), f"page-{page_no:05d}-{dpi}dpi.png"
    )


async def extract_layout(document_id: str | uuid.UUID) -> dict[str, Any]:
    """The job body; callable directly (tests) or through the Procrastinate task."""
    db = job_database()
    storage = job_storage()
    doc_id = uuid.UUID(str(document_id))
    async with db.session() as session:
        document = await session.get(Document, doc_id)
        if document is None:
            raise LayoutError(f"document {doc_id} does not exist")
        file = await session.get(File, document.source_file_id)
        if file is None:
            raise LayoutError("source file is gone")
        project_id, key = document.project_id, file.s3_key
    await _set_status(db, doc_id, DocumentStatus.PARSING.value, error=None)
    path: Path | None = None
    try:
        path = await download_to_temp(storage, key, suffix=".pdf")
        extractor = PdfplumberLayout()
        page_count = await asyncio.to_thread(extractor.page_count, path)
        if page_count == 0:
            raise LayoutError("the PDF has no pages")
        async with db.session() as session:
            await session.execute(delete(PdfPage).where(PdfPage.document_id == doc_id))
            await session.commit()
        scanned = 0
        text_chars = 0
        lines = 0
        done = 0
        pdf_path = path
        for start in range(1, page_count + 1, CHUNK_PAGES):
            numbers = list(range(start, min(start + CHUNK_PAGES, page_count + 1)))
            layouts: list[PageLayout] = await asyncio.to_thread(
                lambda numbers=numbers: list(extractor.extract(pdf_path, pages=numbers))
            )
            async with db.session() as session:
                for layout in layouts:
                    page_key = layout_key(project_id, doc_id, layout.page)
                    await storage.put(
                        page_key,
                        layout.model_dump_json().encode("utf-8"),
                        content_type="application/json",
                    )
                    session.add(
                        PdfPage(
                            document_id=doc_id,
                            page_no=layout.page,
                            width=layout.width,
                            height=layout.height,
                            rotation=layout.rotation,
                            layout_s3_key=page_key,
                            text_chars=layout.text_chars,
                            is_scanned=layout.is_scanned,
                            line_count=len(layout.lines),
                        )
                    )
                    scanned += int(layout.is_scanned)
                    text_chars += layout.text_chars
                    lines += len(layout.lines)
                    done += 1
                await publish(
                    session,
                    project_id,
                    "job.progress",
                    {
                        "kind": "documents.layout",
                        "document_id": str(doc_id),
                        "done": done,
                        "total": page_count,
                    },
                )
                await session.commit()
        stats = {
            "page_count": page_count,
            "scanned_pages": scanned,
            "text_chars": text_chars,
            "lines": lines,
            "layout": "pdfplumber",
        }
        await _set_status(
            db,
            doc_id,
            DocumentStatus.READY.value,
            entry_count=0,
            stats=stats,
            parsed_at=datetime.now(UTC),
            error=None,
        )
        log.info("document.layout", document_id=str(doc_id), pages=page_count, scanned=scanned)
        return {"document_id": str(doc_id), "pages": page_count, "scanned_pages": scanned}
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"[:2000]
        log.warning("document.layout_failed", document_id=str(doc_id), error=message)
        await _set_status(db, doc_id, DocumentStatus.ERROR.value, error=message)
        raise
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


@app.task(name="documents.layout", queue=QUEUE_HEAVY, pass_context=False)
async def extract_layout_task(document_id: str) -> dict[str, Any]:
    return await extract_layout(document_id)
