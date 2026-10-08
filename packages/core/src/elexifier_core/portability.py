"""Move a project between instances: export one project with its rows and stored files into
a tar.gz, import it elsewhere under another owner with the original ids preserved.

    python -m elexifier_core.portability export <project id or name> --out project.tgz
    python -m elexifier_core.portability import project.tgz --owner you@example.org

What travels: the project row, files, documents, entries, PDF pages, annotations, mappings,
specifications, runs and converted entries, custom steps, conversations with their messages,
agent runs with their events and approvals, usage records, and every stored object the rows
point at (sources, outputs, artifacts, page layouts, the learned segmenter). What does not:
users and memberships (the importing user becomes the owner and every "who" column points at
them), provider credentials (encrypted with the exporting instance's key; the project's model
choice is cleared), sessions, audit rows, job queue state, and regenerable caches (page
images). An archive imports only on an instance at the same database schema revision.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import mimetypes
import sys
import tarfile
import tempfile
import uuid
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, BinaryIO

from sqlalchemy import Table, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_core.db.base import Base
from elexifier_core.db.models import (
    AgentEvent,
    AgentRun,
    Annotation,
    Approval,
    Conversation,
    ConversionRun,
    ConversionSpec,
    ConvertedEntry,
    Document,
    Entry,
    File,
    Mapping,
    Message,
    PdfPage,
    PipelineStep,
    Project,
    UsageRecord,
    User,
)
from elexifier_core.storage import NotFoundError, Storage

FORMAT = 1
CHUNK = 1024 * 1024
BATCH = 500

# the order matters: parents before children (foreign keys)
TABLES: list[str] = [
    "projects",
    "files",
    "documents",
    "entries",
    "pdf_pages",
    "annotations",
    "mappings",
    "conversion_specs",
    "conversion_runs",
    "converted_entries",
    "pipeline_steps",
    "conversations",
    "messages",
    "agent_runs",
    "agent_events",
    "approvals",
    "usage_records",
]


class PortabilityError(Exception):
    pass


def _table(name: str) -> Table:
    return Base.metadata.tables[name]


def _user_columns(table: Table) -> list[str]:
    return [
        c.name for c in table.columns for fk in c.foreign_keys if fk.column.table.name == "users"
    ]


def _encode(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    return value


def _decode(table: Table, row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for column in table.columns:
        if column.name not in row:
            continue
        value = row[column.name]
        kind = type(column.type).__name__
        if value is None:
            out[column.name] = None
        elif kind == "UUID":
            out[column.name] = uuid.UUID(str(value))
        elif kind == "DateTime":
            out[column.name] = datetime.fromisoformat(str(value))
        elif kind == "Numeric":
            out[column.name] = Decimal(str(value))
        elif isinstance(value, dict) and "$bytes" in value and kind == "LargeBinary":
            out[column.name] = base64.b64decode(value["$bytes"])
        else:
            out[column.name] = value
    return out


async def _schema_revision(db: AsyncSession) -> str:
    row = (await db.execute(text("select version_num from alembic_version"))).scalar_one_or_none()
    return str(row or "")


# --- export -------------------------------------------------------------------------------------


async def _rows(db: AsyncSession, project_id: uuid.UUID) -> dict[str, list[dict[str, Any]]]:
    """Every row of the project per table, as plain dicts in column order."""
    p = project_id
    doc_ids = select(Document.id).where(Document.project_id == p)
    run_ids = select(ConversionRun.id).where(ConversionRun.project_id == p)
    conv_ids = select(Conversation.id).where(Conversation.project_id == p)
    agent_run_ids = select(AgentRun.id).where(AgentRun.conversation_id.in_(conv_ids))
    queries = {
        "projects": select(Project.__table__).where(Project.id == p),
        "files": select(File.__table__).where(File.project_id == p),
        "documents": select(Document.__table__).where(Document.project_id == p),
        "entries": select(Entry.__table__).where(Entry.document_id.in_(doc_ids)),
        "pdf_pages": select(PdfPage.__table__).where(PdfPage.document_id.in_(doc_ids)),
        "annotations": select(Annotation.__table__).where(Annotation.document_id.in_(doc_ids)),
        "mappings": select(Mapping.__table__).where(Mapping.project_id == p),
        "conversion_specs": select(ConversionSpec.__table__).where(ConversionSpec.project_id == p),
        "conversion_runs": select(ConversionRun.__table__).where(ConversionRun.project_id == p),
        "converted_entries": select(ConvertedEntry.__table__).where(
            ConvertedEntry.run_id.in_(run_ids)
        ),
        "pipeline_steps": select(PipelineStep.__table__).where(PipelineStep.project_id == p),
        "conversations": select(Conversation.__table__).where(Conversation.project_id == p),
        "messages": select(Message.__table__).where(Message.conversation_id.in_(conv_ids)),
        "agent_runs": select(AgentRun.__table__).where(AgentRun.conversation_id.in_(conv_ids)),
        "agent_events": select(AgentEvent.__table__).where(AgentEvent.run_id.in_(agent_run_ids)),
        "approvals": select(Approval.__table__).where(Approval.conversation_id.in_(conv_ids)),
        "usage_records": select(UsageRecord.__table__).where(UsageRecord.project_id == p),
    }
    out: dict[str, list[dict[str, Any]]] = {}
    for name in TABLES:
        result = await db.execute(queries[name])
        out[name] = [dict(r._mapping) for r in result]
    return out


def storage_keys(rows: dict[str, list[dict[str, Any]]]) -> list[str]:
    """Stored objects the rows point at (caches such as page images are left out)."""
    keys: list[str] = [str(f["s3_key"]) for f in rows["files"]]
    keys += [str(pg["layout_s3_key"]) for pg in rows["pdf_pages"] if pg.get("layout_s3_key")]
    for document in rows["documents"]:
        learned = (document.get("stats") or {}).get("learned_segmenter") or {}
        if learned.get("key"):
            keys.append(str(learned["key"]))
    seen: set[str] = set()
    return [k for k in keys if not (k in seen or seen.add(k))]


async def _spool(storage: Storage, key: str, target: Path) -> int:
    size = 0
    with target.open("wb") as fh:
        async for chunk in storage.open(key):
            fh.write(chunk)
            size += len(chunk)
    return size


async def export_project(
    db: AsyncSession, storage: Storage, project_id: uuid.UUID, out: BinaryIO
) -> dict[str, Any]:
    """Write the project's archive to ``out``; returns the manifest."""
    project = await db.get(Project, project_id)
    if project is None:
        raise PortabilityError(f"no project {project_id}")
    rows = await _rows(db, project_id)
    keys = storage_keys(rows)
    manifest: dict[str, Any] = {
        "format": FORMAT,
        "schema": await _schema_revision(db),
        "exported_at": datetime.now(UTC).isoformat(),
        "project_id": str(project.id),
        "name": project.name,
        "source_type": project.source_type,
        "tables": {name: len(items) for name, items in rows.items()},
        "objects": [],
        "missing_objects": [],
    }
    with tarfile.open(fileobj=out, mode="w:gz") as tar, tempfile.TemporaryDirectory() as tmp:
        for name in TABLES:
            payload = "\n".join(
                json.dumps({k: _encode(v) for k, v in row.items()}, ensure_ascii=False)
                for row in rows[name]
            ).encode("utf-8")
            info = tarfile.TarInfo(f"tables/{name}.jsonl")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        spool = Path(tmp) / "object"
        for key in keys:
            try:
                size = await _spool(storage, key, spool)
            except NotFoundError:
                manifest["missing_objects"].append(key)
                continue
            info = tarfile.TarInfo(f"storage/{key}")
            info.size = size
            with spool.open("rb") as fh:
                tar.addfile(info, fh)
            manifest["objects"].append({"key": key, "size": size})
        payload = json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    return manifest


