"""The workspace's task notes: the agreed description of what happens to each record."""

from __future__ import annotations

from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset

from echolot_app.agent.contracts import ToolResult, UiAction, error_result, to_tool_return
from echolot_app.agent.deps import AgentDeps
from echolot_app.db import Workspace, utcnow

MAX_TASK_CHARS = 100_000

task_toolset: FunctionToolset[AgentDeps] = FunctionToolset()


@task_toolset.tool
async def task_read(ctx: RunContext[AgentDeps]) -> ToolReturn:
    """Read the task notes: the agreed description of what should happen to each record (which
    columns go in, which service is used, what comes out, what to do without a result) and every
    decision taken so far. The user can edit them too."""
    async with ctx.deps.session() as db:
        workspace = await db.get(Workspace, ctx.deps.workspace.id)
    text = workspace.task_md if workspace else ""
    if not text.strip():
        return to_tool_return(
            ToolResult(summary="The task notes are empty. Write them with task_update.")
        )
    return to_tool_return(
        ToolResult(
            summary=f"Task notes ({len(text)} characters)",
            data={"markdown": text},
            ui_actions=[UiAction(type="open_task")],
        )
    )


@task_toolset.tool
async def task_update(ctx: RunContext[AgentDeps], markdown: str) -> ToolReturn:
    """Replace the task notes with a new full text (Markdown). Record decisions as they are made,
    in plain words the user can check: the input columns and what they mean, how a request to the
    service is built, what the output columns are, how results are judged, what happens when
    there is no result or an error, and open questions.

    Args:
        markdown: The complete text of the task notes.
    """
    text = markdown.strip()
    if not text:
        return error_result("the task notes are empty")
    if len(text) > MAX_TASK_CHARS:
        return error_result(f"the task notes are longer than {MAX_TASK_CHARS:,} characters")
    async with ctx.deps.session() as db:
        workspace = await db.get(Workspace, ctx.deps.workspace.id)
        if workspace is None:
            return error_result("the workspace no longer exists")
        workspace.task_md = text + "\n"
        workspace.updated_at = utcnow()
        await db.commit()
    return to_tool_return(
        ToolResult(
            summary=f"Task notes updated ({len(text)} characters)",
            data={"characters": len(text)},
            ui_actions=[UiAction(type="open_task")],
        )
    )
