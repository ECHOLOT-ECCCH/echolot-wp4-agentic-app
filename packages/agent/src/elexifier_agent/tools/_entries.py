"""Shared helpers for tools that read documents and entries."""

from __future__ import annotations

import random
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lxml import etree
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_agent.deps import AgentDeps
from elexifier_core.db.models import Document, DocumentStatus, Entry, File
from elexifier_core.storage import Storage
from elexifier_pipeline.xml.index import parse_fragment

MAX_SAMPLE_ENTRIES = 20
MAX_ENTRY_CHARS = 4000
RANGE_READ_THRESHOLD = 40  # above this many entries, download the file once instead of ranging


class ToolInputError(ValueError):
    """Bad arguments from the model; reported back as a tool result, not raised to the user."""


async def get_document(deps: AgentDeps, db: AsyncSession, document_id: str | None) -> Document:
    """The requested document, or the project's only ready document when the id is omitted."""
    if document_id:
        try:
            doc_uuid = uuid.UUID(document_id)
        except ValueError as exc:
            raise ToolInputError(f"document_id {document_id!r} is not a valid id") from exc
        document = await db.get(Document, doc_uuid)
        if document is None or document.project_id != deps.project.id:
            raise ToolInputError("no such document in this project; call list_files")
        return document
    ready = (
        (
            await db.execute(
                select(Document)
                .where(
                    Document.project_id == deps.project.id,
                    Document.status == DocumentStatus.READY.value,
                )
                .order_by(Document.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    if not ready:
        raise ToolInputError(
            "no analysed document yet; the lexicographer must upload an XML file "
            "(it is analysed automatically)"
        )
    if len(ready) > 1:
        names = ", ".join(str(d.id) for d in ready)
        raise ToolInputError(f"several documents are ready; pass document_id (one of: {names})")
    return ready[0]


@dataclass
class EntryReader:
    """Reads entry source XML by byte range, from storage or from a one-off local copy."""

    storage: Storage
    key: str
    _local: Path | None = None

    async def prepare_bulk(self) -> None:
        if self._local is None:
            with tempfile.NamedTemporaryFile(
                prefix="elexifier-", suffix=".xml", delete=False
            ) as fh:
                async for chunk in self.storage.open(self.key):
                    fh.write(chunk)
                self._local = Path(fh.name)

    async def read(self, entry: Entry) -> bytes:
        start, end = int(entry.source_ref["start"]), int(entry.source_ref["end"])
        if self._local is not None:
            with self._local.open("rb") as fh:
                fh.seek(start)
                return fh.read(end - start)
        return b"".join([c async for c in self.storage.open(self.key, (start, end - 1))])

    def close(self) -> None:
        if self._local is not None:
            self._local.unlink(missing_ok=True)
            self._local = None


async def reader_for(deps: AgentDeps, db: AsyncSession, document: Document) -> EntryReader:
    file = await db.get(File, document.source_file_id)
    if file is None:
        raise ToolInputError("the source file of this document is gone")
    return EntryReader(deps.storage, file.s3_key)


async def pick_entries(
    db: AsyncSession,
    document: Document,
    *,
    n: int,
    strategy: str = "first",
    ordinals: Sequence[int] | None = None,
    query: str | None = None,
) -> list[Entry]:
    n = max(1, min(int(n), MAX_SAMPLE_ENTRIES))
    base = select(Entry).where(Entry.document_id == document.id)
    if ordinals:
        wanted = [int(o) for o in ordinals][:MAX_SAMPLE_ENTRIES]
        rows = (await db.execute(base.where(Entry.ordinal.in_(wanted)))).scalars().all()
        return sorted(rows, key=lambda e: e.ordinal)
    if query:
        pattern = f"%{query.strip()}%"
        base = base.where(Entry.headword.ilike(pattern) | Entry.snippet.ilike(pattern))
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    if total == 0:
        return []
    if strategy == "random":
        picks = sorted(random.sample(range(total), min(n, total)))
    elif strategy == "spread":
        step = max(1, total // n)
        picks = list(range(0, total, step))[:n]
    else:
        picks = list(range(min(n, total)))
    # ordinals are dense per document, so offsets map to ordinals unless a query filters rows
    if query:
        rows = (await db.execute(base.order_by(Entry.ordinal))).scalars().all()
        return [rows[i] for i in picks if i < len(rows)]
    rows = (
        (await db.execute(base.where(Entry.ordinal.in_(picks)).order_by(Entry.ordinal)))
        .scalars()
        .all()
    )
    return list(rows)


def parse_entry(fragment: bytes, document: Document) -> etree._Element:
    namespaces = (document.stats or {}).get("namespaces") or None
    return parse_fragment(fragment, namespaces)


def clip(text: str, limit: int = MAX_ENTRY_CHARS) -> tuple[str, bool]:
    return (text[:limit], True) if len(text) > limit else (text, False)
