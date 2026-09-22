"""The conversion specification: the plain-language record of every decision, for XML and PDF
projects alike (the lexicographer edits the same text in the middle pane)."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset
from sqlalchemy import select

from elexifier_agent.contracts import ToolResult, UiAction, error_result, to_tool_return
from elexifier_agent.deps import AgentDeps
from elexifier_core.db.models import ConversionSpec

spec_toolset: FunctionToolset[AgentDeps] = FunctionToolset()


async def latest_spec(db: Any, project_id: Any) -> ConversionSpec | None:
    return (
        await db.execute(
            select(ConversionSpec)
            .where(ConversionSpec.project_id == project_id)
            .order_by(ConversionSpec.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


@spec_toolset.tool
async def spec_read(ctx: RunContext[AgentDeps]) -> ToolReturn:
    """Read the conversion specification: the running record of every decision (what a source
    element or a printed convention means, how values are mapped, what is dropped and why)."""
    async with ctx.deps.session() as db:
        spec = await latest_spec(db, ctx.deps.project.id)
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


@spec_toolset.tool
async def spec_update(ctx: RunContext[AgentDeps], markdown: str) -> ToolReturn:
    """Replace the conversion specification with a new full version (Markdown). Record decisions
    as they are made: the source structure or the printed conventions, the entry element or the
    entry cue, each rule in plain words, value translations, abbreviations and labels, known
    problems and what was decided about them.

    Args:
        markdown: The complete specification text.
    """
    text = markdown.strip()
    if not text:
        return error_result("the specification text is empty")
    async with ctx.deps.session() as db:
        latest = await latest_spec(db, ctx.deps.project.id)
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


__all__ = ["latest_spec", "spec_read", "spec_toolset", "spec_update"]
