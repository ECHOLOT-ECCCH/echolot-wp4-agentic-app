"""Project files: streaming upload, download with ranges, small-file content for previews."""

from __future__ import annotations

import mimetypes
import re
import uuid
from collections.abc import AsyncIterator
from typing import Literal
from urllib.parse import quote, unquote

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from elexifier_api import audit
from elexifier_api.authz import get_project_or_404
from elexifier_api.deps import CurrentSettings, CurrentStorage, CurrentUser, DbSession, State
from elexifier_api.routers.documents import create_document_for_file, is_xml_file
from elexifier_api.schemas import FileOut
from elexifier_core.db.models import File, FileKind, ProjectRole
from elexifier_core.storage import NotFoundError, make_key, safe_filename

router = APIRouter(prefix="/api/projects/{project_id}/files", tags=["files"])

_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
UPLOADABLE_KINDS: tuple[str, ...] = (FileKind.SOURCE.value, FileKind.DERIVED.value)


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


def _guess_mime(name: str, declared: str | None) -> str:
    if declared and declared not in ("application/octet-stream", "multipart/form-data"):
        return declared.split(";")[0].strip()
    guessed, _ = mimetypes.guess_type(name)
    if guessed:
        return guessed
    lowered = name.lower()
    if lowered.endswith((".xml", ".tei")):
        return "application/xml"
    if lowered.endswith(".nvh"):
        return "text/plain"
    return "application/octet-stream"


@router.get("", response_model=list[FileOut])
async def list_files(
    project_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    kind: Literal["source", "derived", "output", "artifact"] | None = None,
) -> list[FileOut]:
    await get_project_or_404(db, project_id, user)
    query = select(File).where(File.project_id == project_id).order_by(File.created_at.desc())
    if kind:
        query = query.where(File.kind == kind)
    rows = (await db.execute(query)).scalars().all()
    return [FileOut.model_validate(f) for f in rows]


@router.post("", response_model=FileOut, status_code=status.HTTP_201_CREATED)
async def upload_file(
    project_id: uuid.UUID,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    storage: CurrentStorage,
    settings: CurrentSettings,
    state: State,
    name: str | None = Query(default=None, description="File name for raw body uploads"),
    kind: Literal["source", "derived"] = "source",
) -> FileOut:
    """Upload a file.

    Two encodings are accepted:
    * raw body (``Content-Type: application/octet-stream`` or the file's own type) with the file
      name in ``?name=`` or the ``X-File-Name`` header (percent-encoded); this streams straight
      to storage and is what the web app and ``curl --data-binary`` use;
    * ``multipart/form-data`` with a single ``file`` part.
    """
    project, _ = await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    limit = settings.max_upload_mb * 1024 * 1024
    content_type = request.headers.get("content-type", "")
    file_id = uuid.uuid4()

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None or isinstance(upload, str):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "multipart body needs a 'file' part")
        filename = safe_filename(upload.filename or "upload")
        declared = upload.content_type

        async def multipart_chunks() -> AsyncIterator[bytes]:
            while chunk := await upload.read(1024 * 1024):
                yield chunk

        stream: AsyncIterator[bytes] = multipart_chunks()
    else:
        header_name = request.headers.get("x-file-name")
        raw_name = name or (unquote(header_name) if header_name else None)
        if not raw_name:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Provide the file name in ?name= or X-File-Name"
            )
        filename = safe_filename(raw_name)
        declared = content_type or None
        stream = request.stream()

    mime = _guess_mime(filename, declared)
    key = make_key(str(project.id), kind, str(file_id), filename)
    try:
        stat = await storage.put(key, _limited(stream, limit), content_type=mime)
    except UploadTooLarge:
        await storage.delete(key)
        raise
    if stat.size == 0:
        await storage.delete(key)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty upload")

    record = File(
        id=file_id,
        project_id=project.id,
        kind=kind,
        name=filename,
        s3_key=key,
        size=stat.size,
        sha256=stat.sha256 or "",
        mime=mime,
        created_by=user.id,
    )
    db.add(record)
    audit.record(
        db,
        user.id,
        "file.uploaded",
        file_id,
        project=str(project.id),
        name=filename,
        size=stat.size,
    )
    await db.flush()
    if kind == FileKind.SOURCE.value and project.source_type == "xml" and is_xml_file(record):
        # XML sources are parsed right away so the entry index is ready when the chat starts
        await create_document_for_file(db, state, record)
    await db.refresh(record)
    return FileOut.model_validate(record)


async def _get_file(db: DbSession, project_id: uuid.UUID, file_id: uuid.UUID) -> File:
    record = await db.get(File, file_id)
    if record is None or record.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return record


@router.get("/{file_id}", response_model=FileOut)
async def get_file(
    project_id: uuid.UUID, file_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> FileOut:
    await get_project_or_404(db, project_id, user)
    return FileOut.model_validate(await _get_file(db, project_id, file_id))


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
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    storage: CurrentStorage,
    inline: bool = False,
) -> StreamingResponse:
    await get_project_or_404(db, project_id, user)
    record = await _get_file(db, project_id, file_id)
    byte_range = _parse_range(request.headers.get("range"), record.size)
    disposition = "inline" if inline else "attachment"
    headers = {
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(record.name)}",
        "Accept-Ranges": "bytes",
        "ETag": f'"{record.sha256}"',
        "Cache-Control": "private, max-age=0",
    }
    status_code = status.HTTP_200_OK
    length = record.size
    if byte_range is not None:
        start, end = byte_range
        length = end - start + 1
        headers["Content-Range"] = f"bytes {start}-{end}/{record.size}"
        status_code = status.HTTP_206_PARTIAL_CONTENT
    headers["Content-Length"] = str(length)
    try:
        body = storage.open(record.s3_key, byte_range)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File content missing") from exc
    return StreamingResponse(body, status_code=status_code, media_type=record.mime, headers=headers)


@router.get("/{file_id}/content")
async def file_content(
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    storage: CurrentStorage,
    settings: CurrentSettings,
    offset: int = Query(default=0, ge=0),
    length: int | None = Query(default=None, ge=1),
) -> Response:
    """Whole-file (or slice) content for the preview pane; refused above the preview size cap."""
    await get_project_or_404(db, project_id, user)
    record = await _get_file(db, project_id, file_id)
    if record.size > settings.max_preview_bytes and length is None:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "File is too large for a whole-file preview; use the entry views",
        )
    if offset >= record.size:
        return Response(b"", media_type=record.mime)
    end = record.size - 1 if length is None else min(offset + length, record.size) - 1
    if end - offset + 1 > settings.max_preview_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Requested slice too large")
    chunks = [c async for c in storage.open(record.s3_key, (offset, end))]
    media_type = record.mime
    if media_type.startswith("text/") or media_type.endswith(("xml", "json")):
        media_type = f"{media_type}; charset=utf-8"
    return Response(
        b"".join(chunks), media_type=media_type, headers={"X-File-Size": str(record.size)}
    )


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    storage: CurrentStorage,
) -> Response:
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    record = await _get_file(db, project_id, file_id)
    await storage.delete(record.s3_key)
    audit.record(db, user.id, "file.deleted", file_id, project=str(project_id), name=record.name)
    await db.delete(record)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
