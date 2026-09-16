"""XML source tools: profile, sample, search, test XPath, distinct values, mapping and spec."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from lxml import etree
from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset
from sqlalchemy import func, select

from elexifier_agent.contracts import ToolResult, UiAction, error_result, to_tool_return
from elexifier_agent.conversion import latest_mapping
from elexifier_agent.deps import AgentDeps
from elexifier_agent.tools._entries import (
    MAX_SAMPLE_ENTRIES,
    ToolInputError,
    clip,
    get_document,
    parse_entry,
    pick_entries,
    reader_for,
)
from elexifier_core.db.models import ConversionSpec, Mapping
from elexifier_pipeline.xml.dmlexifier import MappingError
from elexifier_pipeline.xml.mapping import (
    check_mapping,
    has_errors,
    load_mapping_spec,
    summarize,
)

xml_toolset: FunctionToolset[AgentDeps] = FunctionToolset()

DISTINCT_SCAN_LIMIT = 5000


def _entry_action(entry: Any, document_id: str) -> UiAction:
    return UiAction(type="open_entry", entry_id=str(entry.id), document_id=document_id)


@xml_toolset.tool
async def xml_profile(ctx: RunContext[AgentDeps], document_id: str | None = None) -> ToolReturn:
    """Describe the structure of an analysed XML document: root, element counts and depths, which
    element holds the entries, attributes and sample values. Start here for a new source.

    Args:
        document_id: The document to profile. Omit when the project has a single analysed document.
    """
    async with ctx.deps.session() as db:
        try:
            document = await get_document(ctx.deps, db, document_id)
        except ToolInputError as exc:
            return error_result(str(exc))
    stats = document.stats or {}
    elements = [
        {
            "tag": e["tag"],
            "count": e["count"],
            "depth": e["depth"],
            "parents": e.get("parents", {}),
            "attributes": e.get("attributes", {}),
            "has_text": e.get("has_text", False),
            "sample_values": e.get("sample_values", [])[:3],
        }
        for e in stats.get("element_stats", [])[:40]
    ]
    candidates = stats.get("entry_candidates", [])[:5]
    summary = (
        f"{document.entry_count:,} entries under {document.entry_xpath} "
        f"(root <{stats.get('root_tag')}>, {stats.get('total_elements', 0):,} elements, "
        f"{len(stats.get('element_stats', []))} distinct element names)"
    )
    return to_tool_return(
        ToolResult(
            summary=summary,
            data={
                "document_id": str(document.id),
                "status": document.status,
                "entry_xpath": document.entry_xpath,
                "entry_count": document.entry_count,
                "root_tag": stats.get("root_tag"),
                "namespaces": stats.get("namespaces", {}),
                "doctype": stats.get("doctype"),
                "size_bytes": stats.get("size_bytes"),
                "entry_candidates": candidates,
                "elements": elements,
                "note": "paths in the mapping are relative to the entry element; "
                "use xml_test_xpath to try selectors on real entries",
            },
            ui_actions=[UiAction(type="open_document", document_id=str(document.id))],
        )
    )


@xml_toolset.tool
async def xml_sample_entries(
    ctx: RunContext[AgentDeps],
    n: int = 3,
    strategy: str = "first",
    query: str | None = None,
    ordinals: list[int] | None = None,
    document_id: str | None = None,
) -> ToolReturn:
    """Show the source XML of a few entries (at most 20, each clipped to 4,000 characters).

    Args:
        n: How many entries.
        strategy: "first", "random" or "spread" (evenly across the dictionary).
        query: Only entries whose headword or text contains this string.
        ordinals: Specific entry numbers (0-based) instead of a strategy.
        document_id: The document; omit when the project has one analysed document.
    """
    async with ctx.deps.session() as db:
        try:
            document = await get_document(ctx.deps, db, document_id)
            entries = await pick_entries(
                db, document, n=n, strategy=strategy, ordinals=ordinals, query=query
            )
            reader = await reader_for(ctx.deps, db, document)
        except ToolInputError as exc:
            return error_result(str(exc))
    if not entries:
        return error_result("no entries matched", query=query, ordinals=ordinals)
    samples = []
    for entry in entries:
        text, truncated = clip((await reader.read(entry)).decode("utf-8", errors="replace"))
        samples.append(
            {
                "ordinal": entry.ordinal,
                "entry_id": str(entry.id),
                "headword": entry.headword,
                "xml": text,
                "truncated": truncated,
            }
        )
    return to_tool_return(
        ToolResult(
            summary=f"{len(samples)} sample entr{'y' if len(samples) == 1 else 'ies'} "
            f"({strategy}{' matching ' + repr(query) if query else ''}): "
            + ", ".join(str(s["headword"]) for s in samples[:8]),
            data={"document_id": str(document.id), "entries": samples},
            ui_actions=[_entry_action(entries[0], str(document.id))],
        )
    )


@xml_toolset.tool
async def xml_search(
    ctx: RunContext[AgentDeps], query: str, limit: int = 10, document_id: str | None = None
) -> ToolReturn:
    """Find entries whose headword or text contains a string; returns headwords and snippets.

    Args:
        query: Text to look for (case-insensitive).
        limit: Maximum number of hits (at most 50).
        document_id: The document; omit when the project has one analysed document.
    """
    limit = max(1, min(int(limit), 50))
    async with ctx.deps.session() as db:
        try:
            document = await get_document(ctx.deps, db, document_id)
        except ToolInputError as exc:
            return error_result(str(exc))
        from elexifier_core.db.models import Entry

        pattern = f"%{query.strip()}%"
        condition = Entry.headword.ilike(pattern) | Entry.snippet.ilike(pattern)
        total = (
            await db.execute(
                select(func.count())
                .select_from(Entry)
                .where(Entry.document_id == document.id, condition)
            )
        ).scalar_one()
        rows = (
            (
                await db.execute(
                    select(Entry)
                    .where(Entry.document_id == document.id, condition)
                    .order_by(Entry.ordinal)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    hits = [
        {"ordinal": e.ordinal, "entry_id": str(e.id), "headword": e.headword, "snippet": e.snippet}
        for e in rows
    ]
    return to_tool_return(
        ToolResult(
            summary=f"{total} entr{'y' if total == 1 else 'ies'} match {query!r}"
            + (f"; showing {len(hits)}" if total > len(hits) else ""),
            data={"document_id": str(document.id), "total": total, "hits": hits},
            ui_actions=[_entry_action(rows[0], str(document.id))] if rows else [],
        )
    )


def _xpath_result_summary(result: Any) -> list[str]:
    if not isinstance(result, list):
        return [str(result)]
    out: list[str] = []
    for item in result[:10]:
        if isinstance(item, etree._Element):
            text = " ".join("".join(str(t) for t in item.itertext()).split())
            out.append(f"<{etree.QName(item).localname}> {text[:120]}")
        else:
            out.append(str(item)[:160])
    return out


@xml_toolset.tool
async def xml_test_xpath(
    ctx: RunContext[AgentDeps], xpath: str, n: int = 5, document_id: str | None = None
) -> ToolReturn:
    """Evaluate an XPath expression relative to a few entry elements and show what it selects.
    Use it to verify every selector before writing it into the mapping.

    Args:
        xpath: Expression relative to the entry element (for example "hg/ps" or ".//Headword").
        n: Number of entries to test (at most 20).
        document_id: The document; omit when the project has one analysed document.
    """
    async with ctx.deps.session() as db:
        try:
            document = await get_document(ctx.deps, db, document_id)
            entries = await pick_entries(db, document, n=n, strategy="spread")
            reader = await reader_for(ctx.deps, db, document)
        except ToolInputError as exc:
            return error_result(str(exc))
    namespaces = (document.stats or {}).get("namespaces") or None
    try:
        compiled = etree.XPath(xpath, namespaces=namespaces)
    except etree.XPathSyntaxError as exc:
        return error_result(f"invalid XPath: {exc}", xpath=xpath)
    results = []
    matched = 0
    for entry in entries:
        element = parse_entry(await reader.read(entry), document)
        try:
            value = compiled(element)
        except etree.XPathEvalError as exc:
            return error_result(f"XPath failed on entry {entry.ordinal}: {exc}", xpath=xpath)
        found = _xpath_result_summary(value)
        count = len(value) if isinstance(value, list) else 1
        if count:
            matched += 1
        results.append(
            {"ordinal": entry.ordinal, "headword": entry.headword, "count": count, "values": found}
        )
    return to_tool_return(
        ToolResult(
            summary=f"{xpath!r} matched in {matched} of {len(entries)} sampled entries",
            data={"document_id": str(document.id), "xpath": xpath, "results": results},
        )
    )


@xml_toolset.tool
async def xml_distinct_values(
    ctx: RunContext[AgentDeps],
    xpath: str,
    limit: int = 40,
    max_entries: int = 2000,
    document_id: str | None = None,
) -> ToolReturn:
    """Collect the distinct values an XPath yields across many entries, with counts. Use it to
    design value translations (xlat), for example for part-of-speech codes or labels.

    Args:
        xpath: Expression relative to the entry element; element results use their text content.
        limit: How many of the most frequent values to return (at most 200).
        max_entries: How many entries to scan from the start of the document (at most 5000).
        document_id: The document; omit when the project has one analysed document.
    """
    limit = max(1, min(int(limit), 200))
    max_entries = max(1, min(int(max_entries), DISTINCT_SCAN_LIMIT))
    async with ctx.deps.session() as db:
        try:
            document = await get_document(ctx.deps, db, document_id)
            reader = await reader_for(ctx.deps, db, document)
        except ToolInputError as exc:
            return error_result(str(exc))
        from elexifier_core.db.models import Entry

        rows = (
            (
                await db.execute(
                    select(Entry)
                    .where(Entry.document_id == document.id)
                    .order_by(Entry.ordinal)
                    .limit(max_entries)
                )
            )
            .scalars()
            .all()
        )
    namespaces = (document.stats or {}).get("namespaces") or None
    try:
        compiled = etree.XPath(xpath, namespaces=namespaces)
    except etree.XPathSyntaxError as exc:
        return error_result(f"invalid XPath: {exc}", xpath=xpath)
    if len(rows) > 40:
        await reader.prepare_bulk()
    counter: Counter[str] = Counter()
    entries_with_value = 0
    try:
        for entry in rows:
            element = parse_entry(await reader.read(entry), document)
            value = compiled(element)
            items = value if isinstance(value, list) else [value]
            texts = []
            for item in items:
                if isinstance(item, etree._Element):
                    texts.append(" ".join("".join(str(t) for t in item.itertext()).split()))
                else:
                    texts.append(" ".join(str(item).split()))
            texts = [t for t in texts if t]
            if texts:
                entries_with_value += 1
            counter.update(texts)
    finally:
        reader.close()
    top = counter.most_common(limit)
    return to_tool_return(
        ToolResult(
            summary=f"{len(counter)} distinct value(s) of {xpath!r} in {len(rows)} entries "
            f"({entries_with_value} entries have one); top: "
            + ", ".join(f"{v!r} ({c})" for v, c in top[:6]),
            data={
                "document_id": str(document.id),
                "xpath": xpath,
                "entries_scanned": len(rows),
                "entries_with_value": entries_with_value,
                "distinct": len(counter),
                "values": [{"value": v, "count": c} for v, c in top],
                "truncated": len(counter) > limit,
            },
        )
    )


# --- mapping ------------------------------------------------------------------------------


@xml_toolset.tool
async def mapping_get(ctx: RunContext[AgentDeps]) -> ToolReturn:
    """Return the project's current mapping (latest version) with a plain-language summary."""
    async with ctx.deps.session() as db:
        mapping = await latest_mapping(db, ctx.deps.project.id)
    if mapping is None:
        return to_tool_return(
            ToolResult(
                summary="No mapping yet. Propose one with mapping_write after profiling the source."
            )
        )
    spec = load_mapping_spec(mapping.spec)
    return to_tool_return(
        ToolResult(
            summary=f"Mapping version {mapping.version}"
            + (f": {mapping.note}" if mapping.note else ""),
            data={
                "version": mapping.version,
                "mapping": mapping.spec,
                "summary": summarize(spec),
                "issues": mapping.issues,
            },
            ui_actions=[UiAction(type="open_mapping", mapping_version=mapping.version)],
        )
    )


