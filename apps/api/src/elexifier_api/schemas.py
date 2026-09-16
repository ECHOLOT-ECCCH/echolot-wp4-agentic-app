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
    page_count: int | None = None  # PDF documents, once the layout is extracted
    error: str | None
    parsed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DocumentDetailOut(DocumentOut):
    stats: dict[str, Any]


class PdfPageOut(ApiModel):
    id: uuid.UUID
    document_id: uuid.UUID
    page_no: int
    width: float
    height: float
    rotation: int
    text_chars: int
    is_scanned: bool
    line_count: int


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


# --- conversion runs ---------------------------------------------------------------------------


class RunOut(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    document_id: uuid.UUID | None
    kind: str
    mapping_version: int
    status: str
    scope: dict[str, Any]
    stats: dict[str, Any]
    output_file_id: uuid.UUID | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class RunDetailOut(RunOut):
    validation_report: dict[str, Any]


class ConvertedEntryOut(ApiModel):
    id: uuid.UUID
    run_id: uuid.UUID
    entry_id: uuid.UUID | None
    annotation_id: uuid.UUID | None = None
    position: int
    ordinal: int | None
    headword: str | None
    issue_count: int
    issues: list[Any]
    confidence: float | None = None
    notes: list[Any] = Field(default_factory=list)


class ConvertedEntryPage(BaseModel):
    items: list[ConvertedEntryOut]
    total: int
    offset: int
    limit: int


class ConvertedEntryDetailOut(ConvertedEntryOut):
    dmlex: dict[str, Any]
    xml: str
    mapping_version: int
    run_created_at: datetime
    override: dict[str, Any] | None = None


class ConvertedEntryOverride(BaseModel):
    override: dict[str, Any] | None  # None removes the correction


class RunCreate(BaseModel):
    kind: Literal["sample", "full"] = "sample"
    document_id: uuid.UUID | None = None
    mapping_version: int | None = None
    # sample options
    n: int = Field(default=10, ge=1, le=20)
    ordinals: list[int] | None = None
    strategy: Literal["first", "spread", "random"] = "spread"
    query: str | None = None
    # full-run options
    number_homographs: bool = True


# --- mappings and conversion spec -----------------------------------------------------------


class MappingIssueOut(BaseModel):
    severity: str
    path: str
    message: str


class MappingSummaryOut(ApiModel):
    id: uuid.UUID
    version: int
    note: str
    source: str
    created_by: uuid.UUID | None
    created_at: datetime
    issue_count: int = 0


class MappingOut(MappingSummaryOut):
    spec: dict[str, Any]
    issues: list[MappingIssueOut]
    summary: str


class MappingIn(BaseModel):
    spec: dict[str, Any]
    note: str = Field(default="", max_length=2000)


class MappingCheckOut(BaseModel):
    ok: bool
    issues: list[MappingIssueOut]
    summary: str | None = None


class SpecOut(ApiModel):
    version: int
    markdown: str
    updated_by: uuid.UUID | None
    created_at: datetime


class SpecIn(BaseModel):
    markdown: str = Field(max_length=200_000)


# --- annotations (PDF entry spans) --------------------------------------------------------------


class AnnotationOut(ApiModel):
    id: uuid.UUID
    document_id: uuid.UUID
    page_no: int
    bbox: list[float]
    label: str
    entry_span_id: str | None
    source: str
    status: str
    payload: dict[str, Any]
    set_id: uuid.UUID | None
    position: int
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class AnnotationPage(BaseModel):
    items: list[AnnotationOut]
    total: int
    offset: int
    limit: int


class AnnotationCreate(BaseModel):
    page_no: int = Field(ge=1)
    bbox: list[float] = Field(min_length=4, max_length=4)
    label: str = Field(default="entry", max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class AnnotationUpdate(BaseModel):
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    label: str | None = Field(default=None, max_length=64)
    status: Literal["proposed", "accepted", "rejected", "edited"] | None = None
    payload: dict[str, Any] | None = None


class AnnotationBulk(BaseModel):
    status: Literal["accepted", "rejected", "proposed"]
    ids: list[uuid.UUID] | None = None  # or everything on a page / in the document
    page_no: int | None = None
    only_status: Literal["proposed", "accepted", "rejected", "edited"] | None = "proposed"


class SegmentRequest(BaseModel):
    segmenter: str = "rule"
    params: dict[str, Any] = Field(default_factory=dict)


class SegmentOut(BaseModel):
    document_id: uuid.UUID
    queued: bool
    job_id: int | None = None
    set_id: uuid.UUID | None = None
    stats: dict[str, Any] | None = None


class AnnotationSplit(BaseModel):
    """Split a span at a vertical position (PDF points from the top of the page): the line under
    that position and everything below it go to the second span."""

    y: float


class AnnotationMerge(BaseModel):
    with_id: uuid.UUID


class StructureRequest(BaseModel):
    kind: Literal["sample", "full"] = "sample"
    structurer: Literal["llm", "rule"] = "llm"
    n: int = Field(default=10, ge=1, le=20)
    span_ids: list[uuid.UUID] | None = None
    strategy: Literal["first", "spread"] = "spread"
    profile: dict[str, Any] | None = None
    with_images: bool | None = None
    model: dict[str, Any] | None = None  # optional model override like the chat request
