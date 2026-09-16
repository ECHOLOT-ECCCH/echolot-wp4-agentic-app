"""Conversion runs and their converted entries (sample runs now, full runs in M1.8)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_agent.conversion import (
    ConversionError,
    entry_xml,
    mapping_version,
    queue_full_run,
    run_sample,
)
from elexifier_agent.tools._entries import pick_entries
from elexifier_api import audit
from elexifier_api.authz import get_project_or_404
from elexifier_api.deps import CurrentStorage, CurrentUser, DbSession, State
from elexifier_api.jobs.structuring import LOW_CONFIDENCE, rebuild_outputs
from elexifier_api.schemas import (
    ConvertedEntryDetailOut,
    ConvertedEntryOut,
    ConvertedEntryOverride,
    ConvertedEntryPage,
    RunCreate,
    RunDetailOut,
    RunOut,
)
from elexifier_core.db.models import (
    ConversionRun,
    ConversionStatus,
    ConvertedEntry,
    Document,
    DocumentStatus,
    Entry,
    File,
    ProjectRole,
)
from elexifier_pipeline.dmlex.validator import validate_entries

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
        annotation_id=row.annotation_id,
        position=row.position,
        ordinal=row.ordinal,
        headword=row.headword,
        issue_count=row.issue_count,
        issues=row.issues,
        confidence=row.confidence,
        notes=row.notes or [],
        dmlex=row.dmlex,
        xml=entry_xml(row.override or row.dmlex) if (row.override or row.dmlex) else "",
        mapping_version=run.mapping_version,
        run_created_at=run.created_at,
        override=row.override,
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
    body: RunCreate,
    db: DbSession,
    user: CurrentUser,
    storage: CurrentStorage,
    state: State,
) -> RunDetailOut:
    """Run a sample conversion synchronously, or queue a full run for the worker."""
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
    if body.kind == "full":
        if document.status != DocumentStatus.READY.value:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Document is not ready")

        async def defer(name: str, kwargs: dict[str, Any]) -> int | None:
            job_id = await state.jobs.configure_task(name, queue="heavy").defer_async(**kwargs)
            return int(job_id) if job_id is not None else None

        try:
            run = await queue_full_run(
                db,
                document=document,
                mapping=mapping,
                triggered_by=user.id,
                options={"number_homographs": body.number_homographs},
                defer=defer,
            )
        except ConversionError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        audit.record(
            db,
            user.id,
            "run.full",
            run.id,
            project=str(project_id),
            mapping_version=mapping.version,
        )
        await db.flush()
        return RunDetailOut.model_validate(run)
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
            runner=state.runner,
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
    review: bool = Query(
        default=False, description="entries with issues or a model confidence below 0.6"
    ),
) -> ConvertedEntryPage:
    await get_project_or_404(db, project_id, user)
    await _run(db, project_id, run_id)
    base = select(ConvertedEntry).where(ConvertedEntry.run_id == run_id)
    if issues:
        base = base.where(ConvertedEntry.issue_count > 0)
    if review:
        base = base.where(
            (ConvertedEntry.issue_count > 0) | (ConvertedEntry.confidence < LOW_CONFIDENCE)
        )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        (await db.execute(base.order_by(ConvertedEntry.position).offset(offset).limit(limit)))
        .scalars()
        .all()
    )
    # full runs store issue rows without the output; show the source headword instead
    missing = [r.entry_id for r in rows if r.headword is None and r.entry_id is not None]
    headwords: dict[uuid.UUID, str | None] = {}
    if missing:
        found = (
            await db.execute(select(Entry.id, Entry.headword).where(Entry.id.in_(missing)))
        ).all()
        headwords = {entry_id: headword for entry_id, headword in found}
    items = []
    for r in rows:
        item = ConvertedEntryOut.model_validate(r)
        if item.headword is None and r.entry_id is not None:
            item = item.model_copy(update={"headword": headwords.get(r.entry_id)})
        items.append(item)
    return ConvertedEntryPage(items=items, total=total, offset=offset, limit=limit)


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


@router.patch("/runs/{run_id}/entries/{converted_id}", response_model=ConvertedEntryDetailOut)
async def override_converted_entry(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    converted_id: uuid.UUID,
    body: ConvertedEntryOverride,
    db: DbSession,
    user: CurrentUser,
) -> ConvertedEntryDetailOut:
    """Store the lexicographer's corrected DMLex for one entry (or remove the correction) and
    re-validate it. The export uses the correction."""
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    run = await _run(db, project_id, run_id)
    row = await db.get(ConvertedEntry, converted_id)
    if row is None or row.run_id != run.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Converted entry not found")
    if body.override is not None and not str(body.override.get("headword") or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "An entry needs a headword")
    row.override = body.override
    entry = row.override or row.dmlex
    kept = [i for i in (row.issues or []) if i.get("source") == "structurer"]
    if entry:
        profile = (run.scope or {}).get("profile") or {}
        lang = profile.get("source_language") or "und"
        validation = validate_entries(
            {
                "uri": f"urn:elexifier:{run.document_id}",
                "langCode": lang if lang != "unknown" else "und",
            },
            [entry],
        )
        kept += [i.as_dict() for i in validation.issues_for(0)]
    row.issues = kept
    row.issue_count = len(kept)
    if entry:
        row.headword = str(entry.get("headword") or row.headword or "")[:300] or None
    audit.record(
        db,
        user.id,
        "converted_entry.override",
        row.id,
        run=str(run.id),
        removed=body.override is None,
    )
    await db.flush()
    await db.refresh(row)
    return _detail(row, run)


@router.post("/runs/{run_id}/rebuild", response_model=RunDetailOut)
async def rebuild_run_outputs(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    storage: CurrentStorage,
) -> RunDetailOut:
    """Write the output files again from the stored entries (after corrections)."""
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    run = await _run(db, project_id, run_id)
    if run.mapping_version != 0 or run.status != ConversionStatus.DONE.value:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Only finished structuring runs can be rebuilt; XML runs are re-run instead",
        )
    document = await db.get(Document, run.document_id) if run.document_id else None
    source = await db.get(File, document.source_file_id) if document else None
    stem = Path(source.name).stem if source else "dictionary"
    await rebuild_outputs(db, storage, run, stem=stem)
    audit.record(db, user.id, "run.rebuilt", run.id, project=str(project_id))
    await db.flush()
    await db.refresh(run)
    return RunDetailOut.model_validate(run)


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
