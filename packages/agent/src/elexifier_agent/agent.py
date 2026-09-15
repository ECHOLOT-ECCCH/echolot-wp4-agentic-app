"""Build the conversion agent for a project."""

from __future__ import annotations

from pathlib import Path

from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.models import Model
from pydantic_ai.toolsets import AbstractToolset

from elexifier_agent.deps import AgentDeps
from elexifier_agent.tools.project import project_toolset

PROMPTS_DIR = Path(__file__).with_name("prompts")

AgentOutput = str | DeferredToolRequests
ConversionAgent = Agent[AgentDeps, AgentOutput]


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def render_system_prompt(deps: AgentDeps) -> str:
    project = deps.project
    source = "legacy XML export" if project.source_type == "xml" else "PDF dictionary"
    lines = ["## This project", f"- Name: {project.name}", f"- Source type: {source}"]
    if project.description:
        lines.append(f"- Description: {project.description}")
    lines.append(
        f"- Lexicographer: {deps.user.name or deps.user.email} (project role: {deps.user.role})"
    )
    if deps.model_info is not None:
        caps = []
        if deps.model_info.supports_vision is False:
            caps.append("this model cannot see images, so page images cannot be shown to it")
        if caps:
            lines.append("- Model notes: " + "; ".join(caps))
    return load_prompt("system.md").format(
        project_block="\n".join(lines), dmlex_cheatsheet=load_prompt("dmlex_cheatsheet.md")
    )


def toolsets_for(source_type: str) -> list[AbstractToolset[AgentDeps]]:
    """Toolsets are filtered by project type to keep the model's context small."""
    toolsets: list[AbstractToolset[AgentDeps]] = [project_toolset]
    # M1 adds the XML and DMLex toolsets, M2 the PDF toolset, M3 the sandbox toolset.
    return toolsets


def build_agent(source_type: str, model: Model, *, name: str = "elexifier") -> ConversionAgent:
    agent: ConversionAgent = Agent(
        model,
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
        toolsets=toolsets_for(source_type),
        name=name,
        retries=2,
    )

    @agent.instructions
    def _instructions(ctx: RunContext[AgentDeps]) -> str:
        return render_system_prompt(ctx.deps)

    return agent
