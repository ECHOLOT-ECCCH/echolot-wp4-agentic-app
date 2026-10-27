"""Workspace files: streaming upload (normalized to records), download with ranges, small-file
content for previews, and pages of records (docs/PLAN.md section 9)."""

from __future__ import annotations

import mimetypes
import re
import uuid
from collections.abc import AsyncIterator
from urllib.parse import quote, unquote

import anyio
import structlog
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from echolot_app.db import File, FileKind
from echolot_app.deps import CurrentStorage, DbSession, get_workspace_or_404
from echolot_app.records import RecordsError, RecordsReader, detect_format, normalize
from echolot_app.schemas import FileOut, RecordOut, RecordsPage
from echolot_app.storage import LocalStorage, file_key, safe_filename

router = APIRouter(prefix="/api/workspaces/{workspace_id}/files", tags=["files"])
log = structlog.get_logger(__name__)

MAX_UPLOAD_BYTES = 500 * 1024 * 1024
MAX_PREVIEW_BYTES = 5 * 1024 * 1024
MAX_PAGE = 200
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_MIME_BY_EXTENSION = {
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".tab": "text/tab-separated-values",
    ".jsonl": "application/x-ndjson",
    ".ndjson": "application/x-ndjson",
    ".json": "application/json",
    ".md": "text/markdown",
    ".py": "text/x-python",
    ".txt": "text/plain",
}


class UploadTooLarge(HTTPException):
    def __init__(self, limit_mb: int) -> None:
        super().__init__(
            status.HTTP_413_CONTENT_TOO_LARGE, f"Upload exceeds the limit of {limit_mb} MB"
        )


async def _limited(stream: AsyncIterator[bytes], limit: int) -> AsyncIterator[bytes]:
    total = 0
    async for chunk in stream:
        total += len(chunk)
        if total > limit:
            raise UploadTooLarge(limit // (1024 * 1024))
        yield chunk


def guess_mime(name: str, declared: str | None = None) -> str:
    lowered = name.lower()
    for extension, mime in _MIME_BY_EXTENSION.items():
        if lowered.endswith(extension):
            return mime
    if declared and declared not in ("application/octet-stream", "multipart/form-data"):
        return declared.split(";")[0].strip()
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def file_dir(record: File) -> str:
    return record.rel_path.rsplit("/", 1)[0]


async def get_file_or_404(db: AsyncSession, workspace_id: uuid.UUID, file_id: uuid.UUID) -> File:
    record = await db.get(File, file_id)
    if record is None or record.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return record


def reader_for(storage: LocalStorage, record: File) -> RecordsReader:
    if not record.meta.get("format") or "record_count" not in record.meta:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{record.name} is not a records file (CSV, TSV, JSON, JSONL)"
        )
    return RecordsReader(storage.local_path(record.rel_path), int(record.meta["record_count"]))


async def store_upload(
    db: AsyncSession,
    storage: LocalStorage,
    workspace_id: uuid.UUID,
    filename: str,
    stream: AsyncIterator[bytes],
    declared: str | None = None,
    kind: FileKind = FileKind.INPUT,
) -> File:
    """Stream a file to storage, normalize it to records when it has a records format."""
    file_id = uuid.uuid4()
    filename = safe_filename(filename)
    mime = guess_mime(filename, declared)
    key = file_key(str(workspace_id), str(file_id), filename)
    try:
        stat = await storage.put(key, _limited(stream, MAX_UPLOAD_BYTES), content_type=mime)
    except UploadTooLarge:
        await storage.delete_prefix(key.rsplit("/", 1)[0])
        raise
    if stat.size == 0:
        await storage.delete_prefix(key.rsplit("/", 1)[0])
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty upload")

    meta: dict[str, object] = {}
    fmt = detect_format(filename)
    if fmt is not None:
        try:
            found = await anyio.to_thread.run_sync(normalize, storage.local_path(key), fmt)
            meta = found.as_dict()
        except RecordsError as exc:
            meta = {"format": None, "error": str(exc)}
        except Exception as exc:  # a broken file must not lose the upload
            log.warning("records.normalize_failed", file=filename, error=str(exc))
            meta = {"format": None, "error": f"could not read the file: {type(exc).__name__}"}

    record = File(
        id=file_id,
        workspace_id=workspace_id,
        kind=kind.value,
        name=filename,
        rel_path=key,
        size=stat.size,
        sha256=stat.sha256,
        mime=mime,
        meta=meta,
    )
    db.add(record)
    await db.flush()
    return record


@router.get("", response_model=list[FileOut])
async def list_files(
    workspace_id: uuid.UUID, db: DbSession, kind: FileKind | None = None
) -> list[FileOut]:
    await get_workspace_or_404(db, workspace_id)
    query = select(File).where(File.workspace_id == workspace_id).order_by(File.created_at.desc())
    if kind:
        query = query.where(File.kind == kind.value)
    return [FileOut.model_validate(f) for f in (await db.execute(query)).scalars()]


