"""What the lexicographer has open in the middle pane, described for the model (one line)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_core.db.models import ConversionRun, ConvertedEntry, Document, Entry, File

Kind = Literal["file", "document", "entry", "run", "span", "mapping", "spec", "profile", "none"]


class Viewing(BaseModel):
    """The browser's preview target, as sent with a chat message (unknown fields ignored)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    kind: Kind = "none"
    file_id: uuid.UUID | None = Field(default=None, alias="fileId")
    document_id: uuid.UUID | None = Field(default=None, alias="documentId")
    entry_id: uuid.UUID | None = Field(default=None, alias="entryId")
    run_id: uuid.UUID | None = Field(default=None, alias="runId")
    converted_id: uuid.UUID | None = Field(default=None, alias="convertedId")
    version: int | None = None
    page: int | None = Field(default=None, ge=1)


def _run_label(run: ConversionRun) -> str:
    kind = "sample" if run.kind == "sample" else "full"
    if run.mapping_version:
        return f"{kind} run with mapping v{run.mapping_version} ({run.status})"
    structurer = (run.scope or {}).get("structurer") or "structuring"
    return f"{kind} {structurer} structuring run ({run.status})"


async def describe_viewing(
    db: AsyncSession, project_id: uuid.UUID, viewing: Viewing
) -> tuple[str, dict[str, Any]] | None:
    """A short description and the ids behind it, or None when nothing (or nothing of this
    project) is open."""
    data: dict[str, Any] = {"kind": viewing.kind}
    if viewing.kind == "entry" and viewing.entry_id:
        entry = await db.get(Entry, viewing.entry_id)
        document = await db.get(Document, entry.document_id) if entry else None
        if entry is None or document is None or document.project_id != project_id:
            return None
        source = await db.get(File, document.source_file_id)
        text = f"entry {entry.ordinal + 1}"
        if entry.headword:
            text += f" \u201c{entry.headword}\u201d"
        if source is not None:
            text += f" of {source.name}"
        data.update(entry_id=str(entry.id), ordinal=entry.ordinal, headword=entry.headword)
        if viewing.run_id:
            run = await db.get(ConversionRun, viewing.run_id)
            if run is not None and run.project_id == project_id:
                text += f", output of the {_run_label(run)}"
                data["run_id"] = str(run.id)
        else:
            text += ", converted live with the latest mapping"
        return text, data
    if viewing.kind == "span" and viewing.converted_id:
        row = await db.get(ConvertedEntry, viewing.converted_id)
        run = await db.get(ConversionRun, row.run_id) if row else None
        if row is None or run is None or run.project_id != project_id:
            return None
        text = f"structured PDF entry \u201c{row.headword or '?'}\u201d from the {_run_label(run)}"
        data.update(converted_id=str(row.id), run_id=str(run.id), headword=row.headword)
        return text, data
    if viewing.kind == "run" and viewing.run_id:
        run = await db.get(ConversionRun, viewing.run_id)
        if run is None or run.project_id != project_id:
            return None
        data["run_id"] = str(run.id)
        return f"the {_run_label(run)}", data
    if viewing.kind == "document" and viewing.document_id:
        document = await db.get(Document, viewing.document_id)
        if document is None or document.project_id != project_id:
            return None
        source = await db.get(File, document.source_file_id)
        name = source.name if source is not None else "the document"
        data["document_id"] = str(document.id)
        if document.type == "pdf" and viewing.page:
            data["page"] = viewing.page
            return f"page {viewing.page} of {name}", data
        return f"{name} ({document.entry_count:,} entries)" if document.entry_count else name, data
    if viewing.kind == "file" and viewing.file_id:
        file = await db.get(File, viewing.file_id)
        if file is None or file.project_id != project_id:
            return None
        data["file_id"] = str(file.id)
        if viewing.page:
            data["page"] = viewing.page
            return f"page {viewing.page} of {file.name}", data
        return f"the file {file.name}", data
    if viewing.kind == "mapping":
        if viewing.version:
            data["version"] = viewing.version
            return f"the mapping editor, version {viewing.version}", data
        return "the mapping editor (latest version)", data
    if viewing.kind == "spec":
        return "the conversion specification", data
    if viewing.kind == "profile":
        return "the structure profile", data
    return None


__all__ = ["Viewing", "describe_viewing"]
