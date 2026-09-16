"""API response and request models shared by routers."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserOut(ApiModel):
    id: uuid.UUID
    email: str
    name: str
    role: str
    created_at: datetime
    last_login_at: datetime | None = None


class HealthOut(BaseModel):
    status: str
    database: str
    events: str


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    source_type: Literal["xml", "pdf"]


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    default_model: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None


class ProjectOut(ApiModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    description: str | None
    source_type: str
    default_model: dict[str, Any] | None
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    my_role: str


class MemberAdd(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: Literal["editor", "viewer"] = "editor"


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    name: str
    role: str


class FileOut(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    kind: str
    name: str
    size: int
    sha256: str
    mime: str
    meta: dict[str, Any]
    created_by: uuid.UUID | None
    created_at: datetime


class CredentialCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    label: str = Field(min_length=1, max_length=100)
    api_key: str | None = Field(default=None, max_length=4096)
    base_url: str | None = Field(default=None, max_length=1024)
    declared_capabilities: dict[str, Any] = Field(default_factory=dict)


class CredentialOut(ApiModel):
    id: uuid.UUID
    provider: str
    label: str
    base_url: str | None
    key_hint: str
    declared_capabilities: dict[str, Any]
    last_used_at: datetime | None
    created_at: datetime


class ProbeRequest(BaseModel):
    model_id: str | None = Field(default=None, max_length=200)


class ProbeOut(BaseModel):
    ok: bool
    model_id: str
    latency_ms: int
    message: str
    input_tokens: int = 0
    output_tokens: int = 0
    reply: str = ""


class ProviderOut(BaseModel):
    name: str
    label: str
    needs_api_key: bool
    needs_base_url: bool
    docs_url: str


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=255)


class ConversationOut(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class MessagesIn(BaseModel):
    messages: list[dict[str, Any]]


class DocumentCreate(BaseModel):
    file_id: uuid.UUID
    entry_xpath: str | None = Field(default=None, max_length=512)
    headword_xpath: str | None = Field(default=None, max_length=512)


class DocumentReparse(BaseModel):
    entry_xpath: str | None = Field(default=None, max_length=512)
    headword_xpath: str | None = Field(default=None, max_length=512)


class DocumentOut(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    source_file_id: uuid.UUID
    file_name: str
    type: str
    status: str
    entry_xpath: str | None
    headword_xpath: str | None
    entry_count: int
    error: str | None
    parsed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DocumentDetailOut(DocumentOut):
    stats: dict[str, Any]


class EntryOut(ApiModel):
    id: uuid.UUID
    document_id: uuid.UUID
    ordinal: int
    headword: str | None
    snippet: str


class EntryPage(BaseModel):
    items: list[EntryOut]
    total: int
    offset: int
    limit: int


class EntryDetailOut(EntryOut):
    source_xml: str
    source_ref: dict[str, Any]
