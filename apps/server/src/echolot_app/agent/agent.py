"""Build the workbench agent."""

from __future__ import annotations

from pathlib import Path

from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.toolsets import AbstractToolset
from sqlalchemy import select

from echolot_app.agent.deps import AgentDeps
from echolot_app.agent.tools.dev import dev_toolset
from echolot_app.agent.tools.files import files_toolset
from echolot_app.agent.tools.scripts import scripts_toolset
from echolot_app.agent.tools.task import task_toolset
from echolot_app.db import File, FileKind, Workspace
from echolot_app.scripts import latest_script

PROMPTS_DIR = Path(__file__).with_name("prompts")

AgentOutput = str | DeferredToolRequests
WorkbenchAgent = Agent[AgentDeps, AgentOutput]

# Provider defaults are small (Anthropic: 4096); a script written in one tool call needs more.
MAX_OUTPUT_TOKENS = 16384
MAX_FILES_LISTED = 20
MAX_TASK_IN_PROMPT = 4000  # longer notes: the model reads them with task_read


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


async def workspace_block(deps: AgentDeps) -> str:
    """What the model should know about the workspace at the start of every turn."""
    lines = ["## This workspace", f"- Name: {deps.workspace.name}"]
    if deps.workspace.description:
        lines.append(f"- Description: {deps.workspace.description}")
    async with deps.session() as db:
        workspace = await db.get(Workspace, deps.workspace.id)
        script = await latest_script(db, deps.workspace.id)
        files = (
            (
                await db.execute(
                    select(File)
                    .where(File.workspace_id == deps.workspace.id, File.kind == FileKind.INPUT)
                    .order_by(File.created_at)
                    .limit(MAX_FILES_LISTED)
                )
            )
            .scalars()
            .all()
        )
    if files:
        lines.append("- Input files:")
        for f in files:
            count = f.meta.get("record_count")
            detail = f"{count:,} records" if isinstance(count, int) else "no records"
            lines.append(f"  - {f.name} ({detail}), file_id {f.id}")
    else:
        lines.append("- No input files yet.")
    if script is None:
        lines.append("- Script: none yet")
    else:
        who = "you" if script.author == "agent" else "the user"
        services = ", ".join(script.services) or "no services"
        lines.append(f"- Script: version {script.version}, written by {who}, uses {services}")
    task = workspace.task_md.strip() if workspace else ""
    if not task:
        lines.append("- Task notes: not written yet")
    elif len(task) <= MAX_TASK_IN_PROMPT:
        lines += ["- Task notes (the user may have edited them):", '"""', task, '"""']
    else:
        lines.append(f"- Task notes: {len(task):,} characters; read them with task_read")
    return "\n".join(lines)


def toolsets_for(*, dev_tools: bool = False) -> list[AbstractToolset[AgentDeps]]:
    toolsets: list[AbstractToolset[AgentDeps]] = [files_toolset, task_toolset, scripts_toolset]
    if dev_tools:
        toolsets.append(dev_toolset)
    return toolsets


def build_agent(model: Model, *, name: str = "echolot", dev_tools: bool = False) -> WorkbenchAgent:
    agent: WorkbenchAgent = Agent(
        model,
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
        toolsets=toolsets_for(dev_tools=dev_tools),
        name=name,
        retries=2,
        model_settings=ModelSettings(max_tokens=MAX_OUTPUT_TOKENS),
    )

    @agent.instructions
    async def _instructions(ctx: RunContext[AgentDeps]) -> str:
        return load_prompt("system.md").format(workspace_block=await workspace_block(ctx.deps))

    return agent
