"""PDF source tools: analyse the layout and segment pages into entry spans (M2.2)."""

from __future__ import annotations

from collections import Counter
from typing import Any, Literal

from pydantic import ValidationError
from pydantic_ai import ApprovalRequired, BinaryContent, RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset

from elexifier_agent.contracts import ToolResult, UiAction, error_result, to_tool_return
from elexifier_agent.deps import AgentDeps
from elexifier_agent.pdf_service import (
    SYNC_SEGMENT_MAX_PAGES,
    PdfServiceError,
    annotation_counts,
    crop_png,
    lines_in_region,
    load_layout,
    load_layouts,
    page_image,
    page_rows,
    profile_layouts,
    run_segmentation,
    train_learned_segmenter,
)
from elexifier_agent.structuring import (
    StructuringError,
    estimate_for,
    full_texts,
    pick_spans,
    queue_full_structuring,
    structure_sample,
    usable_spans,
)
from elexifier_agent.tools._entries import ToolInputError, get_document
from elexifier_core.db.models import Document, File, Project
from elexifier_pipeline.pdf.structurers import StructureProfile

pdf_toolset: FunctionToolset[AgentDeps] = FunctionToolset()

PROFILE_PAGES = 12  # pages read for the profile (spread over the document)
HEADWORDS_IN_RESULT = 40


async def _pdf_document(deps: AgentDeps, db: Any, document_id: str | None) -> Document:
    document = await get_document(deps, db, document_id)
    if document.type != "pdf":
        raise ToolInputError("this tool works on PDF documents; the document is XML")
    return document


