"""What the user has open in the middle pane, described for the model (one line)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from echolot_app.db import File

Kind = Literal["file", "task", "script", "run", "none"]


class Viewing(BaseModel):
    """The browser's preview target, as sent with a chat message (unknown fields ignored)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    kind: Kind = "none"
    file_id: uuid.UUID | None = Field(default=None, alias="fileId")
    run_id: uuid.UUID | None = Field(default=None, alias="runId")
    version: int | None = Field(default=None, ge=1)
    row: int | None = Field(default=None, ge=0)


async def describe_viewing(
    db: AsyncSession, workspace_id: uuid.UUID, viewing: Viewing
) -> tuple[str, dict[str, Any]] | None:
    """A short description and the ids behind it, or None when nothing (or nothing of this
    workspace) is open."""
    data: dict[str, Any] = {"kind": viewing.kind}
    if viewing.kind == "file" and viewing.file_id:
        file = await db.get(File, viewing.file_id)
        if file is None or file.workspace_id != workspace_id:
            return None
        data["file_id"] = str(file.id)
        if viewing.row is not None:
            data["row"] = viewing.row
            return f"row {viewing.row} of the file {file.name} (file_id {file.id})", data
        return f"the file {file.name} (file_id {file.id})", data
    if viewing.kind == "task":
        return "the task notes", data
    if viewing.kind == "script":
        if viewing.version:
            data["version"] = viewing.version
            return f"the script, version {viewing.version}", data
        return "the script (latest version)", data
    return None


__all__ = ["Viewing", "describe_viewing"]
