"""PDF source tools: analyse the layout and segment pages into entry spans (M2.2)."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset

from elexifier_agent.contracts import ToolResult, UiAction, error_result, to_tool_return
from elexifier_agent.deps import AgentDeps
from elexifier_agent.pdf_service import (
    SYNC_SEGMENT_MAX_PAGES,
    PdfServiceError,
    annotation_counts,
    load_layouts,
    page_rows,
    profile_layouts,
    run_segmentation,
)
from elexifier_agent.tools._entries import ToolInputError, get_document
from elexifier_core.db.models import Document

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