@router.post("", response_model=FileOut, status_code=status.HTTP_201_CREATED)
async def upload_file(
    workspace_id: uuid.UUID,
    request: Request,
    db: DbSession,
    storage: CurrentStorage,
    name: str | None = Query(default=None, description="File name for raw body uploads"),
) -> FileOut:
    """Upload a file as the raw request body (``curl --data-binary``, the web app's XHR), with
    the file name in ``?name=`` or the ``X-File-Name`` header (percent-encoded)."""
    await get_workspace_or_404(db, workspace_id)
    header_name = request.headers.get("x-file-name")
    raw_name = name or (unquote(header_name) if header_name else None)
    if not raw_name:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Provide the file name in ?name= or X-File-Name"
        )
    record = await store_upload(
        db, storage, workspace_id, raw_name, request.stream(), request.headers.get("content-type")
    )
    return FileOut.model_validate(record)


@router.get("/{file_id}", response_model=FileOut)
async def get_file(workspace_id: uuid.UUID, file_id: uuid.UUID, db: DbSession) -> FileOut:
    return FileOut.model_validate(await get_file_or_404(db, workspace_id, file_id))


def _parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    if not header:
        return None
    match = _RANGE.match(header.strip())
    if not match:
        raise HTTPException(status.HTTP_416_RANGE_NOT_SATISFIABLE, "Bad Range header")
    start_s, end_s = match.groups()
    if start_s == "" and end_s == "":
        raise HTTPException(status.HTTP_416_RANGE_NOT_SATISFIABLE, "Bad Range header")
    if start_s == "":
        length = min(int(end_s), size)
        start, end = size - length, size - 1
    else:
        start = int(start_s)
        end = min(int(end_s), size - 1) if end_s else size - 1
    if start >= size or start > end:
        raise HTTPException(status.HTTP_416_RANGE_NOT_SATISFIABLE, "Range out of bounds")
    return start, end


@router.get("/{file_id}/download")
async def download_file(
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
    request: Request,
    db: DbSession,
    storage: CurrentStorage,
    inline: bool = False,
) -> StreamingResponse:
    record = await get_file_or_404(db, workspace_id, file_id)
    byte_range = _parse_range(request.headers.get("range"), record.size)
    disposition = "inline" if inline else "attachment"
    headers = {
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(record.name)}",
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=0",
    }
    if record.sha256:
        headers["ETag"] = f'"{record.sha256}"'
    status_code = status.HTTP_200_OK
    length = record.size
    if byte_range is not None:
        start, end = byte_range
        length = end - start + 1
        headers["Content-Range"] = f"bytes {start}-{end}/{record.size}"
        status_code = status.HTTP_206_PARTIAL_CONTENT
    headers["Content-Length"] = str(length)
    await storage.stat(record.rel_path)  # 404 through the app's NotFoundError handler
    return StreamingResponse(
        storage.open(record.rel_path, byte_range),
        status_code=status_code,
        media_type=record.mime,
        headers=headers,
    )


@router.get("/{file_id}/content")
async def file_content(
    workspace_id: uuid.UUID, file_id: uuid.UUID, db: DbSession, storage: CurrentStorage
) -> Response:
    """Whole-file content for the preview pane; refused above the preview size cap."""
    record = await get_file_or_404(db, workspace_id, file_id)
    if record.size > MAX_PREVIEW_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "File is too large for a whole-file preview; use the records view",
        )
    data = b"".join([c async for c in storage.open(record.rel_path)])
    media_type = record.mime or "application/octet-stream"
    if media_type.startswith("text/") or media_type.endswith(("json", "ndjson", "xml")):
        media_type = f"{media_type}; charset=utf-8"
    return Response(data, media_type=media_type, headers={"X-File-Size": str(record.size)})


@router.get("/{file_id}/records", response_model=RecordsPage)
async def file_records(
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
    db: DbSession,
    storage: CurrentStorage,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
) -> RecordsPage:
    record = await get_file_or_404(db, workspace_id, file_id)
    reader = reader_for(storage, record)
    page = await anyio.to_thread.run_sync(reader.page, offset, limit)
    return RecordsPage(
        total=reader.record_count,
        offset=offset,
        columns=list(record.meta.get("columns") or []),
        records=[RecordOut(row=row, record=data) for row, data in page],
    )


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    workspace_id: uuid.UUID, file_id: uuid.UUID, db: DbSession, storage: CurrentStorage
) -> Response:
    record = await get_file_or_404(db, workspace_id, file_id)
    await db.delete(record)
    await db.flush()
    await storage.delete_prefix(file_dir(record))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
