"""Development-only tools, registered when ECHO_ENABLE_TEST_MODEL is on.

They exist so the approval flow can be exercised end to end (browser walk-through, CI smoke test)
without submitting a real run.
"""

from __future__ import annotations

from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset

from echolot_app.agent.contracts import ToolResult, to_tool_return
from echolot_app.agent.deps import AgentDeps

dev_toolset: FunctionToolset[AgentDeps] = FunctionToolset()


@dev_toolset.tool(requires_approval=True)
async def demo_requires_approval(ctx: RunContext[AgentDeps], action: str = "demo") -> ToolReturn:
    """Development helper that always asks the user for approval before it runs.

    Args:
        action: A short description of the pretend action, echoed back when approved.
    """
    return to_tool_return(
        ToolResult(
            summary=f"Approved and performed: {action}",
            data={"action": action, "workspace": ctx.deps.workspace.name},
        )
    )
