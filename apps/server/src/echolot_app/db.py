"""SQLite through async SQLAlchemy: engine, sessions and the tables of docs/PLAN.md section 8.

The schema is created with ``create_all`` at startup; there are no migrations yet (PLAN
section 3).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from echolot_app.settings import Settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """SQLite has no time zones: store naive UTC, hand back aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; use datetime.now(UTC)")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        dict[str, Any]: JSON,
        list[Any]: JSON,
        datetime: UTCDateTime,
        uuid.UUID: Uuid,
    }


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=False)


def _fk(target: str, *, nullable: bool = False, ondelete: str = "CASCADE") -> Any:
    return mapped_column(ForeignKey(target, ondelete=ondelete), nullable=nullable, index=True)


# --- enums (stored as strings) -----------------------------------------------------------------


class FileKind(StrEnum):
    INPUT = "input"
    RESULT = "result"
    EXPORT = "export"


class Author(StrEnum):
    AGENT = "agent"
    USER = "user"


class RunStatus(StrEnum):
    """App run statuses (docs/PLAN.md 7.5)."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (RunStatus.DONE, RunStatus.FAILED, RunStatus.CANCELLED)


class AgentRunStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


# --- tables ------------------------------------------------------------------------------------


class User(IdMixin, TimestampMixin, Base):
    """A person who can sign in (docs/PLAN.md 7.11). Accounts are made by an admin."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True)  # stored lower-case
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    # the model the agent uses for this user: {"provider", "model_id"}; empty = server default
    model: Mapped[dict[str, Any]] = mapped_column(default=dict)
    last_login_at: Mapped[datetime | None] = mapped_column()


class UserSession(CreatedAtMixin, Base):
    """A signed-in browser. The cookie holds the token; only its SHA-256 is stored."""

    __tablename__ = "user_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = _fk("users.id")
    expires_at: Mapped[datetime] = mapped_column()
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)


class ProviderKey(IdMixin, TimestampMixin, Base):
    """A user's own key for one model vendor, encrypted at rest (bring your own key)."""

    __tablename__ = "provider_keys"
    __table_args__ = (UniqueConstraint("user_id", "provider"),)

    user_id: Mapped[uuid.UUID] = _fk("users.id")
    provider: Mapped[str] = mapped_column(String(32))
    encrypted_key: Mapped[str] = mapped_column(Text, default="")  # "" when the vendor needs none
    key_hint: Mapped[str] = mapped_column(String(16), default="")  # "…abcd", for display
    base_url: Mapped[str | None] = mapped_column(String(500))
    last_used_at: Mapped[datetime | None] = mapped_column()


class Workspace(IdMixin, TimestampMixin, Base):
    __tablename__ = "workspaces"

    # null only for workspaces made before login existed; the first admin adopts them
    owner_id: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    task_md: Mapped[str] = mapped_column(Text, default="")


class File(IdMixin, TimestampMixin, Base):
    __tablename__ = "files"

    workspace_id: Mapped[uuid.UUID] = _fk("workspaces.id")
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(255))
    rel_path: Mapped[str] = mapped_column(String(1024))  # storage key
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64))
    mime: Mapped[str | None] = mapped_column(String(255))
    # format, columns, record_count, dialect, encoding, records_rel_path
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Script(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "scripts"
    __table_args__ = (UniqueConstraint("workspace_id", "version"),)

    workspace_id: Mapped[uuid.UUID] = _fk("workspaces.id")
    version: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(16))
    services: Mapped[list[Any]] = mapped_column(default=list)


class Run(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "runs"

    workspace_id: Mapped[uuid.UUID] = _fk("workspaces.id")
    script_id: Mapped[uuid.UUID] = _fk("scripts.id")
    input_file_id: Mapped[uuid.UUID] = _fk("files.id")
    parent_run_id: Mapped[uuid.UUID | None] = _fk("runs.id", nullable=True, ondelete="SET NULL")
    selection: Mapped[dict[str, Any]] = mapped_column(default=dict)  # e.g. {"rows": [3, 17]}
    record_limit: Mapped[int | None] = mapped_column(Integer)
    concurrency: Mapped[int] = mapped_column(Integer, default=4)
    script_params: Mapped[dict[str, Any]] = mapped_column(default=dict)
    purpose: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.QUEUED.value)
    executor: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str | None] = mapped_column(String(64))
    token_hash: Mapped[str | None] = mapped_column(String(64))
    progress: Mapped[dict[str, Any]] = mapped_column(default=dict)  # done, total, ok, error
    counts: Mapped[dict[str, Any]] = mapped_column(default=dict)  # total, ok, error, duration_s
    result_file_id: Mapped[uuid.UUID | None] = _fk("files.id", nullable=True, ondelete="SET NULL")
    error: Mapped[str | None] = mapped_column(Text)
    submitted_by: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()


class RunDecision(Base):
    __tablename__ = "run_decisions"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    row: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(default=dict)
    decided_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ServiceCall(IdMixin, CreatedAtMixin, Base):
    """One request the agent sent to a service through the runner (service_call, docs/PLAN.md
    7.9): a one-record run of a fixed script, kept apart from the workspace's runs and files."""

    __tablename__ = "service_calls"

    workspace_id: Mapped[uuid.UUID] = _fk("workspaces.id")
    service: Mapped[str] = mapped_column(String(64))
    request: Mapped[dict[str, Any]] = mapped_column(default=dict)  # method, path, params, body
    purpose: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.QUEUED.value)
    executor: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str | None] = mapped_column(String(64))
    token_hash: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[dict[str, Any] | None] = mapped_column()  # the runner's result line
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column()


class Conversation(IdMixin, TimestampMixin, Base):
    __tablename__ = "conversations"

    workspace_id: Mapped[uuid.UUID] = _fk("workspaces.id")
    title: Mapped[str] = mapped_column(String(200), default="New conversation")


class Message(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("conversation_id", "position"),)

    conversation_id: Mapped[uuid.UUID] = _fk("conversations.id")
    position: Mapped[int] = mapped_column(Integer)
    ui_message: Mapped[dict[str, Any]] = mapped_column()


class AgentRun(IdMixin, Base):
    __tablename__ = "agent_runs"

    conversation_id: Mapped[uuid.UUID] = _fk("conversations.id")
    model: Mapped[dict[str, Any]] = mapped_column(default=dict)
    messages: Mapped[list[Any]] = mapped_column(default=list)
    usage: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default=AgentRunStatus.RUNNING.value)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column()


# --- engine and sessions -----------------------------------------------------------------------


def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# Columns added to existing tables after their first release. ``create_all`` makes new tables
# but never alters old ones, and there is no Alembic yet (PLAN section 3): each entry is added
# with ALTER TABLE when a database from before it lacks it. Append only.
_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("workspaces", "owner_id", "CHAR(32) REFERENCES users (id) ON DELETE CASCADE"),
]


def _add_missing_columns(connection: Any) -> None:
    for table, column, ddl in _ADDED_COLUMNS:
        present = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")}
        if column not in present:
            connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            connection.exec_driver_sql(
                f"CREATE INDEX IF NOT EXISTS ix_{table}_{column} ON {table} ({column})"
            )


class Database:
    """The engine and session factory for one process."""

    def __init__(self, settings: Settings) -> None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.engine: AsyncEngine = create_async_engine(settings.database_url)
        event.listen(self.engine.sync_engine, "connect", _sqlite_pragmas)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_all(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.run_sync(_add_missing_columns)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()
