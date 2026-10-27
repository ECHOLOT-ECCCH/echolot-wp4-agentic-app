"""API request and response models shared by routers."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)


class WorkspaceOut(ApiModel):
    id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class TaskIn(BaseModel):
    markdown: str = Field(max_length=200_000)


class TaskOut(BaseModel):
    markdown: str
    updated_at: datetime


class FileOut(ApiModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    kind: str
    name: str
    size: int
    sha256: str | None
    mime: str | None
    meta: dict[str, Any]
    created_at: datetime


class RecordOut(BaseModel):
    row: int
    record: dict[str, Any]


class RecordsPage(BaseModel):
    total: int
    offset: int
    columns: list[str]
    records: list[RecordOut]


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationOut(ApiModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class MessagesIn(BaseModel):
    messages: list[dict[str, Any]]