@pdf_toolset.tool
async def pdf_analyze(ctx: RunContext[AgentDeps], document_id: str | None = None) -> ToolReturn:
    """Describe a PDF dictionary before segmenting it: pages, pages without a text layer,
    column layout, fonts and sizes with their shares, how many lines start with a bold word,
    a few first lines, and the current segmentation if any. Start here for a PDF project.

    Args:
        document_id: The PDF document. Omit when the project has a single analysed document.
    """
    deps = ctx.deps
    async with deps.session() as db:
        try:
            document = await _pdf_document(deps, db, document_id)
        except ToolInputError as exc:
            return error_result(str(exc))
        pages = await page_rows(db, document.id)
        if not pages:
            return error_result("the PDF has not been analysed yet (no pages); wait for the layout")
        step = max(1, len(pages) // PROFILE_PAGES)
        picked = pages[::step][:PROFILE_PAGES]
        layouts = await load_layouts(deps.storage, picked)
        profile = profile_layouts(layouts)
        profile["pages"] = len(pages)
        profile["scanned_pages"] = sum(1 for p in pages if p.is_scanned)
        profile["text_pages"] = len(pages) - profile["scanned_pages"]
        profile["profiled_pages"] = [p.page_no for p in picked]
        segmentation = (document.stats or {}).get("segmentation")
        counts = await annotation_counts(db, document.id)
        doc_id = str(document.id)
        name = (document.stats or {}).get("file_name") or doc_id
    warnings = []
    if profile["scanned_pages"]:
        warnings.append(
            f"{profile['scanned_pages']} page(s) have no text layer (scanned or empty); they "
            "cannot be segmented without OCR"
        )
    columns = profile["columns"]
    column_text = " / ".join(f"{v} pages with {k} column(s)" for k, v in columns.items())
    summary = (
        f"{profile['pages']} pages ({profile['text_pages']} with text), "
        f"{column_text or 'no columns'}, "
        f"body size {profile['body_size']}, {profile['bold_start_share']:.0%} of lines start "
        f"with a bold word, {profile['lines_per_page']} lines per page."
        + (
            f" Headword font: {profile['headword_font']['font']} "
            f"{profile['headword_font']['size']} (starts flush-left lines and appears almost "
            "nowhere else)."
            if profile.get("headword_font")
            else " No distinct headword font found."
        )
        + (
            f" Segmented: {segmentation['stats']['spans']} spans "
            f"({segmentation['segmenter']}, mode {segmentation['stats']['mode']})."
            if segmentation
            else " Not segmented yet."
        )
    )
    return to_tool_return(
        ToolResult(
            summary=summary[:500],
            data={
                "document_id": doc_id,
                "name": name,
                **profile,
                "segmentation": segmentation,
                "annotations": counts,
                "warnings": warnings,
            },
            ui_actions=[UiAction(type="open_document", document_id=doc_id, page=1)],
        )
    )


@pdf_toolset.tool
async def pdf_segment(
    ctx: RunContext[AgentDeps],
    document_id: str | None = None,
    segmenter: str = "rule",
    params: dict[str, Any] | None = None,
) -> ToolReturn:
    """Split the pages into entry spans and show them as boxes on the pages for review.
    Replaces the previous proposals (decisions the lexicographer already made are kept).
    Rule segmenter parameters: mode ("auto" | "font" | "bold" | "size" | "indent"),
    headword_font and headword_size (font mode: the font that starts entries, e.g. "Arial-BoldMT"
    8.0; auto picks it when pdf_analyze reports one), headword_size_delta (points above the body
    size), indent_tolerance, skip_margin_share, max_headword_words. Run pdf_analyze first, then
    adjust when the counts look wrong.

    Args:
        document_id: The PDF document. Omit when the project has one.
        segmenter: "rule" (built in) or a plugin name.
        params: Parameters for the segmenter (see above); omit for the defaults.
    """
    deps = ctx.deps
    async with deps.session() as db:
        try:
            document = await _pdf_document(deps, db, document_id)
            pages = await page_rows(db, document.id)
            if len(pages) > SYNC_SEGMENT_MAX_PAGES and deps.defer_job is not None:
                job_id = await deps.defer_job(
                    "pdf.segment",
                    {
                        "document_id": str(document.id),
                        "segmenter": segmenter,
                        "params": params or {},
                        "user_id": str(deps.user.id),
                    },
                )
                await db.commit()
                return to_tool_return(
                    ToolResult(
                        summary=f"Segmentation of {len(pages)} pages queued (job {job_id}); "
                        "the boxes appear on the pages when it finishes.",
                        data={"document_id": str(document.id), "job_id": job_id, "queued": True},
                        ui_actions=[UiAction(type="open_document", document_id=str(document.id))],
                    )
                )
            spans, stats, set_id = await run_segmentation(
                db,
                deps.storage,
                document,
                segmenter_name=segmenter,
                params=params,
                created_by=deps.user.id,
            )
            await db.commit()
        except (ToolInputError, PdfServiceError) as exc:
            return error_result(str(exc))
        doc_id = str(document.id)
    per_page: dict[int, int] = {}
    for span in spans:
        per_page[span.page] = per_page.get(span.page, 0) + 1
    heads = [s.headword_guess for s in spans if s.headword_guess and not s.continuation]
    first_page = spans[0].page if spans else 1
    summary = (
        f"{stats.spans} entry spans on {stats.pages} pages with the {segmenter} segmenter "
        f"(mode {stats.mode}, {stats.continuations} continuations, mean confidence "
        f"{stats.confidence_mean:.2f}). "
        + ("Warnings: " + "; ".join(stats.warnings) + ". " if stats.warnings else "")
        + "The boxes are on the pages; ask the lexicographer to review a page or two."
    )
    return to_tool_return(
        ToolResult(
            summary=summary[:500],
            data={
                "document_id": doc_id,
                "set_id": str(set_id),
                "segmenter": segmenter,
                "params": params or {},
                "stats": stats.model_dump(),
                "spans_per_page": {str(k): v for k, v in sorted(per_page.items())[:60]},
                "first_headwords": heads[:HEADWORDS_IN_RESULT],
                "low_confidence": [
                    {
                        "id": s.id,
                        "page": s.page,
                        "headword": s.headword_guess,
                        "confidence": s.confidence,
                    }
                    for s in sorted(spans, key=lambda s: s.confidence)[:10]
                ],
            },
            ui_actions=[
                UiAction(
                    type="show_overlay",
                    document_id=doc_id,
                    overlay_set_id=str(set_id),
                    page=first_page,
                )
            ],
        )
    )


MAX_REGION_CHARS = 6000
PAGE_IMAGE_DPI = 110


@pdf_toolset.tool
async def pdf_get_page(
    ctx: RunContext[AgentDeps],
    page: int,
    bbox: list[float] | None = None,
    document_id: str | None = None,
    with_image: bool | None = None,
) -> ToolReturn:
    """Read a page (or a region of it) of the PDF: the text lines in reading order with their
    boxes, and, when the model can see images, a rendering of the region. Use it to look at a
    passage the lexicographer points at, to check what a segmented span contains, or to see the
    typography before choosing segmentation parameters.

    Args:
        page: 1-based page number.
        bbox: Optional region [x0, top, x1, bottom] in PDF points (origin top-left), for example
            the box of an annotation; omit for the whole page.
        document_id: The PDF document. Omit when the project has one.
        with_image: Attach the rendered page or region as an image (default: yes when the model
            supports images).
    """
    deps = ctx.deps
    region: tuple[float, float, float, float] | None = None
    if bbox is not None:
        if len(bbox) != 4 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            return error_result("bbox must be [x0, top, x1, bottom] with x1 > x0 and bottom > top")
        region = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    async with deps.session() as db:
        try:
            document = await _pdf_document(deps, db, document_id)
        except ToolInputError as exc:
            return error_result(str(exc))
        pages = await page_rows(db, document.id)
        row = next((p for p in pages if p.page_no == page), None)
        if row is None:
            return error_result(f"page {page} does not exist (the document has {len(pages)} pages)")
        file = await db.get(File, document.source_file_id)
        if file is None:
            return error_result("the source file of this document is gone")
        layout = await load_layout(deps.storage, row)
        lines = lines_in_region(layout, region)
        vision = deps.model_info.supports_vision if deps.model_info else None
        attach = with_image if with_image is not None else bool(vision)
        image: bytes | None = None
        if attach:
            png = await page_image(deps.storage, document, file, page, PAGE_IMAGE_DPI)
            image = crop_png(png, region, PAGE_IMAGE_DPI) if region else png
        doc_id, file_id = str(document.id), str(file.id)
    text_lines = []
    used = 0
    for line in lines:
        entry = {
            "id": line.id,
            "text": line.text,
            "bbox": [
                round(line.x0, 1),
                round(line.top, 1),
                round(line.x1, 1),
                round(line.bottom, 1),
            ],
            "column": line.column,
            "bold_start": bool(line.words and line.words[0].bold),
        }
        used += len(line.text)
        if used > MAX_REGION_CHARS:
            break
        text_lines.append(entry)
    truncated = len(text_lines) < len(lines)
    where = f"region {[round(v) for v in region]} of page {page}" if region else f"page {page}"
    summary = (
        f"{len(lines)} line(s) in {where}"
        + (f", showing {len(text_lines)}" if truncated else "")
        + ("; the image is attached." if image is not None else ".")
        + (" The page has no text layer." if row.is_scanned else "")
    )
    result = ToolResult(
        summary=summary,
        data={
            "document_id": doc_id,
            "page": page,
            "bbox": list(region) if region else None,
            "page_size": [layout.width, layout.height],
            "lines": text_lines,
            "truncated": truncated,
            "text": "\n".join(line["text"] for line in text_lines),
        },
        ui_actions=[UiAction(type="open_page", file_id=file_id, page=page, bbox=region)],
    )
    content = [BinaryContent(data=image, media_type="image/png")] if image is not None else None
    return to_tool_return(result, content=content)


async def _profile_from(
    db: Any, deps: AgentDeps, profile: dict[str, Any] | None
) -> tuple[StructureProfile, dict[str, Any]]:
    """The structure profile: the call's values over the ones stored on the project."""
    project = await db.get(Project, deps.project.id)
    settings = (project.settings if project is not None else None) or deps.project.settings or {}
    stored = dict(settings.get("structure_profile") or {})
    merged = {**stored, **{k: v for k, v in (profile or {}).items() if v not in (None, "", [])}}
    try:
        return StructureProfile.model_validate(merged), merged
    except ValidationError as exc:
        raise ToolInputError(f"invalid profile: {exc.errors()[0]['msg']}") from exc


async def _store_profile(db: Any, project_id: Any, profile: dict[str, Any]) -> None:
    project = await db.get(Project, project_id)
    if project is not None:
        project.settings = {**(project.settings or {}), "structure_profile": profile}


@pdf_toolset.tool
async def pdf_estimate_cost(
    ctx: RunContext[AgentDeps],
    document_id: str | None = None,
    with_images: bool | None = None,
) -> ToolReturn:
    """Estimate the tokens and cost of structuring every reviewed entry span with the current
    model (accepted spans, or all proposals when nothing was reviewed yet). Show this to the
    lexicographer before a full structuring run.

    Args:
        document_id: The PDF document. Omit when the project has one.
        with_images: Include a page crop per entry (default: yes when the model sees images).
    """
    deps = ctx.deps
    async with deps.session() as db:
        try:
            document = await _pdf_document(deps, db, document_id)
        except ToolInputError as exc:
            return error_result(str(exc))
        spans = await usable_spans(db, document)
        texts = await full_texts(db, spans)
        doc_id = str(document.id)
    info = deps.model_info
    images = with_images if with_images is not None else bool(info and info.supports_vision)
    estimate = estimate_for(
        spans,
        with_images=images,
        price_in_per_m=info.price_in_per_m if info else None,
        price_out_per_m=info.price_out_per_m if info else None,
        texts=texts,
    )
    reviewed = sum(1 for s in spans if s.status in ("accepted", "edited"))
    model_label = (deps.model_spec or {}).get("model_id") or (info.id if info else "the model")
    summary = (
        f"{estimate.entries} entry spans ({reviewed} reviewed) with {model_label}: about "
        f"{estimate.input_tokens:,} input and {estimate.output_tokens:,} output tokens"
        + (f", roughly ${estimate.usd_estimate:.2f}" if estimate.usd_estimate is not None else "")
        + ("; page crops included" if images else "; text only")
        + "."
        + (" " + " ".join(estimate.notes) if estimate.notes else "")
    )
    return to_tool_return(
        ToolResult(
            summary=summary[:500],
            data={"document_id": doc_id, **estimate.model_dump(), "reviewed_spans": reviewed},
        )
    )


@pdf_toolset.tool
async def pdf_structure(
    ctx: RunContext[AgentDeps],
    kind: Literal["sample", "full"] = "sample",
    n: int = 10,
    span_ids: list[str] | None = None,
    strategy: Literal["first", "spread"] = "spread",
    profile: dict[str, Any] | None = None,
    structurer: Literal["llm", "rule"] = "llm",
    with_images: bool | None = None,
    document_id: str | None = None,
) -> ToolReturn:
    """Turn entry spans into DMLex entries with the model (or the rule baseline) and validate
    them. kind="sample" structures up to 20 spans right away, stores a run and opens the results
    next to the page for review. kind="full" structures every reviewed span in the background
    after the lexicographer approves (show pdf_estimate_cost first): unchanged spans reuse
    earlier results, progress shows in the run panel, and the DMLex XML and JSON files are
    written and validated at the end; use run_stats when it has finished. Give the profile once
    (source language, target languages, what an entry contains); it is remembered.

    Args:
        kind: "sample" or "full".
        n: Sample size (1-20) when span_ids are not given.
        span_ids: Structure exactly these annotation ids.
        strategy: "spread" across the reviewed spans or the "first" ones.
        profile: {"source_language": "en", "target_languages": ["sl"], "contents": "..."}.
        structurer: "llm" (the project's model) or "rule" (headword + one definition, free).
        with_images: Send a page crop per entry (default: yes when the model sees images).
        document_id: The PDF document. Omit when the project has one.
    """
    deps = ctx.deps
    if kind == "full" and not ctx.tool_call_approved:
        raise ApprovalRequired(
            metadata={"reason": "structures every reviewed span with the model and writes files"}
        )
    async with deps.session() as db:
        try:
            document = await _pdf_document(deps, db, document_id)
            struct_profile, merged = await _profile_from(db, deps, profile)
            spans = await usable_spans(db, document)
            info = deps.model_info
            images = with_images if with_images is not None else bool(info and info.supports_vision)
            if kind == "full":
                estimate = estimate_for(
                    spans,
                    with_images=images and structurer == "llm",
                    price_in_per_m=info.price_in_per_m if info else None,
                    price_out_per_m=info.price_out_per_m if info else None,
                )
                if structurer == "rule":
                    estimate = estimate.model_copy(
                        update={"input_tokens": 0, "output_tokens": 0, "usd_estimate": 0.0}
                    )
                run = await queue_full_structuring(
                    db,
                    document=document,
                    profile=struct_profile,
                    structurer_name=structurer,
                    with_images=images and structurer == "llm",
                    triggered_by=deps.user.id,
                    model_spec=deps.model_spec if structurer == "llm" else {},
                    project_id=deps.project.id,
                    estimate=estimate,
                    defer=deps.defer_job,
                )
                if profile:
                    await _store_profile(db, deps.project.id, merged)
                await db.commit()
                run_id = str(run.id)
                return to_tool_return(
                    ToolResult(
                        summary=f"Full structuring of {len(spans)} spans queued with the "
                        f"{structurer} structurer (about {estimate.input_tokens:,} input and "
                        f"{estimate.output_tokens:,} output tokens"
                        + (
                            f", roughly ${estimate.usd_estimate:.2f}"
                            if estimate.usd_estimate is not None
                            else ""
                        )
                        + "). The run panel shows progress; use run_stats when it has finished.",
                        data={
                            "run_id": run_id,
                            "spans": len(spans),
                            "estimate": estimate.model_dump(),
                            "structurer": structurer,
                            "with_images": images and structurer == "llm",
                        },
                        ui_actions=[UiAction(type="open_run", run_id=run_id)],
                    )
                )
            if span_ids:
                wanted = set(span_ids)
                chosen = [s for s in spans if str(s.id) in wanted]
                if not chosen:
                    raise ToolInputError("none of the span_ids match a usable span")
            else:
                chosen = pick_spans(spans, n, strategy)
            outcome = await structure_sample(
                db,
                deps.storage,
                document=document,
                spans=chosen,
                profile=struct_profile,
                model=deps.model,
                structurer_name=structurer,
                with_images=images and structurer == "llm",
                triggered_by=deps.user.id,
                model_spec=deps.model_spec,
                project_id=deps.project.id,
            )
            if profile:
                await _store_profile(db, deps.project.id, merged)
            await db.commit()
        except (ToolInputError, StructuringError) as exc:
            return error_result(str(exc))
        run = outcome.run
        run_id, stats = str(run.id), dict(run.stats)
        rows = []
        for row, result in zip(outcome.converted, outcome.results, strict=True):
            item: dict[str, Any] = {
                "annotation_id": str(row.annotation_id),
                "page": next((s.page_no for s in chosen if s.id == row.annotation_id), None),
                "headword": row.headword,
                "confidence": row.confidence,
                "notes": row.notes,
                "issues": row.issues,
                "segmentation": result.segmentation,
            }
            if result.segmentation != "ok":
                item["segmentation_reason"] = result.segmentation_reason
            if len(rows) < 3:
                item["dmlex"] = row.dmlex
            if result.error:
                item["error"] = result.error
            rows.append(item)
        doc_id = str(document.id)
    summary = (
        f"Structured {stats['converted']} of {stats['requested']} spans with the {structurer} "
        f"structurer: {stats['with_issues']} with issues ({stats['errors']} errors, "
        f"{stats['warnings']} warnings), mean confidence {stats['confidence_mean']:.2f}, "
        f"{stats['low_confidence']} below 0.6"
        + (f", {stats['failed']} model failures" if stats.get("failed") else "")
        + _segmentation_clause(stats)
        + f"; {stats['usage']['input_tokens']:,} input / {stats['usage']['output_tokens']:,} "
        "output tokens. Open the entries next to the page and ask the lexicographer to check them."
    )
    return to_tool_return(
        ToolResult(
            summary=summary[:500],
            data={
                "run_id": run_id,
                "document_id": doc_id,
                "stats": stats,
                "profile": struct_profile.model_dump(),
                "entries": rows,
                "note": "entries after the first 3 only list issues; the run panel shows all. "
                "Spans judged merged or fragment carry that flag in the review panel: the "
                "lexicographer splits or merges the boxes (Edit boxes), or the segmentation is "
                "redone, and they are structured again.",
            },
            cost={"tokens": stats["usage"]},
            ui_actions=[UiAction(type="open_run", run_id=run_id)],
        )
    )


@pdf_toolset.tool
async def pdf_train_segmenter(
    ctx: RunContext[AgentDeps], document_id: str | None = None, apply: bool = False
) -> ToolReturn:
    """Learn entry starts from the lexicographer's decisions (accepted, edited and drawn spans
    on the pages they reviewed; rejected proposals count against) and use the model as a
    second opinion on the pages nobody reviewed: proposals it disagrees with get the flag
    learned_disagrees and their pages join the review sample. With apply=True its spans replace
    the proposals on the unreviewed pages (reviewed pages keep theirs); the "learned" segmenter
    can also be run with pdf_segment afterwards. Needs decisions on a few pages first; the
    report says how well the model reproduces held-out reviewed pages (F1).

    Args:
        document_id: The PDF document. Omit when the project has one.
        apply: Replace the proposals on unreviewed pages with the learned segmentation.
    """
    deps = ctx.deps
    async with deps.session() as db:
        try:
            document = await _pdf_document(deps, db, document_id)
            outcome = await train_learned_segmenter(
                db, deps.storage, document, apply=apply, created_by=deps.user.id
            )
            await db.commit()
        except (ToolInputError, PdfServiceError) as exc:
            return error_result(str(exc))
        doc_id = str(document.id)
    report = outcome.report
    kinds = Counter(d["kind"] for d in outcome.disagreements)
    summary = (
        f"Trained on {report.pages} reviewed pages ({report.positives} entry starts, "
        f"{report.lines} lines): "
        + (
            f"held-out F1 {report.f1:.2f} (precision {report.precision:.2f}, recall "
            f"{report.recall:.2f}, {report.held_out_pages} pages held out)"
            if report.held_out_pages
            else f"F1 {report.f1:.2f} on the training pages (too few pages to hold any out)"
        )
        + f". Second opinion on {outcome.unreviewed_pages} unreviewed pages: "
        + (
            f"{outcome.applied_spans} learned spans replaced the proposals on "
            f"{outcome.applied_pages} pages."
            if outcome.applied
            else (
                f"{len(outcome.disagreements)} disagreements on {len(outcome.pages_flagged)} "
                f"pages ({kinds.get('not_a_start', 0)} proposals the model would not start, "
                f"{kinds.get('missed_start', 0)} starts inside a proposal), flagged "
                "learned_disagrees and added to the review sample."
                if outcome.disagreements
                else "no disagreements with the current proposals."
            )
        )
        + (" Warnings: " + "; ".join(report.warnings) + "." if report.warnings else "")
    )
    first_page = outcome.pages_flagged[0] if outcome.pages_flagged else None
    return to_tool_return(
        ToolResult(
            summary=summary[:500],
            data={
                "document_id": doc_id,
                "report": report.model_dump(),
                "unreviewed_pages": outcome.unreviewed_pages,
                "disagreements": len(outcome.disagreements),
                "disagreement_kinds": dict(kinds),
                "pages_flagged": outcome.pages_flagged[:60],
                "applied": outcome.applied,
                "applied_pages": outcome.applied_pages,
                "applied_spans": outcome.applied_spans,
                "model_key": outcome.model_key,
            },
            ui_actions=[UiAction(type="open_document", document_id=doc_id, page=first_page)]
            if first_page
            else [],
        )
    )


def _segmentation_clause(stats: dict[str, Any]) -> str:
    doubts = stats.get("segmentation") or {}
    if not doubts:
        return ""
    parts = ", ".join(f"{n} {verdict.replace('_', ' ')}" for verdict, n in doubts.items())
    return f"; segmentation doubts: {parts}"


@pdf_toolset.tool
async def pdf_review_summary(
    ctx: RunContext[AgentDeps], document_id: str | None = None
) -> ToolReturn:
    """What the lexicographer should look at instead of every page: the review sample (first and
    last pages, one page per letter, every page with an oddity), the flagged spans with their
    reasons, and how many proposals are still undecided. Use it after pdf_segment to propose a
    review of manageable size, and to explain what "accept the rest" would accept.

    Args:
        document_id: The PDF document. Omit when the project has one.
    """
    deps = ctx.deps
    async with deps.session() as db:
        try:
            document = await _pdf_document(deps, db, document_id)
        except ToolInputError as exc:
            return error_result(str(exc))
        review = (document.stats or {}).get("review")
        counts = await annotation_counts(db, document.id)
        doc_id = str(document.id)
    if not review:
        return error_result("no segmentation yet; run pdf_segment first")
    flagged_pages = review.get("flagged_pages", [])
    summary = (
        f"{review['entries']:,} entries on {review['text_pages']:,} pages (median "
        f"{review['median_entries_per_page']:g} per page). {review['flagged_spans']:,} flagged "
        f"spans on {len(flagged_pages)} pages; review sample: {len(review['sample_pages'])} "
        f"pages. Proposals still undecided: {counts.get('proposed', 0):,}; accepted: "
        f"{counts.get('accepted', 0):,}; rejected: {counts.get('rejected', 0):,}."
    )
    return to_tool_return(
        ToolResult(
            summary=summary[:500],
            data={
                "document_id": doc_id,
                "sample_pages": review["sample_pages"],
                "letter_pages": review.get("letter_pages", []),
                "flagged_pages": flagged_pages[:60],
                "reasons": review.get("reasons", {}),
                "counts": counts,
                "note": "reasons: few_entries/many_entries/no_entries per page; very_long, "
                "many_lines, very_short, no_headword, numeric_headword, long_headword, "
                "low_confidence, continuation_mid_page per span",
            },
            ui_actions=[
                UiAction(
                    type="open_document",
                    document_id=doc_id,
                    page=(review["sample_pages"] or [1])[0],
                )
            ],
        )
    )
