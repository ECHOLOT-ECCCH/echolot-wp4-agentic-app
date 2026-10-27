"""The services a script can call through ``ctx.service(name)`` and their documentation."""

from __future__ import annotations

from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset

from echolot_app.agent.contracts import ToolResult, error_result, to_tool_return
from echolot_app.agent.deps import AgentDeps
from echolot_app.service_docs import load_service_docs

services_toolset: FunctionToolset[AgentDeps] = FunctionToolset()


@services_toolset.tool
async def list_services(ctx: RunContext[AgentDeps]) -> ToolReturn:
    """List the services a script can call through ctx.service("<name>"), with one line each.
    Read a service's documentation with service_docs before writing a script against it."""
    docs = load_service_docs(ctx.deps.settings.services_dir)
    items = [{"name": d.name, "description": d.description} for d in docs.values()]
    summary = f"{len(items)} service(s): {', '.join(docs) or 'none'}"
    return to_tool_return(ToolResult(summary=summary, data={"services": items}))


@services_toolset.tool
async def service_docs(ctx: RunContext[AgentDeps], name: str) -> ToolReturn:
    """Read a service's documentation: endpoints, request and response shapes, errors, quirks,
    and how to build good requests.

    Args:
        name: The service name from list_services.
    """
    docs = load_service_docs(ctx.deps.settings.services_dir)
    doc = docs.get(name.strip())
    if doc is None:
        return error_result(f"no service {name!r}; known: {', '.join(docs) or 'none'}")
    return to_tool_return(
        ToolResult(
            summary=f"Documentation of {doc.name} ({len(doc.body)} characters)",
            data={"name": doc.name, "description": doc.description, "documentation": doc.body},
        )
    )
