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


class ScriptIn(BaseModel):
    code: str = Field(max_length=600_000)
    note: str | None = Field(default=None, max_length=2000)


class ScriptSummary(ApiModel):
    id: uuid.UUID
    version: int
    note: str | None
    author: str
    services: list[str]
    sha256: str
    created_at: datetime


class ScriptOut(ScriptSummary):
    workspace_id: uuid.UUID
    code: str


class RunCreate(BaseModel):
    file_id: uuid.UUID
    script_version: int | None = Field(default=None, ge=1)  # the latest when omitted
    limit: int | None = Field(default=None, ge=1)
    concurrency: int | None = Field(default=None, ge=1, le=32)
    script_params: dict[str, Any] = Field(default_factory=dict)
    purpose: str | None = Field(default=None, max_length=2000)


class RunOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    script_version: int | None
    services: list[str]
    input_file_id: uuid.UUID
    input_file_name: str | None
    parent_run_id: uuid.UUID | None
    record_limit: int | None
    selected_rows: int | None
    concurrency: int
    script_params: dict[str, Any]
    purpose: str | None
    status: str
    executor: str
    progress: dict[str, Any]
    counts: dict[str, Any]
    result_file_id: uuid.UUID | None
    error: str | None
    submitted_by: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunRow(BaseModel):
    row: int
    status: str | None
    error: str | None
    ms: int | None
    input: dict[str, Any]
    output: dict[str, Any]
    decision: dict[str, Any] | None = None


class DecisionIn(BaseModel):
    """A reviewer's choice for one result row: a candidate's QID, or null for "none of these"."""

    qid: str | None = Field(default=None, pattern=r"^Q[1-9][0-9]*$")
    note: str | None = Field(default=None, max_length=2000)


class DecisionOut(BaseModel):
    row: int
    value: dict[str, Any]
    decided_at: datetime


class RunRowsPage(BaseModel):
    total: int
    offset: int
    run_status: str
    input_columns: list[str]
    output_columns: list[str]
    rows: list[RunRow]


# --- accounts (docs/PLAN.md 7.11) --------------------------------------------------------------


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class UserOut(ApiModel):
    id: uuid.UUID
    email: str
    name: str
    is_admin: bool
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    name: str = Field(default="", max_length=200)
    is_admin: bool = False
    # omitted: a temporary password is generated and returned once
    password: str | None = Field(default=None, max_length=256)


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    is_admin: bool | None = None
    is_active: bool | None = None


class UserWithPassword(BaseModel):
    """A new account or a reset: the temporary password is shown once and never stored."""

    user: UserOut
    password: str | None


# --- model vendors and keys (docs/PLAN.md 7.11) ------------------------------------------------


class SuggestedModelOut(BaseModel):
    id: str
    label: str


class VendorOut(BaseModel):
    name: str
    label: str
    needs_key: bool
    needs_base_url: bool
    key_url: str
    suggested: list[SuggestedModelOut]
    # the user's stored key for this vendor, if any
    has_key: bool
    key_hint: str
    base_url: str | None
    key_updated_at: datetime | None
    key_last_used_at: datetime | None


class KeyIn(BaseModel):
    api_key: str | None = Field(default=None, max_length=4000)
    base_url: str | None = Field(default=None, max_length=500)


class ModelChoiceIn(BaseModel):
    """Null provider: the server's model."""

    provider: str | None = Field(default=None, max_length=32)
    model_id: str | None = Field(default=None, max_length=200)


class ServerModelOut(BaseModel):
    provider: str
    model_id: str


class ModelChoiceOut(BaseModel):
    provider: str
    model_id: str
    source: str  # "server" or "own"
    # the server's model, when users may use it
    server_model: ServerModelOut | None
    ready: bool
    problem: str | None


class ProbeIn(BaseModel):
    model_id: str | None = Field(default=None, max_length=200)