@xml_toolset.tool
async def mapping_write(
    ctx: RunContext[AgentDeps], mapping: dict[str, Any] | str, note: str = ""
) -> ToolReturn:
    """Validate and store a new version of the mapping (the JSON descriptor tree). Returns the
    checks; a mapping with errors is not stored. Always test selectors first and describe the
    change to the lexicographer.

    Args:
        mapping: The complete mapping as a JSON object (or a JSON string).
        note: One sentence on what changed and why (shown in the version history).
    """
    raw: Any = mapping
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            return error_result(f"mapping is not valid JSON: {exc}")
    if not isinstance(raw, dict):
        return error_result("mapping must be a JSON object")
    try:
        spec = load_mapping_spec(raw)
    except MappingError as exc:
        return error_result(str(exc))
    issues = check_mapping(spec)
    issue_dicts = [i.model_dump() for i in issues]
    if has_errors(issues):
        error_count = sum(i.severity == "error" for i in issues)
        return to_tool_return(
            ToolResult(
                summary=f"Mapping not stored: {error_count} error(s). "
                "Fix them and call mapping_write again.",
                data={"stored": False, "issues": issue_dicts},
            )
        )
    async with ctx.deps.session() as db:
        latest = await latest_mapping(db, ctx.deps.project.id)
        version = (latest.version + 1) if latest else 1
        row = Mapping(
            project_id=ctx.deps.project.id,
            version=version,
            spec=spec.model_dump(exclude_none=True),
            note=note.strip()[:2000],
            issues=issue_dicts,
            created_by=ctx.deps.user.id,
            source="agent",
        )
        db.add(row)
        await db.commit()
    warnings = [i for i in issues if i.severity != "error"]
    return to_tool_return(
        ToolResult(
            summary=f"Stored mapping version {version}"
            + (f" with {len(warnings)} warning(s)" if warnings else "")
            + ". Run a sample conversion next.",
            data={
                "stored": True,
                "version": version,
                "issues": issue_dicts,
                "summary": summarize(spec),
            },
            ui_actions=[UiAction(type="open_mapping", mapping_version=version)],
        )
    )