# --- import -------------------------------------------------------------------------------------


def _chunks(fh: Any) -> AsyncIterator[bytes]:
    async def gen() -> AsyncIterator[bytes]:
        while chunk := fh.read(CHUNK):
            yield chunk

    return gen()


async def import_project(
    db: AsyncSession,
    storage: Storage,
    archive: BinaryIO,
    owner_email: str,
    *,
    allow_schema_mismatch: bool = False,
) -> dict[str, Any]:
    """Create the project from the archive under ``owner_email`` (a user who has logged in on
    this instance at least once). Commits nothing; the caller commits."""
    owner = (await db.execute(select(User).where(User.email == owner_email))).scalars().first()
    if owner is None:
        raise PortabilityError(
            f"no user {owner_email!r} on this instance; they must log in once before importing"
        )
    with tarfile.open(fileobj=archive, mode="r:gz") as tar:
        member = tar.getmember("manifest.json")
        manifest = json.loads(tar.extractfile(member).read().decode("utf-8"))  # type: ignore[union-attr]
        if manifest.get("format") != FORMAT:
            raise PortabilityError(f"unknown archive format {manifest.get('format')!r}")
        here = await _schema_revision(db)
        if manifest.get("schema") != here and not allow_schema_mismatch:
            raise PortabilityError(
                f"the archive was made at schema {manifest.get('schema')!r}, this instance is at "
                f"{here!r}; update the older side first (or pass --allow-schema-mismatch)"
            )
        project_id = uuid.UUID(manifest["project_id"])
        if await db.get(Project, project_id) is not None:
            raise PortabilityError(f"project {project_id} already exists here ({manifest['name']})")
        rows: dict[str, list[dict[str, Any]]] = {}
        for name in TABLES:
            table = _table(name)
            try:
                raw = tar.extractfile(tar.getmember(f"tables/{name}.jsonl"))
            except KeyError:
                rows[name] = []
                continue
            lines = raw.read().decode("utf-8").splitlines() if raw else []  # type: ignore[union-attr]
            user_cols = _user_columns(table)
            decoded = []
            for line in lines:
                if not line.strip():
                    continue
                row = _decode(table, json.loads(line))
                for col in user_cols:
                    if row.get(col) is not None:
                        row[col] = owner.id
                decoded.append(row)
            rows[name] = decoded
        for project in rows["projects"]:
            project["owner_id"] = owner.id
            project["default_model"] = None  # credential ids do not travel
        for name in TABLES:
            table = _table(name)
            items = rows[name]
            for start in range(0, len(items), BATCH):
                await db.execute(insert(table), items[start : start + BATCH])
        await db.execute(
            insert(_table("project_members")),
            [{"project_id": project_id, "user_id": owner.id, "role": "owner"}],
        )
        mimes = {str(f["s3_key"]): str(f.get("mime") or "") for f in rows["files"]}
        stored = 0
        for member in tar.getmembers():
            if not member.isfile() or not member.name.startswith("storage/"):
                continue
            key = member.name[len("storage/") :]
            content_type = (
                mimes.get(key) or mimetypes.guess_type(key)[0] or "application/octet-stream"
            )
            fh = tar.extractfile(member)
            if fh is None:
                continue
            await storage.put(key, _chunks(fh), content_type)
            stored += 1
    await db.flush()
    return {
        "project_id": str(project_id),
        "name": manifest["name"],
        "owner": owner.email,
        "tables": {name: len(items) for name, items in rows.items()},
        "objects": stored,
        "missing_objects": manifest.get("missing_objects", []),
    }


