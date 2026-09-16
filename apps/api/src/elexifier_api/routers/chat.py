"""Conversations, message persistence and the streaming chat endpoint (docs/PLAN.md 7.2)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_agent.catalog import curated_info
from elexifier_agent.deps import AgentDeps, ProjectContext, UserContext
from elexifier_agent.runtime import RunSummary
from elexifier_agent.ui_messages import (
    ContextChip,
    attach_context,
    collect_tool_outputs,
    normalize_ui_messages,
)
from elexifier_api import audit
from elexifier_api.authz import get_project_or_404
from elexifier_api.deps import CurrentSecretBox, CurrentSettings, CurrentUser, DbSession, State
from elexifier_api.models_resolver import resolve_model
from elexifier_api.schemas import ConversationCreate, ConversationOut, MessagesIn
from elexifier_api.state import AppState
from elexifier_core.db.models import (
    AgentRun,
    Conversation,
    Message,
    Project,
    ProjectRole,
    RunStatus,
    UsageRecord,
)

router = APIRouter(prefix="/api/projects/{project_id}/conversations", tags=["chat"])
log = structlog.get_logger(__name__)


async def _conversation(db: AsyncSession, project_id: uuid.UUID, cid: uuid.UUID) -> Conversation:
    conversation = await db.get(Conversation, cid)
    if conversation is None or conversation.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversation


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    project_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> list[ConversationOut]:
    await get_project_or_404(db, project_id, user)
    rows = (
        await db.execute(
            select(Conversation)
            .where(Conversation.project_id == project_id)
            .order_by(Conversation.updated_at.desc())
        )
    ).scalars()
    return [ConversationOut.model_validate(c) for c in rows]


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    project_id: uuid.UUID, body: ConversationCreate, db: DbSession, user: CurrentUser
) -> ConversationOut:
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    conversation = Conversation(
        project_id=project_id,
        title=(body.title or "").strip() or "New conversation",
        created_by=user.id,
    )
    db.add(conversation)
    await db.flush()
    await db.refresh(conversation)
    return ConversationOut.model_validate(conversation)


@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    project_id: uuid.UUID, conversation_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> ConversationOut:
    await get_project_or_404(db, project_id, user)
    return ConversationOut.model_validate(await _conversation(db, project_id, conversation_id))


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    project_id: uuid.UUID, conversation_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> Response:
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    conversation = await _conversation(db, project_id, conversation_id)
    await db.delete(conversation)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def load_messages(db: AsyncSession, conversation_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(Message.ui_message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.position)
        )
    ).scalars()
    return list(rows)


@router.get("/{conversation_id}/messages")
async def get_messages(
    project_id: uuid.UUID, conversation_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> list[dict[str, Any]]:
    await get_project_or_404(db, project_id, user)
    await _conversation(db, project_id, conversation_id)
    return await load_messages(db, conversation_id)


async def replace_messages(
    db: AsyncSession, conversation: Conversation, messages: list[dict[str, Any]]
) -> None:
    await db.execute(delete(Message).where(Message.conversation_id == conversation.id))
    db.add_all(
        Message(conversation_id=conversation.id, position=i, ui_message=m)
        for i, m in enumerate(messages)
    )
    if conversation.title == "New conversation":
        first_text = _first_user_text(messages)
        if first_text:
            conversation.title = first_text[:80]
    conversation.updated_at = datetime.now(UTC)
    await db.flush()


async def append_messages(
    db: AsyncSession, conversation_id: uuid.UUID, messages: list[dict[str, Any]]
) -> None:
    """Append the server-side messages of a turn and fold tool outputs into the parts the
    browser stored for the same tool calls (approval round trips)."""
    if not messages:
        return
    combined = normalize_ui_messages([*await load_messages(db, conversation_id), *messages])
    await db.execute(delete(Message).where(Message.conversation_id == conversation_id))
    db.add_all(
        Message(conversation_id=conversation_id, position=i, ui_message=m)
        for i, m in enumerate(combined)
    )
    await db.flush()


def _first_user_text(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        if message.get("role") == "user":
            for part in message.get("parts", []):
                if part.get("type") == "text" and part.get("text"):
                    return " ".join(str(part["text"]).split())
    return ""


@router.put("/{conversation_id}/messages", status_code=status.HTTP_204_NO_CONTENT)
async def put_messages(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    body: MessagesIn,
    db: DbSession,
    user: CurrentUser,
) -> Response:
    """Replace the stored UI messages (client-side persistence fallback after a turn)."""
    await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    conversation = await _conversation(db, project_id, conversation_id)
    await replace_messages(db, conversation, body.messages)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{conversation_id}/chat")
async def chat(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    state: State,
    box: CurrentSecretBox,
    settings: CurrentSettings,
) -> Response:
    """Run one agent turn and stream the AI SDK data-stream protocol back."""
    project, role = await get_project_or_404(db, project_id, user, ProjectRole.EDITOR.value)
    conversation = await _conversation(db, project_id, conversation_id)
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body must be JSON") from exc
    messages = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body must contain messages")

    spec, model = await resolve_model(
        db, user=user, project=project, requested=body.get("model"), box=box, settings=settings
    )
    # tool outputs recorded by earlier turns resolve approval parts the browser still holds
    stored = await load_messages(db, conversation.id)
    messages = normalize_ui_messages(messages, known_outputs=collect_tool_outputs(stored))
    raw_context = body.get("context")
    if isinstance(raw_context, list) and raw_context:
        try:
            chips = [ContextChip.model_validate(c) for c in raw_context[:6]]
        except ValidationError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Malformed context") from exc
        messages = attach_context(messages, chips)
    body = {k: v for k, v in body.items() if k != "context"} | {"messages": messages}
    await replace_messages(db, conversation, messages)
    run = AgentRun(
        conversation_id=conversation.id,
        user_id=user.id,
        model=spec.model_dump(),
        status=RunStatus.RUNNING.value,
    )
    db.add(run)
    audit.record(
        db, user.id, "chat.turn", conversation.id, project=str(project.id), model=spec.model_id
    )
    await db.commit()  # the stream outlives this request's session
    run_id = run.id

    deps = AgentDeps(
        project=_project_context(project),
        user=UserContext(id=user.id, email=user.email, name=user.name, role=role),
        session_factory=state.db.session_factory,
        storage=state.storage,
        settings=settings,
        model_info=state.catalog.cached_info(spec.provider, spec.model_id)
        or curated_info(spec.provider, spec.model_id),
        conversation_id=conversation.id,
        defer_job=_deferrer(state),
        model=model,
        model_spec=spec.model_dump(),
    )

    async def on_complete(summary: RunSummary) -> None:
        async with state.db.session() as session:
            stored = await session.get(AgentRun, run_id)
            if stored is not None:
                stored.messages = _json_bytes(summary.messages_json)
                stored.usage = summary.usage
                stored.status = RunStatus.DONE.value
                stored.finished_at = datetime.now(UTC)
            session.add(
                UsageRecord(
                    user_id=user.id,
                    project_id=project.id,
                    run_id=run_id,
                    provider=spec.provider,
                    model=spec.model_id,
                    purpose="chat",
                    input_tokens=int(summary.usage.get("input_tokens") or 0),
                    output_tokens=int(summary.usage.get("output_tokens") or 0),
                )
            )
            await append_messages(session, conversation.id, summary.new_ui_messages)
            await session.commit()

    async def on_cancel(reason: str) -> None:
        async with state.db.session() as session:
            stored = await session.get(AgentRun, run_id)
            if stored is not None and stored.status == RunStatus.RUNNING.value:
                failed = reason.startswith("error:")
                stored.status = RunStatus.FAILED.value if failed else RunStatus.CANCELLED.value
                stored.error = reason[:2000]
                stored.finished_at = datetime.now(UTC)
                await session.commit()

    try:
        return await state.runtime.dispatch_chat(
            request,
            deps=deps,
            model=model,
            on_complete=on_complete,
            on_cancel=on_cancel,
            body=body,
        )
    except ValidationError as exc:
        await on_cancel("error: malformed chat request")
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Malformed chat request (messages)"
        ) from exc


def _deferrer(state: AppState) -> Callable[[str, dict[str, Any]], Awaitable[int | None]]:
    async def defer(name: str, kwargs: dict[str, Any]) -> int | None:
        job_id = await state.jobs.configure_task(name, queue="heavy").defer_async(**kwargs)
        return int(job_id) if job_id is not None else None

    return defer


def _project_context(project: Project) -> ProjectContext:
    return ProjectContext(
        id=project.id,
        name=project.name,
        source_type=project.source_type,
        description=project.description,
        settings=project.settings,
    )


def _json_bytes(data: bytes) -> list[Any]:
    import json

    loaded = json.loads(data)
    return loaded if isinstance(loaded, list) else [loaded]