@xml_toolset.tool
async def mapping_explain(ctx: RunContext[AgentDeps]) -> ToolReturn:
    """Explain the current mapping in plain language (source path -> DMLex element), for the
    lexicographer and for the conversion spec."""
    async with ctx.deps.session() as db:
        mapping = await latest_mapping(db, ctx.deps.project.id)
    if mapping is None:
        return to_tool_return(ToolResult(summary="No mapping yet."))
    text = summarize(load_mapping_spec(mapping.spec))
    return to_tool_return(
        ToolResult(
            summary=f"Mapping version {mapping.version} explained",
            data={"version": mapping.version, "explanation": text},
        )
    )


# --- conversion spec ----------------------------------------------------------------------


async def _latest_spec(db: Any, project_id: Any) -> ConversionSpec | None:
    return (
        await db.execute(
            select(ConversionSpec)
            .where(ConversionSpec.project_id == project_id)
            .order_by(ConversionSpec.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


@xml_toolset.tool
async def spec_read(ctx: RunContext[AgentDeps]) -> ToolReturn:
    """Read the conversion specification: the running record of every decision (what a source
    element means, how values are mapped, what is dropped and why)."""
    async with ctx.deps.session() as db:
        spec = await _latest_spec(db, ctx.deps.project.id)
    if spec is None:
        return to_tool_return(
            ToolResult(summary="The conversion spec is empty. Start it with spec_update.")
        )
    return to_tool_return(
        ToolResult(
            summary=f"Conversion spec version {spec.version} ({len(spec.markdown)} characters)",
            data={"version": spec.version, "markdown": spec.markdown},
            ui_actions=[UiAction(type="open_spec")],
        )
    )


@xml_toolset.tool
async def spec_update(ctx: RunContext[AgentDeps], markdown: str) -> ToolReturn:
    """Replace the conversion specification with a new full version (Markdown). Record decisions
    as they are made: source structure, entry element, each mapping rule in plain words, value
    translations, known problems and what was decided about them.

    Args:
        markdown: The complete specification text.
    """
    text = markdown.strip()
    if not text:
        return error_result("the specification text is empty")
    async with ctx.deps.session() as db:
        latest = await _latest_spec(db, ctx.deps.project.id)
        version = (latest.version + 1) if latest else 1
        db.add(
            ConversionSpec(
                project_id=ctx.deps.project.id,
                version=version,
                markdown=text,
                updated_by=ctx.deps.user.id,
            )
        )
        await db.commit()
    return to_tool_return(
        ToolResult(
            summary=f"Conversion spec updated to version {version}",
            data={"version": version, "characters": len(text)},
            ui_actions=[UiAction(type="open_spec")],
        )
    )


__all__ = ["MAX_SAMPLE_ENTRIES", "xml_toolset"]
