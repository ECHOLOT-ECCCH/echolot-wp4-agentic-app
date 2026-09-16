"""PDF source tools: analyse the layout and segment pages into entry spans (M2.2)."""

from __future__ import annotations

from typing import Any

from pydantic_ai import BinaryContent, RunContext, ToolReturn
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
)
from elexifier_agent.tools._entries import ToolInputError, get_document
from elexifier_core.db.models import Document, File

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
    Rule segmenter parameters: mode ("auto" | "bold" | "size" | "indent"),
    headword_size_delta (points above the body size), indent_tolerance, skip_margin_share,
    max_headword_words. Run pdf_analyze first, then adjust when the counts look wrong.

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
