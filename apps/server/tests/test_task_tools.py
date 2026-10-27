from __future__ import annotations

import httpx

from echolot_app.agent.agent import workspace_block

from .conftest import Workspace, call_tool


async def test_task_update_and_read(client: httpx.AsyncClient, workspace: Workspace) -> None:
    empty = await call_tool(workspace.deps, "task_read", {})
    assert empty["summary"].startswith("The task notes are empty")

    written = await call_tool(
        workspace.deps, "task_update", {"markdown": "# Task\nLink each {person} to Wikidata.\n"}
    )
    assert written["summary"] == "Task notes updated (38 characters)"
    stored = (await client.get(f"/api/workspaces/{workspace.id}/task")).json()
    assert stored["markdown"] == "# Task\nLink each {person} to Wikidata.\n"

    read = await call_tool(workspace.deps, "task_read", {})
    assert read["data"]["markdown"].startswith("# Task")
    rejected = await call_tool(workspace.deps, "task_update", {"markdown": "   "})
    assert rejected["summary"] == "Error: the task notes are empty"


async def test_a_user_edit_reaches_the_next_turn(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    await client.put(
        f"/api/workspaces/{workspace.id}/task", json={"markdown": "Skip records without a name."}
    )
    block = await workspace_block(workspace.deps)
    assert '- Task notes (the user may have edited them):\n"""\nSkip records without a name.' in (
        block
    )
    await client.put(f"/api/workspaces/{workspace.id}/task", json={"markdown": "x" * 5000})
    assert "5,000 characters; read them with task_read" in await workspace_block(workspace.deps)
