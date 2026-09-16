"""Structuring of PDF entry spans into DMLex: pick spans, extract their text (and page crops),
run a structurer, validate, and store a run with converted entries and usage."""

from __future__ import annotations

import statistics
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_agent.pdf_service import crop_png, page_image
from elexifier_core.db.models import (
    Annotation,
    AnnotationStatus,
    ConversionRun,
    ConversionStatus,
    ConvertedEntry,
    Document,
    File,
    RunKind,
    UsageRecord,
)
from elexifier_core.events import publish
from elexifier_core.storage import Storage
from elexifier_pipeline.dmlex.validator import validate_entries
from elexifier_pipeline.pdf.models import BBox
from elexifier_pipeline.pdf.structurers import (
    CostEstimate,
    ExtractedEntry,
    LlmStructurer,
    RuleStructurer,
    StructuredEntry,
    StructureProfile,
    estimate_cost,
)

MAX_SAMPLE_SPANS = 20
IMAGE_DPI = 110
USABLE_STATUSES = (AnnotationStatus.ACCEPTED.value, AnnotationStatus.EDITED.value)


class StructuringError(Exception):
    pass


@dataclass
class StructuringOutcome:
    run: ConversionRun
    converted: list[ConvertedEntry]
    results: list[StructuredEntry]
    usage: dict[str, int] = field(default_factory=dict)
    failed: int = 0


async def usable_spans(
    db: AsyncSession, document: Document, *, include_proposed: bool = True
) -> list[Annotation]:
    """Spans to structure: accepted and edited ones, plus proposals when nothing was reviewed."""
    rows = list(
        (
            await db.execute(
                select(Annotation)
                .where(
                    Annotation.document_id == document.id,
                    Annotation.status != AnnotationStatus.REJECTED.value,
                    Annotation.label == "entry",
                )
                .order_by(Annotation.page_no, Annotation.position, Annotation.created_at)
            )
        )
        .scalars()
        .all()
    )
    reviewed = [r for r in rows if r.status in USABLE_STATUSES]
    if reviewed or not include_proposed:
        return reviewed
    return rows


def pick_spans(spans: Sequence[Annotation], n: int, strategy: str = "spread") -> list[Annotation]:
    n = max(1, min(n, MAX_SAMPLE_SPANS, len(spans)))
    if strategy == "first" or len(spans) <= n:
        return list(spans[:n])
    step = len(spans) / n
    return [spans[int(i * step)] for i in range(n)]


def span_text(annotation: Annotation) -> str:
    return str((annotation.payload or {}).get("text") or "").strip()


async def extract(
    storage: Storage,
    document: Document,
    file: File,
    spans: Sequence[Annotation],
    *,
    with_images: bool,
) -> list[ExtractedEntry]:
    out: list[ExtractedEntry] = []
    pages: dict[int, bytes] = {}
    for annotation in spans:
        image = None
        if with_images:
            if annotation.page_no not in pages:
                pages[annotation.page_no] = await page_image(
                    storage, document, file, annotation.page_no, IMAGE_DPI
                )
            bbox: BBox = tuple(annotation.bbox)  # type: ignore[assignment]
            image = crop_png(pages[annotation.page_no], bbox, IMAGE_DPI)
        out.append(
            ExtractedEntry(
                span_id=str(annotation.id),
                page=annotation.page_no,
                bbox=tuple(annotation.bbox),  # type: ignore[arg-type]
                text=span_text(annotation),
                page_image_png=image,
            )
        )
    return out


def estimate_for(
    spans: Sequence[Annotation],
    *,
    with_images: bool,
    price_in_per_m: float | None,
    price_out_per_m: float | None,
) -> CostEstimate:
    lengths = [len(span_text(a)) for a in spans] or [0]
    return estimate_cost(
        len(spans),
        int(statistics.fmean(lengths)),
        with_images,
        price_in_per_m=price_in_per_m,
        price_out_per_m=price_out_per_m,
    )


