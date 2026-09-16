"""Build the conversion agent for a project."""

from __future__ import annotations

from pathlib import Path

from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset

from elexifier_agent.deps import AgentDeps
from elexifier_agent.tools.conversion import conversion_toolset
from elexifier_agent.tools.dev import dev_toolset
from elexifier_agent.tools.pdf import pdf_toolset
from elexifier_agent.tools.project import project_toolset
from elexifier_agent.tools.sandbox import sandbox_toolset
from elexifier_agent.tools.xml import xml_toolset

PROMPTS_DIR = Path(__file__).with_name("prompts")

AgentOutput = str | DeferredToolRequests
ConversionAgent = Agent[AgentDeps, AgentOutput]

# Provider defaults are small (Anthropic: 4096). A mapping proposal is one tool call carrying the
# whole mapping JSON, which does not fit. Every current model accepts this output cap.
MAX_OUTPUT_TOKENS = 16384


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
    prompt = load_prompt("system.md").format(
        project_block="\n".join(lines), dmlex_cheatsheet=load_prompt("dmlex_cheatsheet.md")
    )
    if project.source_type == "xml":
        prompt += "\n\n" + load_prompt("xml_workflow.md")
    elif project.source_type == "pdf":
        prompt += "\n\n" + load_prompt("pdf_workflow.md")
    prompt += "\n\n" + load_prompt("sandbox_workflow.md")
    return prompt


RUN_TOOLS = frozenset({"run_stats", "export", "validate_dmlex"})


def _not_for_canned_model(ctx: RunContext[AgentDeps], tool: ToolDefinition) -> bool:
    """The dev/CI test model calls every tool it sees with schema-generated arguments; sending
    such a snippet to the sandbox on every turn is noise, so it never sees run_code."""
    return not isinstance(ctx.model, TestModel)


def _is_run_tool(ctx: RunContext[AgentDeps], tool: ToolDefinition) -> bool:
    return tool.name in RUN_TOOLS


def toolsets_for(source_type: str, *, dev_tools: bool = False) -> list[AbstractToolset[AgentDeps]]:
    """Toolsets are filtered by project type to keep the model's context small."""
    toolsets: list[AbstractToolset[AgentDeps]] = [project_toolset]
    if source_type == "xml":
        toolsets.extend([xml_toolset, conversion_toolset])
    elif source_type == "pdf":
        # structuring runs are checked and exported with the run tools; the mapping-driven
        # ones (run_conversion, diff_runs) stay with XML projects
        toolsets.extend([pdf_toolset, conversion_toolset.filtered(_is_run_tool)])
    toolsets.append(sandbox_toolset.filtered(_not_for_canned_model))  # run_code (M3)
    if dev_tools:
        toolsets.append(dev_toolset)
    return toolsets


def build_agent(
    source_type: str, model: Model, *, name: str = "elexifier", dev_tools: bool = False
) -> ConversionAgent:
    agent: ConversionAgent = Agent(
        model,
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
        toolsets=toolsets_for(source_type, dev_tools=dev_tools),
        name=name,
        retries=2,
        model_settings=ModelSettings(max_tokens=MAX_OUTPUT_TOKENS),
    )

    @agent.instructions
    def _instructions(ctx: RunContext[AgentDeps]) -> str:
        return render_system_prompt(ctx.deps)

    return agent
