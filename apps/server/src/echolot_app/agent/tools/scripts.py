"""The workspace's script: the per-record function the agent writes (docs/PLAN.md 7.1)."""

from __future__ import annotations

from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset

from echolot_app.agent.contracts import ToolResult, UiAction, error_result, to_tool_return
from echolot_app.agent.deps import AgentDeps
from echolot_app.db import Author
from echolot_app.scripts import ScriptError, add_script_version, get_script, latest_script

scripts_toolset: FunctionToolset[AgentDeps] = FunctionToolset()


@scripts_toolset.tool
async def script_read(ctx: RunContext[AgentDeps], version: int | None = None) -> ToolReturn:
    """Read the workspace's script: the latest version, or a given one. The user may have edited
    it in the middle pane since you wrote it.

    Args:
        version: The version number; the latest when omitted.
    """
    async with ctx.deps.session() as db:
        if version is None:
            script = await latest_script(db, ctx.deps.workspace.id)
        else:
            script = await get_script(db, ctx.deps.workspace.id, version)
    if script is None:
        if version is None:
            return to_tool_return(ToolResult(summary="There is no script yet; write one."))
        return error_result(f"there is no version {version} of the script")
    who = "you" if script.author == Author.AGENT else "the user"
    summary = f"Script version {script.version}, written by {who}"
    return to_tool_return(
        ToolResult(
            summary=summary,
            data={
                "version": script.version,
                "author": script.author,
                "note": script.note,
                "services": script.services,
                "code": script.code,
            },
            ui_actions=[UiAction(type="open_script", script_version=script.version)],
        )
    )


@scripts_toolset.tool
async def script_write(ctx: RunContext[AgentDeps], code: str, note: str) -> ToolReturn:
    """Store a new version of the workspace's script and show it to the user. The script must
    define ``def process(record, ctx)`` returning a dict of output fields, may define
    ``def setup(ctx)``, and reaches services only through ``ctx.service("<name>")`` with the
    name written as a literal string. Writing it does not run it.

    Args:
        code: The complete Python source of the script.
        note: One sentence on what changed in this version, shown to the user.
    """
    async with ctx.deps.session() as db:
        try:
            script = await add_script_version(db, ctx.deps.workspace.id, code, note, Author.AGENT)
        except ScriptError as exc:
            return error_result(f"the script was not stored: {exc}")
        await db.commit()
    services = ", ".join(script.services) or "none"
    return to_tool_return(
        ToolResult(
            summary=f"Stored script version {script.version} (services: {services})",
            data={"version": script.version, "services": script.services},
            ui_actions=[UiAction(type="open_script", script_version=script.version)],
        )
    )