async def structure_sample(
    db: AsyncSession,
    storage: Storage,
    *,
    document: Document,
    spans: Sequence[Annotation],
    profile: StructureProfile,
    model: Any | None,
    structurer_name: str,
    with_images: bool,
    triggered_by: uuid.UUID | None,
    model_spec: dict[str, Any] | None = None,
    project_id: uuid.UUID,
) -> StructuringOutcome:
    """Structure the given spans now (sample size), validate and store the run. Commits nothing."""
    if not spans:
        raise StructuringError("no entry spans to structure; segment and review the pages first")
    if len(spans) > MAX_SAMPLE_SPANS:
        raise StructuringError(f"a sample structures at most {MAX_SAMPLE_SPANS} spans")
    file = await db.get(File, document.source_file_id)
    if file is None:
        raise StructuringError("the source file of this document is gone")
    if structurer_name == "llm":
        if model is None:
            raise StructuringError("no model available for structuring in this context")
        structurer: Any = LlmStructurer(model, profile=profile)
    elif structurer_name == "rule":
        structurer = RuleStructurer(profile)
    else:
        raise StructuringError(f"unknown structurer {structurer_name!r} (rule | llm)")
    extracted = await extract(storage, document, file, spans, with_images=with_images)
    results: list[StructuredEntry] = [r async for r in structurer.structure(extracted)]
    entries = [r.entry for r in results if r.entry]
    validation = validate_entries(
        {"uri": f"urn:elexifier:{document.id}", "langCode": profile.source_language or "und"},
        entries,
    )
    run = ConversionRun(
        project_id=project_id,
        document_id=document.id,
        kind=RunKind.SAMPLE.value,
        mapping_version=0,  # PDF structuring has no mapping
        scope={
            "structurer": structurer_name,
            "with_images": with_images,
            "profile": profile.model_dump(),
            "span_ids": [str(a.id) for a in spans],
            "model": model_spec or {},
        },
        status=ConversionStatus.DONE.value,
        triggered_by=triggered_by,
        finished_at=datetime.now(UTC),
    )
    db.add(run)
    await db.flush()
    converted: list[ConvertedEntry] = []
    usage_total = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "requests": 0}
    failed = 0
    errors = warnings = 0
    valid_index = 0
    for position, (annotation, result) in enumerate(zip(spans, results, strict=True)):
        for key in usage_total:
            usage_total[key] += int(result.usage.get(key, 0))
        issues: list[dict[str, Any]] = []
        if result.entry:
            issues = [i.as_dict() for i in validation.issues_for(valid_index)]
            valid_index += 1
        if result.error:
            failed += 1
            issues.append(
                {
                    "severity": "error",
                    "source": "structurer",
                    "code": "model-error",
                    "path": "entry",
                    "message": result.error,
                }
            )
        errors += sum(1 for i in issues if i["severity"] == "error")
        warnings += sum(1 for i in issues if i["severity"] != "error")
        row = ConvertedEntry(
            run_id=run.id,
            entry_id=None,
            annotation_id=annotation.id,
            position=position,
            ordinal=annotation.position,
            headword=(
                str(
                    result.entry.get("headword")
                    or (annotation.payload or {}).get("headword_guess")
                    or ""
                )[:300]
                or None
            ),
            dmlex=result.entry,
            issues=issues,
            issue_count=len(issues),
            confidence=result.confidence,
            notes=list(result.notes),
        )
        db.add(row)
        converted.append(row)
    run.stats = {
        "requested": len(spans),
        "converted": sum(1 for r in results if r.entry),
        "failed": failed,
        "with_issues": sum(1 for c in converted if c.issue_count),
        "errors": errors,
        "warnings": warnings,
        "resource_issues": len(validation.resource_issues),
        "confidence_mean": round(statistics.fmean([r.confidence for r in results if r.entry]), 3)
        if any(r.entry for r in results)
        else 0.0,
        "low_confidence": sum(1 for r in results if r.entry and r.confidence < 0.6),
        "usage": usage_total,
        "structurer": structurer_name,
    }
    run.validation_report = {
        "summary": validation.report.summary(),
        "valid": validation.report.valid and failed == 0,
        "error_count": validation.report.error_count,
        "limitation_count": validation.report.limitation_count,
        "resource_issues": [i.as_dict() for i in validation.resource_issues],
        "profile": profile.model_dump(),
    }
    if model_spec and (usage_total["input_tokens"] or usage_total["output_tokens"]):
        db.add(
            UsageRecord(
                user_id=triggered_by,
                project_id=project_id,
                run_id=None,
                provider=str(model_spec.get("provider") or "unknown"),
                model=str(model_spec.get("model_id") or "unknown"),
                purpose="structure",
                input_tokens=usage_total["input_tokens"],
                output_tokens=usage_total["output_tokens"],
            )
        )
    await publish(
        db,
        project_id,
        "run.finished",
        {"run_id": str(run.id), "kind": run.kind, "structurer": structurer_name},
    )
    await db.flush()
    return StructuringOutcome(
        run=run, converted=converted, results=results, usage=usage_total, failed=failed
    )


__all__ = [
    "MAX_SAMPLE_SPANS",
    "StructuringError",
    "StructuringOutcome",
    "estimate_for",
    "pick_spans",
    "structure_sample",
    "usable_spans",
]
