"""Conversion runs and their converted entries (sample runs now, full runs in M1.8)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_agent.conversion import ConversionError, entry_xml, mapping_version, run_sample
from elexifier_agent.tools._entries import pick_entries
from elexifier_api import audit
from elexifier_api.authz import get_project_or_404
from elexifier_api.deps import CurrentStorage, CurrentUser, DbSession
from elexifier_api.schemas import (
    ConvertedEntryDetailOut,
    ConvertedEntryOut,
    ConvertedEntryPage,
    RunDetailOut,
    RunOut,
    SampleRunCreate,
)
from elexifier_core.db.models import (
    ConversionRun,
    ConvertedEntry,
    Document,
    DocumentStatus,
    Entry,
    ProjectRole,
)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["runs"])


async def _run(db: AsyncSession, project_id: uuid.UUID, run_id: uuid.UUID) -> ConversionRun:
    run = await db.get(ConversionRun, run_id)
    if run is None or run.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return run


def _detail(row: ConvertedEntry, run: ConversionRun) -> ConvertedEntryDetailOut:
    return ConvertedEntryDetailOut(
        id=row.id,
        run_id=row.run_id,
        entry_id=row.entry_id,
        position=row.position,
        ordinal=row.ordinal,
        headword=row.headword,
        issue_count=row.issue_count,
        issues=row.issues,
        dmlex=row.dmlex,
        xml=entry_xml(row.dmlex),
        mapping_version=run.mapping_version,
        run_created_at=run.created_at,
    )


@router.get("/runs", response_model=list[RunOut])
async def list_runs(project_id: uuid.UUID, db: DbSession, user: CurrentUser) -> list[RunOut]:
    await get_project_or_404(db, project_id, user)
    rows = (
        (
            await db.execute(
                select(ConversionRun)
                .where(ConversionRun.project_id == project_id)
                .order_by(ConversionRun.created_at.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    return [RunOut.model_validate(r) for r in rows]


@router.post("/runs", response_model=RunDetailOut, status_code=status.HTTP_201_CREATED)
async def create_run(
    project_id: uuid.UUID,
    body: SampleRunCreate,
    db: DbSession,
    user: CurrentUser,
    storage: CurrentStorage,
) -> RunDetailOut:
    """Run a sample conversion synchronously (the UI's "run sample" button)."""
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    if body.document_id is not None:
        document = await db.get(Document, body.document_id)
        if document is None or document.project_id != project_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    else:
        ready = (
            (
                await db.execute(
                    select(Document)
                    .where(
                        Document.project_id == project_id,
                        Document.status == DocumentStatus.READY.value,
                    )
                    .order_by(Document.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        if len(ready) != 1:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Pass document_id: the project has no or several analysed documents",
            )
        document = ready[0]
    mapping = await mapping_version(db, project_id, body.mapping_version)
    if mapping is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No such mapping version")
    entries = await pick_entries(
        db, document, n=body.n, strategy=body.strategy, ordinals=body.ordinals, query=body.query
    )
    if not entries:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No entries selected")
    try:
        outcome = await run_sample(
            db,
            storage,
            document=document,
            mapping=mapping,
            entries=entries,
            triggered_by=user.id,
            scope={
                "ordinals": [e.ordinal for e in entries],
                "strategy": None if body.ordinals else body.strategy,
                "query": body.query,
            },
        )
    except ConversionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    audit.record(
        db,
        user.id,
        "run.sample",
        outcome.run.id,
        project=str(project_id),
        mapping_version=mapping.version,
    )
    await db.flush()
    return RunDetailOut.model_validate(outcome.run)


@router.get("/runs/{run_id}", response_model=RunDetailOut)
async def get_run(
    project_id: uuid.UUID, run_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> RunDetailOut:
    await get_project_or_404(db, project_id, user)
    return RunDetailOut.model_validate(await _run(db, project_id, run_id))


@router.get("/runs/{run_id}/entries", response_model=ConvertedEntryPage)
async def list_converted_entries(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    issues: bool = Query(default=False, description="only entries with issues"),
) -> ConvertedEntryPage:
    await get_project_or_404(db, project_id, user)
    await _run(db, project_id, run_id)
    base = select(ConvertedEntry).where(ConvertedEntry.run_id == run_id)
    if issues:
        base = base.where(ConvertedEntry.issue_count > 0)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        (await db.execute(base.order_by(ConvertedEntry.position).offset(offset).limit(limit)))
        .scalars()
        .all()
    )
    return ConvertedEntryPage(
        items=[ConvertedEntryOut.model_validate(r) for r in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/runs/{run_id}/entries/{converted_id}", response_model=ConvertedEntryDetailOut)
async def get_converted_entry(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    converted_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
) -> ConvertedEntryDetailOut:
    await get_project_or_404(db, project_id, user)
    run = await _run(db, project_id, run_id)
    row = await db.get(ConvertedEntry, converted_id)
    if row is None or row.run_id != run.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Converted entry not found")
    return _detail(row, run)


@router.get("/entries/{entry_id}/converted", response_model=list[ConvertedEntryDetailOut])
async def converted_for_entry(
    project_id: uuid.UUID,
    entry_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    run_id: Annotated[uuid.UUID | None, Query(description="default: the latest run")] = None,
) -> list[ConvertedEntryDetailOut]:
    """The DMLex output of one source entry: all output entries it produced in the chosen run
    (a source entry can yield several, e.g. phrasal verbs as separate entries)."""
    await get_project_or_404(db, project_id, user)
    entry = await db.get(Entry, entry_id)
    document = await db.get(Document, entry.document_id) if entry else None
    if entry is None or document is None or document.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entry not found")
    if run_id is None:
        latest = (
            await db.execute(
                select(ConversionRun.id)
                .join(ConvertedEntry, ConvertedEntry.run_id == ConversionRun.id)
                .where(ConvertedEntry.entry_id == entry_id)
                .order_by(ConversionRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest is None:
            return []
        run_id = latest
    run = await _run(db, project_id, run_id)
    rows = (
        (
            await db.execute(
                select(ConvertedEntry)
                .where(ConvertedEntry.run_id == run.id, ConvertedEntry.entry_id == entry_id)
                .order_by(ConvertedEntry.position)
            )
        )
        .scalars()
        .all()
    )
    return [_detail(r, run) for r in rows]
