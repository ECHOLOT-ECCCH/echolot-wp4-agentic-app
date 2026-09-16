"""Mapping versions and the conversion specification (the editors in the middle pane)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_api import audit
from elexifier_api.authz import get_project_or_404
from elexifier_api.deps import CurrentUser, DbSession
from elexifier_api.schemas import (
    MappingCheckOut,
    MappingIn,
    MappingIssueOut,
    MappingOut,
    MappingSummaryOut,
    SpecIn,
    SpecOut,
)
from elexifier_core.db.models import ConversionSpec, Mapping, ProjectRole
from elexifier_pipeline.xml.dmlexifier import MappingError
from elexifier_pipeline.xml.mapping import (
    Issue,
    MappingSpec,
    check_mapping,
    has_errors,
    load_mapping_spec,
    summarize,
)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["mappings"])


def _issues_out(issues: list[Issue]) -> list[MappingIssueOut]:
    return [MappingIssueOut(severity=i.severity, path=i.path, message=i.message) for i in issues]


def _validate(spec: dict) -> tuple[MappingSpec | None, list[Issue]]:
    """Shape errors become a single error issue; then the structural checks."""
    try:
        model = load_mapping_spec(spec)
    except MappingError as exc:
        return None, [Issue(severity="error", path="<root>", message=str(exc))]
    return model, check_mapping(model)


def _out(mapping: Mapping) -> MappingOut:
    model, _ = _validate(mapping.spec)
    issues = [Issue.model_validate(i) for i in (mapping.issues or [])]
    return MappingOut(
        id=mapping.id,
        version=mapping.version,
        note=mapping.note,
        source=mapping.source,
        created_by=mapping.created_by,
        created_at=mapping.created_at,
        issue_count=len(issues),
        spec=mapping.spec,
        issues=_issues_out(issues),
        summary=summarize(model) if model is not None else "",
    )


async def _mapping(db: AsyncSession, project_id: uuid.UUID, version: str) -> Mapping:
    query = select(Mapping).where(Mapping.project_id == project_id)
    if version == "latest":
        query = query.order_by(Mapping.version.desc()).limit(1)
    else:
        try:
            query = query.where(Mapping.version == int(version))
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Mapping not found") from exc
    mapping = (await db.execute(query)).scalar_one_or_none()
    if mapping is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mapping not found")
    return mapping


@router.get("/mappings", response_model=list[MappingSummaryOut])
async def list_mappings(
    project_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> list[MappingSummaryOut]:
    await get_project_or_404(db, project_id, user)
    rows = (
        (
            await db.execute(
                select(Mapping)
                .where(Mapping.project_id == project_id)
                .order_by(Mapping.version.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        MappingSummaryOut(
            id=m.id,
            version=m.version,
            note=m.note,
            source=m.source,
            created_by=m.created_by,
            created_at=m.created_at,
            issue_count=len(m.issues or []),
        )
        for m in rows
    ]


@router.post("/mappings/check", response_model=MappingCheckOut)
async def check(
    project_id: uuid.UUID, body: MappingIn, db: DbSession, user: CurrentUser
) -> MappingCheckOut:
    """Validate a mapping without storing it (the editor calls this while typing)."""
    await get_project_or_404(db, project_id, user)
    model, issues = _validate(body.spec)
    return MappingCheckOut(
        ok=not has_errors(issues),
        issues=_issues_out(issues),
        summary=summarize(model) if model is not None else None,
    )


@router.post("/mappings", response_model=MappingOut, status_code=status.HTTP_201_CREATED)
async def create_mapping(
    project_id: uuid.UUID, body: MappingIn, db: DbSession, user: CurrentUser
) -> MappingOut:
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    model, issues = _validate(body.spec)
    if model is None or has_errors(issues):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"message": "The mapping has errors", "issues": [i.model_dump() for i in issues]},
        )
    version = (
        await db.execute(
            select(func.coalesce(func.max(Mapping.version), 0)).where(
                Mapping.project_id == project_id
            )
        )
    ).scalar_one() + 1
    mapping = Mapping(
        project_id=project_id,
        version=version,
        spec=body.spec,
        note=body.note,
        issues=[i.model_dump() for i in issues],
        created_by=user.id,
        source="user",
    )
    db.add(mapping)
    audit.record(db, user.id, "mapping.saved", mapping.id, project=str(project_id), version=version)
    await db.flush()
    await db.refresh(mapping)
    return _out(mapping)


@router.get("/mappings/{version}", response_model=MappingOut)
async def get_mapping(
    project_id: uuid.UUID, version: str, db: DbSession, user: CurrentUser
) -> MappingOut:
    """One version, or ``latest``."""
    await get_project_or_404(db, project_id, user)
    return _out(await _mapping(db, project_id, version))


@router.get("/spec", response_model=SpecOut)
async def get_spec(project_id: uuid.UUID, db: DbSession, user: CurrentUser) -> SpecOut:
    await get_project_or_404(db, project_id, user)
    spec = (
        await db.execute(
            select(ConversionSpec)
            .where(ConversionSpec.project_id == project_id)
            .order_by(ConversionSpec.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No specification yet")
    return SpecOut.model_validate(spec)


@router.put("/spec", response_model=SpecOut)
async def put_spec(
    project_id: uuid.UUID, body: SpecIn, db: DbSession, user: CurrentUser
) -> SpecOut:
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    version = (
        await db.execute(
            select(func.coalesce(func.max(ConversionSpec.version), 0)).where(
                ConversionSpec.project_id == project_id
            )
        )
    ).scalar_one() + 1
    spec = ConversionSpec(
        project_id=project_id, version=version, markdown=body.markdown, updated_by=user.id
    )
    db.add(spec)
    audit.record(db, user.id, "spec.saved", spec.id, project=str(project_id), version=version)
    await db.flush()
    await db.refresh(spec)
    return SpecOut.model_validate(spec)
