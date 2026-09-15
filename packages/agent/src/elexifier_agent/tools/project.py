"""Project tools available for every project type."""

from __future__ import annotations

import uuid

from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset
from sqlalchemy import select

from elexifier_agent.contracts import ToolResult, UiAction, error_result, to_tool_return
from elexifier_agent.deps import AgentDeps
from elexifier_core.db.models import File

MAX_HEAD_BYTES = 8192

project_toolset: FunctionToolset[AgentDeps] = FunctionToolset()


@project_toolset.tool
async def list_files(ctx: RunContext[AgentDeps]) -> ToolReturn:
    """List the files in this project: uploaded sources, derived files, outputs and artifacts."""
    async with ctx.deps.session() as db:
        rows = (
            (
                await db.execute(
                    select(File)
                    .where(File.project_id == ctx.deps.project.id)
                    .order_by(File.created_at)
                )
            )
            .scalars()
            .all()
        )
    files = [
        {
            "file_id": str(f.id),
            "name": f.name,
            "kind": f.kind,
            "mime": f.mime,
            "size_bytes": f.size,
            "uploaded_at": f.created_at.isoformat(),
        }
        for f in rows
    ]
    kinds = {}
    for f in files:
        kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    name = ctx.deps.project.name
    if files:
        counts = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items()))
        summary = f"{len(files)} file(s) in project '{name}': {counts}"
    else:
        summary = f"Project '{name}' has no files yet. Ask the lexicographer to upload one."
    return to_tool_return(ToolResult(summary=summary, data={"files": files}))


@project_toolset.tool
async def read_file_head(
    ctx: RunContext[AgentDeps], file_id: str, max_bytes: int = 4096
) -> ToolReturn:
    """Read the first bytes of a text-like file (XML, JSON, NVH, TXT) to see its shape.

    Args:
        file_id: The file id from list_files.
        max_bytes: How many bytes to read (at most 8192). Use the profiling tools for the full
            structure; this is only a peek.
    """
    max_bytes = max(1, min(int(max_bytes), MAX_HEAD_BYTES))
    try:
        fid = uuid.UUID(file_id)
    except ValueError:
        return error_result("file_id is not a valid id", file_id=file_id)
    async with ctx.deps.session() as db:
        record = await db.get(File, fid)
    if record is None or record.project_id != ctx.deps.project.id:
        return error_result("no such file in this project", file_id=file_id)
    if record.mime == "application/pdf" or record.name.lower().endswith(".pdf"):
        return error_result(
            "this is a PDF; use the PDF tools to inspect pages instead of reading raw bytes",
            file_id=file_id,
        )
    end = min(max_bytes, record.size) - 1
    chunks = [c async for c in ctx.deps.storage.open(record.s3_key, (0, end))]
    raw = b"".join(chunks)
    text = raw.decode("utf-8", errors="replace")
    return to_tool_return(
        ToolResult(
            summary=f"First {len(raw)} of {record.size} bytes of {record.name}",
            data={
                "file_id": file_id,
                "name": record.name,
                "size_bytes": record.size,
                "truncated": record.size > len(raw),
                "text": text,
            },
            ui_actions=[UiAction(type="open_file", file_id=file_id)],
        )
    )
