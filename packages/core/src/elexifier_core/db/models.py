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
