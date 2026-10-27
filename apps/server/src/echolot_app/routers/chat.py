"""Conversations, message persistence and the streaming chat endpoint."""

from __future__ import annotations

import json
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from echolot_app.agent.deps import AgentDeps, WorkspaceContext
from echolot_app.agent.models import model_from_settings, model_label
from echolot_app.agent.runtime import RunSummary
from echolot_app.agent.ui_messages import (
    attach_viewing,
    collect_tool_outputs,
    normalize_ui_messages,
    restore_attachments,
)
from echolot_app.db import AgentRun, AgentRunStatus, Conversation, Message, utcnow
from echolot_app.deps import CurrentSettings, DbSession, State, get_workspace_or_404
from echolot_app.schemas import ConversationCreate, ConversationOut, MessagesIn
from echolot_app.viewing import Viewing, describe_viewing

router = APIRouter(prefix="/api/workspaces/{workspace_id}/conversations", tags=["chat"])
log = structlog.get_logger(__name__)
NEW_TITLE = "New conversation"


async def _conversation(db: AsyncSession, workspace_id: uuid.UUID, cid: uuid.UUID) -> Conversation:
    conversation = await db.get(Conversation, cid)
    if conversation is None or conversation.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversation


@router.get("", response_model=list[ConversationOut])
async def list_conversations(workspace_id: uuid.UUID, db: DbSession) -> list[ConversationOut]:
    await get_workspace_or_404(db, workspace_id)
    rows = (
        await db.execute(
            select(Conversation)
            .where(Conversation.workspace_id == workspace_id)
            .order_by(Conversation.updated_at.desc())
        )
    ).scalars()
    return [ConversationOut.model_validate(c) for c in rows]


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    workspace_id: uuid.UUID, body: ConversationCreate, db: DbSession
) -> ConversationOut:
    await get_workspace_or_404(db, workspace_id)
    conversation = Conversation(
        workspace_id=workspace_id, title=(body.title or "").strip() or NEW_TITLE
    )
    db.add(conversation)
    await db.flush()
    return ConversationOut.model_validate(conversation)


@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    workspace_id: uuid.UUID, conversation_id: uuid.UUID, db: DbSession
) -> ConversationOut:
    return ConversationOut.model_validate(await _conversation(db, workspace_id, conversation_id))


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    workspace_id: uuid.UUID, conversation_id: uuid.UUID, db: DbSession
) -> Response:
    await db.delete(await _conversation(db, workspace_id, conversation_id))
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
    workspace_id: uuid.UUID, conversation_id: uuid.UUID, db: DbSession
) -> list[dict[str, Any]]:
    await _conversation(db, workspace_id, conversation_id)
    return await load_messages(db, conversation_id)


async def _store(db: AsyncSession, conversation_id: uuid.UUID, messages: list[Any]) -> None:
    await db.execute(delete(Message).where(Message.conversation_id == conversation_id))
    await db.flush()
    db.add_all(
        Message(conversation_id=conversation_id, position=i, ui_message=m)
        for i, m in enumerate(messages)
    )
    await db.flush()


async def replace_messages(
    db: AsyncSession, conversation: Conversation, messages: list[dict[str, Any]]
) -> None:
    await _store(db, conversation.id, messages)
    if conversation.title == NEW_TITLE:
        first_text = _first_user_text(messages)
        if first_text:
            conversation.title = first_text[:80]
    conversation.updated_at = utcnow()
    await db.flush()


async def append_messages(
    db: AsyncSession, conversation_id: uuid.UUID, messages: list[dict[str, Any]]
) -> None:
    """Append the server-side messages of a turn and fold tool outputs into the parts the
    browser stored for the same tool calls (approval round trips)."""
    if not messages:
        return
    combined = normalize_ui_messages([*await load_messages(db, conversation_id), *messages])
    await _store(db, conversation_id, combined)


