"""The services a script can call through ``ctx.service(name)``, their documentation, and single
requests to them (service_call)."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset

from echolot_app.agent.contracts import ToolResult, error_result, to_tool_return
from echolot_app.agent.deps import AgentDeps
from echolot_app.db import RunStatus
from echolot_app.runs.calls import start_call, wait_for_call
from echolot_app.service_docs import load_service_docs

services_toolset: FunctionToolset[AgentDeps] = FunctionToolset()

MAX_CALL_BODY_CHARS = 100_000
MAX_CALL_PATH_CHARS = 500


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


@services_toolset.tool(requires_approval=True)
async def service_call(
    ctx: RunContext[AgentDeps],
    service: str,
    path: str,
    purpose: str,
    body: dict[str, Any] | list[Any] | None = None,
    params: dict[str, str | int | float | bool] | None = None,
    method: Literal["POST", "GET"] = "POST",
) -> ToolReturn:
    """Send one request to a service and get its reply here, without a script or a run: to look
    at a single item (e.g. what Wikidata holds for one QID), to see how a service answers one
    record, or to check a request before writing the script. The user approves every call on a
    card that shows the request; say in `purpose` why. It goes through the runner like a run, so
    it takes a few seconds, and replies over about 6,000 tokens are cut. For more than a handful
    of records, write a script and run it.

    Args:
        service: The service name from list_services, e.g. "echolot".
        path: The endpoint path from the service's documentation, e.g. "/wikidata/statements".
        purpose: One sentence for the approval card, e.g. "See what Wikidata holds for Q156736".
        body: The JSON body of a POST, as the service's documentation describes it.
        params: The query string, e.g. {"algorithm": "rule_based"}.
        method: "POST" (the default) or "GET".
    """
    deps = ctx.deps
    docs = load_service_docs(deps.settings.services_dir)
    name = service.strip()
    if name not in docs:
        return error_result(f"no service {service!r}; known: {', '.join(docs) or 'none'}")
    path = path.strip()
    if not path.startswith("/") or "://" in path or len(path) > MAX_CALL_PATH_CHARS:
        return error_result(f"the path must be an endpoint such as /reconcile, not {path!r}")
    if method == "GET" and body is not None:
        return error_result("a GET request has no body; send it as POST or put values in params")
    if len(json.dumps(body, ensure_ascii=False)) > MAX_CALL_BODY_CHARS:
        return error_result(f"the body is larger than {MAX_CALL_BODY_CHARS:,} characters")
    request = {
        "method": method,
        "path": path,
        "params": {k: str(v) for k, v in (params or {}).items()},
        "body": body,
    }
    async with deps.session() as db:
        call = await start_call(
            db,
            deps.settings,
            deps.executor,
            deps.workspace.id,
            name,
            request,
            purpose.strip() or None,
        )
        call_id = call.id
    outcome = await wait_for_call(deps.session, deps.executor, call_id)
    label = f"{name} {method} {path}"
    if outcome.status is RunStatus.DONE:
        return to_tool_return(
            ToolResult(
                summary=f"{label} answered",
                data={"service": name, "method": method, "path": path, "reply": outcome.reply},
            )
        )
    return error_result(f"{label} failed: {outcome.error}", service=name, path=path)