# --- CLI ----------------------------------------------------------------------------------------


async def _resolve_project(db: AsyncSession, ref: str) -> uuid.UUID:
    try:
        return uuid.UUID(ref)
    except ValueError:
        pass
    found = (await db.execute(select(Project).where(Project.name == ref))).scalars().all()
    if len(found) != 1:
        raise PortabilityError(
            f"{len(found)} projects named {ref!r}; use the id"
            if found
            else f"no project named {ref!r}"
        )
    return found[0].id


def _print(items: Iterable[tuple[str, Any]]) -> None:
    for key, value in items:
        print(f"{key:<18} {value}")


async def _main(argv: list[str] | None = None) -> int:
    from elexifier_core.db.session import Database
    from elexifier_core.settings import get_settings
    from elexifier_core.storage import build_storage

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export", help="write one project to a tar.gz")
    exp.add_argument("project", help="project id or exact name")
    exp.add_argument("--out", default="-", help="file path, or - for stdout")
    imp = sub.add_parser("import", help="create a project from a tar.gz")
    imp.add_argument("archive", help="file path, or - for stdin")
    imp.add_argument("--owner", required=True, help="e-mail of the user who will own it")
    imp.add_argument("--allow-schema-mismatch", action="store_true")
    args = parser.parse_args(argv)

    settings = get_settings()
    db = Database(settings)
    storage = build_storage(settings)
    try:
        async with db.session() as session:
            if args.command == "export":
                project_id = await _resolve_project(session, args.project)
                if args.out == "-":
                    manifest = await export_project(session, storage, project_id, sys.stdout.buffer)
                else:
                    # a one-shot command: blocking file handles are fine here
                    with open(args.out, "wb") as out:  # noqa: ASYNC230
                        manifest = await export_project(session, storage, project_id, out)
                total = sum(o["size"] for o in manifest["objects"])
                _print(
                    [
                        ("exported", manifest["name"]),
                        ("rows", json.dumps(manifest["tables"])),
                        ("objects", f"{len(manifest['objects'])} ({total / 1e6:.1f} MB)"),
                        ("missing", manifest["missing_objects"] or "none"),
                    ]
                )
            else:
                if args.archive == "-":
                    data = io.BytesIO(sys.stdin.buffer.read())
                    result = await import_project(
                        session,
                        storage,
                        data,
                        args.owner,
                        allow_schema_mismatch=args.allow_schema_mismatch,
                    )
                else:
                    with open(args.archive, "rb") as fh:  # noqa: ASYNC230
                        result = await import_project(
                            session,
                            storage,
                            fh,
                            args.owner,
                            allow_schema_mismatch=args.allow_schema_mismatch,
                        )
                await session.commit()
                _print(
                    [
                        ("imported", result["name"]),
                        ("owner", result["owner"]),
                        ("rows", json.dumps(result["tables"])),
                        ("objects", result["objects"]),
                        ("missing", result["missing_objects"] or "none"),
                    ]
                )
    except PortabilityError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        await db.dispose()
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
