"""ORM models. Milestone 0 tables; later milestones add documents, entries, runs, annotations."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from elexifier_core.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class ProjectRole(StrEnum):
    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


class SourceType(StrEnum):
    XML = "xml"
    PDF = "pdf"


class FileKind(StrEnum):
    SOURCE = "source"
    DERIVED = "derived"
    OUTPUT = "output"
    ARTIFACT = "artifact"


class RunStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DocumentStatus(StrEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    READY = "ready"
    ERROR = "error"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


# --- Identity and access -----------------------------------------------------------------


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    oidc_sub: Mapped[str] = mapped_column(String(255), unique=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(String(16), default=UserRole.USER.value)
    last_login_at: Mapped[datetime | None]

    sessions: Mapped[list[Session]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    credentials: Mapped[list[ProviderCredential]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Session(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(index=True)
    last_seen_at: Mapped[datetime | None]
    user_agent: Mapped[str | None] = mapped_column(String(512))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    id_token: Mapped[str | None] = mapped_column(Text)  # for RP-initiated logout (id_token_hint)

    user: Mapped[User] = relationship(back_populates="sessions")


class ProviderCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "provider_credentials"
    __table_args__ = (UniqueConstraint("user_id", "label"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(100))
    base_url: Mapped[str | None] = mapped_column(String(1024))
    encrypted_key: Mapped[str] = mapped_column(Text)
    declared_capabilities: Mapped[dict[str, Any]] = mapped_column(default=dict)
    last_used_at: Mapped[datetime | None]

    user: Mapped[User] = relationship(back_populates="credentials")


class AuditLog(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "audit_log"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(255), default="")
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)


# --- Projects and files -----------------------------------------------------------------


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "projects"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(8))
    default_model: Mapped[dict[str, Any] | None]
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)

    members: Mapped[list[ProjectMember]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    files: Mapped[list[File]] = relationship(back_populates="project", cascade="all, delete-orphan")


class ProjectMember(CreatedAtMixin, Base):
    __tablename__ = "project_members"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(String(16))

    project: Mapped[Project] = relationship(back_populates="members")
    user: Mapped[User] = relationship()


class File(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "files"
    __table_args__ = (Index("ix_files_project_id_kind", "project_id", "kind"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(512))
    s3_key: Mapped[str] = mapped_column(String(1024), unique=True)
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    mime: Mapped[str] = mapped_column(String(255), default="application/octet-stream")
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    project: Mapped[Project] = relationship(back_populates="files")


# --- Documents and entries ----------------------------------------------------------------


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A parsed source file: profile, chosen entry element and the indexed entries."""

    __tablename__ = "documents"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(8))  # xml | pdf
    status: Mapped[str] = mapped_column(String(16), default=DocumentStatus.QUEUED.value)
    entry_xpath: Mapped[str | None] = mapped_column(String(512))
    headword_xpath: Mapped[str | None] = mapped_column(String(512))
    entry_count: Mapped[int] = mapped_column(Integer, default=0)
    stats: Mapped[dict[str, Any]] = mapped_column(default=dict)  # XmlProfile as JSON
    error: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[int | None] = mapped_column(BigInteger)
    parsed_at: Mapped[datetime | None]

    entries: Mapped[list[Entry]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    pages: Mapped[list[PdfPage]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class PdfPage(UUIDPrimaryKeyMixin, Base):
    """One page of a PDF document: geometry and where its extracted layout JSON lives."""

    __tablename__ = "pdf_pages"
    __table_args__ = (UniqueConstraint("document_id", "page_no"),)

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    page_no: Mapped[int] = mapped_column(Integer)  # 1-based
    width: Mapped[float]
    height: Mapped[float]
    rotation: Mapped[int] = mapped_column(Integer, default=0)
    layout_s3_key: Mapped[str] = mapped_column(String(512))
    text_chars: Mapped[int] = mapped_column(Integer, default=0)
    is_scanned: Mapped[bool] = mapped_column(default=False)
    line_count: Mapped[int] = mapped_column(Integer, default=0)

    document: Mapped[Document] = relationship(back_populates="pages")


class AnnotationStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EDITED = "edited"


class Annotation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A box on a PDF page: a proposed entry span from a segmenter, or a lexicographer's own."""

    __tablename__ = "annotations"
    __table_args__ = (
        Index("ix_annotations_document_id_page_no", "document_id", "page_no"),
        Index("ix_annotations_document_id_status", "document_id", "status"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    page_no: Mapped[int] = mapped_column(Integer)  # 1-based
    bbox: Mapped[list[Any]] = mapped_column(default=list)  # [x0, top, x1, bottom], PLAN 7.10
    label: Mapped[str] = mapped_column(String(64), default="entry")
    entry_span_id: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(8), default="rule")  # rule | ml | user
    status: Mapped[str] = mapped_column(String(16), default=AnnotationStatus.PROPOSED.value)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)  # bboxes, line_ids, text ...
    set_id: Mapped[uuid.UUID | None] = mapped_column(index=True)  # one segmentation run
    position: Mapped[int] = mapped_column(Integer, default=0)  # reading order within the set
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class Entry(UUIDPrimaryKeyMixin, Base):
    """One indexed entry: where it is in the source file and how it is listed."""

    __tablename__ = "entries"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal"),
        Index("ix_entries_document_id_headword", "document_id", "headword"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    headword: Mapped[str | None] = mapped_column(String(300))
    source_ref: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"start": int, "end": int}
    snippet: Mapped[str] = mapped_column(String(400), default="")

    document: Mapped[Document] = relationship(back_populates="entries")


# --- Mappings and conversion specs --------------------------------------------------------


class Mapping(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One version of the project's mapping (the JSON descriptor tree, PLAN 7.8b)."""

    __tablename__ = "mappings"
    __table_args__ = (UniqueConstraint("project_id", "version"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict[str, Any]]
    note: Mapped[str] = mapped_column(Text, default="")
    issues: Mapped[list[Any]] = mapped_column(default=list)  # check_mapping output at save time
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(String(16), default="agent")  # agent | user | import


class ConversionSpec(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Plain-language record of every conversion decision, versioned like the mapping."""

    __tablename__ = "conversion_specs"
    __table_args__ = (UniqueConstraint("project_id", "version"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    markdown: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


# --- Conversion runs ------------------------------------------------------------------------


class RunKind(StrEnum):
    SAMPLE = "sample"
    FULL = "full"


class ConversionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class ConversionRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One application of a mapping version: a synchronous sample or a full run in the worker."""

    __tablename__ = "conversion_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(16))  # RunKind
    mapping_version: Mapped[int] = mapped_column(Integer)
    steps_snapshot: Mapped[list[Any]] = mapped_column(default=list)  # custom steps (M3)
    scope: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"ordinals": [...], "n": 10}
    status: Mapped[str] = mapped_column(String(16), default=ConversionStatus.QUEUED.value)
    output_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("files.id", ondelete="SET NULL")
    )
    stats: Mapped[dict[str, Any]] = mapped_column(default=dict)
    validation_report: Mapped[dict[str, Any]] = mapped_column(default=dict)
    job_id: Mapped[int | None] = mapped_column(BigInteger)
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None]

    converted_entries: Mapped[list[ConvertedEntry]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )


class ConvertedEntry(UUIDPrimaryKeyMixin, Base):
    """The DMLex output of one source entry in one run, with its validation issues."""

    __tablename__ = "converted_entries"
    __table_args__ = (
        Index("ix_converted_entries_run_id_position", "run_id", "position"),
        Index("ix_converted_entries_run_id_entry_id", "run_id", "entry_id"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversion_runs.id", ondelete="CASCADE"))
    entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("entries.id", ondelete="SET NULL")
    )
    position: Mapped[int] = mapped_column(Integer)  # order in the run's output
    ordinal: Mapped[int | None] = mapped_column(Integer)  # source entry ordinal
    headword: Mapped[str | None] = mapped_column(String(300))
    dmlex: Mapped[dict[str, Any]] = mapped_column(default=dict)  # normalized DMLex JSON
    issues: Mapped[list[Any]] = mapped_column(default=list)
    issue_count: Mapped[int] = mapped_column(Integer, default=0)
    # PDF structuring (M2.6): the span this entry came from, the model's confidence and notes,
    # and the lexicographer's corrected version when they edited the output
    annotation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("annotations.id", ondelete="SET NULL")
    )
    confidence: Mapped[float | None]
    notes: Mapped[list[Any]] = mapped_column(default=list)
    override: Mapped[dict[str, Any] | None]
    # sha256 of model, profile and span text: re-runs reuse earlier results for unchanged spans
    cache_key: Mapped[str | None] = mapped_column(String(64), index=True)

    run: Mapped[ConversionRun] = relationship(back_populates="converted_entries")


# --- Conversations and agent runs ---------------------------------------------------------


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "conversations"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255), default="New conversation")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.position"
    )


class Message(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One AI SDK UIMessage as the browser sees it; source of truth for conversation reload."""

    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("conversation_id", "position"),)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer)
    ui_message: Mapped[dict[str, Any]]

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class AgentRun(UUIDPrimaryKeyMixin, Base):
    """One agent turn: model, Pydantic AI message history, usage."""

    __tablename__ = "agent_runs"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    model: Mapped[dict[str, Any]] = mapped_column(default=dict)
    messages: Mapped[list[Any] | None]
    usage: Mapped[dict[str, Any] | None]
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.RUNNING.value)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]


class AgentEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "agent_events"
    __table_args__ = (UniqueConstraint("run_id", "seq"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    event: Mapped[dict[str, Any]]


class Approval(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "approvals"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    tool_call_id: Mapped[str] = mapped_column(String(128), index=True)
    tool_name: Mapped[str] = mapped_column(String(100))
    args: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default=ApprovalStatus.PENDING.value)
    reason: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None]


class UsageRecord(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "usage_records"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(String(32), default="chat")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    usd_estimate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