def _first_user_text(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        if message.get("role") == "user":
            for part in message.get("parts", []):
                if part.get("type") == "text" and part.get("text"):
                    return " ".join(str(part["text"]).split())
    return ""


@router.put("/{conversation_id}/messages", status_code=status.HTTP_204_NO_CONTENT)
async def put_messages(
    workspace_id: uuid.UUID, conversation_id: uuid.UUID, body: MessagesIn, db: DbSession
) -> Response:
    """Replace the stored UI messages (client-side persistence fallback after a turn)."""
    conversation = await _conversation(db, workspace_id, conversation_id)
    await replace_messages(db, conversation, body.messages)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{conversation_id}/chat")
async def chat(
    workspace_id: uuid.UUID,
    conversation_id: uuid.UUID,
    request: Request,
    db: DbSession,
    state: State,
    settings: CurrentSettings,
) -> Response:
    """Run one agent turn and stream the AI SDK data-stream protocol back."""
    workspace = await get_workspace_or_404(db, workspace_id)
    conversation = await _conversation(db, workspace_id, conversation_id)
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body must be JSON") from exc
    messages = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body must contain messages")
    try:
        model = model_from_settings(settings)
    except Exception as exc:  # unknown provider, missing key: a configuration problem
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"The model is not configured ({settings.model_provider}): {exc}. "
            "Set ECHO_MODEL_PROVIDER, ECHO_MODEL_ID and ECHO_MODEL_API_KEY.",
        ) from exc

    # tool outputs recorded by earlier turns resolve approval parts the browser still holds
    stored = await load_messages(db, conversation.id)
    messages = normalize_ui_messages(messages, known_outputs=collect_tool_outputs(stored))
    messages = restore_attachments(messages, stored)
    raw_viewing = body.get("viewing")
    if isinstance(raw_viewing, dict) and raw_viewing.get("kind") not in (None, "none"):
        try:
            viewing = Viewing.model_validate(raw_viewing)
        except ValidationError:
            viewing = None  # an unknown target is not worth failing the turn
        described = await describe_viewing(db, workspace.id, viewing) if viewing else None
        if described is not None:
            messages = attach_viewing(messages, *described)
    body = {k: v for k, v in body.items() if k != "viewing"} | {"messages": messages}
    await replace_messages(db, conversation, messages)
    run = AgentRun(conversation_id=conversation.id, model=model_label(settings))
    db.add(run)
    await db.commit()  # the stream outlives this request's session
    run_id = run.id

    deps = AgentDeps(
        workspace=WorkspaceContext(
            id=workspace.id, name=workspace.name, description=workspace.description
        ),
        session_factory=state.db.session_factory,
        storage=state.storage,
        settings=settings,
        conversation_id=conversation.id,
    )

    async def on_complete(summary: RunSummary) -> None:
        async with state.db.session() as session:
            stored_run = await session.get(AgentRun, run_id)
            if stored_run is not None:
                stored_run.messages = _json_list(summary.messages_json)
                stored_run.usage = summary.usage
                stored_run.status = AgentRunStatus.DONE.value
                stored_run.finished_at = utcnow()
            await append_messages(session, conversation.id, summary.new_ui_messages)
            await session.commit()

    async def on_cancel(reason: str) -> None:
        async with state.db.session() as session:
            stored_run = await session.get(AgentRun, run_id)
            if stored_run is not None and stored_run.status == AgentRunStatus.RUNNING.value:
                failed = reason.startswith("error:")
                stored_run.status = (
                    AgentRunStatus.FAILED.value if failed else AgentRunStatus.CANCELLED.value
                )
                stored_run.error = reason[:2000]
                stored_run.finished_at = utcnow()
                await session.commit()

    try:
        response = await state.runtime.dispatch_chat(
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
    # the answer streams; a reverse proxy in front (nginx) must pass it on as it is produced
    response.headers.setdefault("X-Accel-Buffering", "no")
    response.headers.setdefault("Cache-Control", "no-cache")
    return response


def _json_list(data: bytes) -> list[Any]:
    loaded = json.loads(data)
    return loaded if isinstance(loaded, list) else [loaded]
