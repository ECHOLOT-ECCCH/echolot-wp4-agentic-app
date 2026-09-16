"""Parse an uploaded XML file: profile it, pick the entry element, index every entry."""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_api.jobs.runtime import job_database, job_storage
from elexifier_core.db.models import Document, DocumentStatus, Entry, File
from elexifier_core.db.session import Database
from elexifier_core.events import publish
from elexifier_core.jobs import QUEUE_HEAVY, app
from elexifier_core.storage import Storage
from elexifier_pipeline.xml import index as xml_index
from elexifier_pipeline.xml.streaming import simple_tag

log = structlog.get_logger(__name__)
BATCH = 2000
PROGRESS_EVERY = 10_000


class ParseError(Exception):
    pass


async def download_to_temp(storage: Storage, key: str, suffix: str = ".xml") -> Path:
    with tempfile.NamedTemporaryFile(prefix="elexifier-", suffix=suffix, delete=False) as handle:
        async for chunk in storage.open(key):
            handle.write(chunk)
        return Path(handle.name)


def _entry_tag(document: Document, profile: xml_index.XmlProfile) -> tuple[str, str]:
    """(entry_xpath, local tag) from the document's setting or the best candidate."""
    if document.entry_xpath:
        parsed = simple_tag(document.entry_xpath)
        if parsed is None:
            raise ParseError(
                f"entry XPath {document.entry_xpath!r} must end in a plain element name"
            )
        return document.entry_xpath, parsed[1]
    if not profile.entry_candidates:
        raise ParseError("no repeated element looks like a dictionary entry")
    best = profile.entry_candidates[0]
    return best.xpath, best.tag


async def _set_status(
    db: Database, document_id: uuid.UUID, status: str, **fields: Any
) -> Document | None:
    async with db.session() as session:
        document = await session.get(Document, document_id)
        if document is None:
            return None
        document.status = status
        for key, value in fields.items():
            setattr(document, key, value)
        event = {
            DocumentStatus.READY.value: "document.ready",
            DocumentStatus.ERROR.value: "document.error",
        }.get(status)
        if event:
            await publish(
                session,
                document.project_id,
                event,
                {
                    "document_id": str(document.id),
                    "status": status,
                    "error": document.error,
                    "entry_count": document.entry_count,
                },
            )
        await session.commit()
        return document


async def parse_document(document_id: str | uuid.UUID) -> dict[str, Any]:
    """The job body; callable directly (tests) or through the Procrastinate task."""
    db = job_database()
    storage = job_storage()
    doc_id = uuid.UUID(str(document_id))
    async with db.session() as session:
        document = await session.get(Document, doc_id)
        if document is None:
            raise ParseError(f"document {doc_id} does not exist")
        file = await session.get(File, document.source_file_id)
        if file is None:
            raise ParseError("source file is gone")
        project_id = document.project_id
        key, file_size = file.s3_key, file.size
    await _set_status(db, doc_id, DocumentStatus.PARSING.value, error=None)
    path: Path | None = None
    try:
        path = await download_to_temp(storage, key)
        profile = await asyncio.to_thread(xml_index.profile_xml, path, size_bytes=file_size)
        async with db.session() as session:
            document = await session.get(Document, doc_id)
            assert document is not None
            entry_xpath, entry_tag = _entry_tag(document, profile)
            headword_xpath = document.headword_xpath
        count = await _index(db, doc_id, project_id, path, entry_tag, profile, headword_xpath)
        await _set_status(
            db,
            doc_id,
            DocumentStatus.READY.value,
            entry_xpath=entry_xpath,
            entry_count=count,
            stats=profile.model_dump(),
            parsed_at=datetime.now(UTC),
            error=None,
        )
        log.info("document.parsed", document_id=str(doc_id), entries=count, entry_tag=entry_tag)
        return {"document_id": str(doc_id), "entries": count, "entry_xpath": entry_xpath}
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"[:2000]
        log.warning("document.parse_failed", document_id=str(doc_id), error=message)
        await _set_status(db, doc_id, DocumentStatus.ERROR.value, error=message)
        raise
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


async def _index(
    db: Database,
    doc_id: uuid.UUID,
    project_id: uuid.UUID,
    path: Path,
    entry_tag: str,
    profile: xml_index.XmlProfile,
    headword_xpath: str | None,
) -> int:
    async with db.session() as session:
        await session.execute(delete(Entry).where(Entry.document_id == doc_id))
        await session.commit()
    refs = await asyncio.to_thread(
        lambda: list(
            xml_index.index_entries(
                path,
                entry_tag,
                namespaces=profile.namespaces or None,
                headword_xpath=headword_xpath,
            )
        )
    )
    async with db.session() as session:
        for offset in range(0, len(refs), BATCH):
            batch = refs[offset : offset + BATCH]
            await session.execute(
                insert(Entry),
                [
                    {
                        "id": uuid.uuid4(),
                        "document_id": doc_id,
                        "ordinal": ref.ordinal,
                        "headword": (ref.headword or None) and ref.headword[:300],
                        "source_ref": {"start": ref.start, "end": ref.end},
                        "snippet": ref.snippet[:400],
                    }
                    for ref in batch
                ],
            )
            done = offset + len(batch)
            if done % PROGRESS_EVERY < BATCH or done == len(refs):
                await publish(
                    session,
                    project_id,
                    "job.progress",
                    {
                        "kind": "documents.parse",
                        "document_id": str(doc_id),
                        "done": done,
                        "total": len(refs),
                    },
                )
                await session.commit()
        await session.commit()
    return len(refs)


@app.task(name="documents.parse", queue=QUEUE_HEAVY, pass_context=False)
async def parse_document_task(document_id: str) -> dict[str, Any]:
    return await parse_document(document_id)


async def entry_source(storage: Storage, session: AsyncSession, entry: Entry) -> bytes:
    """The entry's source XML fetched from storage by byte range."""
    document = await session.get(Document, entry.document_id)
    assert document is not None
    file = await session.get(File, document.source_file_id)
    assert file is not None
    start, end = int(entry.source_ref["start"]), int(entry.source_ref["end"])
    chunks = [c async for c in storage.open(file.s3_key, (start, end - 1))]
    return b"".join(chunks)
