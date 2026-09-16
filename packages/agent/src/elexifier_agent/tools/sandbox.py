"""``run_code``: a Python snippet in the sandbox after the lexicographer's approval
(docs/PLAN.md 7.3, 7.12). Inputs are project files mounted read-only; what the snippet writes to
``out/`` becomes artifacts; the snippet, the logs and a ``step.json`` are kept with them so the
run can later become a conversion step."""

from __future__ import annotations

import json
import mimetypes
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_agent.contracts import (
    ArtifactRef,
    ToolResult,
    UiAction,
    error_result,
    to_tool_return,
)
from elexifier_agent.deps import AgentDeps
from elexifier_agent.sandbox import RunnerError
from elexifier_core.db.models import AuditLog, Document, File, FileKind
from elexifier_core.sandbox_contract import RunInput, RunLimits, RunRequest, RunResponse
from elexifier_core.storage import Storage, make_key, safe_filename

MAX_TIMEOUT_S = 600
MAX_CODE_CHARS = 512 * 1024
LOG_PREVIEW_CHARS = 6000
PREVIEW_MAX_BYTES = 5 * 1024 * 1024  # the middle pane previews files up to this size
SANDBOX_LIBRARIES = "lxml, pdfplumber, pypdfium2, jsonschema, xmlschema, saxonche"
_TEXT_EXTENSIONS = {
    ".txt": "text/plain",
    ".log": "text/plain",
    ".py": "text/x-python",
    ".jsonl": "application/x-ndjson",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".md": "text/markdown",
    ".nvh": "text/plain",
}

sandbox_toolset: FunctionToolset[AgentDeps] = FunctionToolset()


class SandboxToolError(Exception):
    pass


@dataclass
class ResolvedInput:
    file: File
    mount_path: str


def guess_mime(name: str) -> str:
    lowered = name.lower()
    for extension, mime in _TEXT_EXTENSIONS.items():
        if lowered.endswith(extension):
            return mime
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def is_previewable(mime: str, size: int) -> bool:
    return size <= PREVIEW_MAX_BYTES and (
        mime.startswith("text/")
        or mime in ("application/json", "application/xml")
        or mime == "application/x-ndjson"
    )


async def resolve_inputs(
    db: AsyncSession, project_id: uuid.UUID, inputs: list[str]
) -> list[ResolvedInput]:
    """File ids, or the words ``source`` and ``latest_export``, mounted as ``in/<file name>``."""
    resolved: list[ResolvedInput] = []
    used: set[str] = set()
    for raw in inputs:
        token = raw.strip()
        record: File | None
        if token == "source":
            document = (
                (
                    await db.execute(
                        select(Document)
                        .where(Document.project_id == project_id)
                        .order_by(Document.created_at.desc())
                    )
                )
                .scalars()
                .first()
            )
            if document is not None:
                record = await db.get(File, document.source_file_id)
            else:
                record = (
                    (
                        await db.execute(
                            select(File)
                            .where(
                                File.project_id == project_id, File.kind == FileKind.SOURCE.value
                            )
                            .order_by(File.created_at.desc())
                        )
                    )
                    .scalars()
                    .first()
                )
            if record is None:
                raise SandboxToolError("the project has no source file yet")
        elif token == "latest_export":
            record = (
                (
                    await db.execute(
                        select(File)
                        .where(File.project_id == project_id, File.kind == FileKind.OUTPUT.value)
                        .order_by(File.created_at.desc())
                    )
                )
                .scalars()
                .first()
            )
            if record is None:
                raise SandboxToolError("there is no export yet; run a full conversion first")
        else:
            try:
                file_id = uuid.UUID(token)
            except ValueError as exc:
                raise SandboxToolError(
                    f"input {raw!r} is neither a file id nor 'source' / 'latest_export'"
                ) from exc
            record = await db.get(File, file_id)
            if record is None or record.project_id != project_id:
                raise SandboxToolError(f"no such file in this project: {raw}")
        name = safe_filename(record.name) or "input"
        candidate = name
        counter = 2
        while candidate in used:
            stem, dot, ext = name.rpartition(".")
            candidate = f"{stem}-{counter}.{ext}" if dot else f"{name}-{counter}"
            counter += 1
        used.add(candidate)
        resolved.append(ResolvedInput(file=record, mount_path=f"in/{candidate}"))
    return resolved


async def store_artifact(
    db: AsyncSession,
    storage: Storage,
    *,
    project_id: uuid.UUID,
    name: str,
    data: bytes,
    mime: str,
    meta: dict[str, Any],
    created_by: uuid.UUID | None,
) -> File:
    """Upload ``data`` as a new artifact file of the project."""
    file_id = uuid.uuid4()
    key = make_key(str(project_id), FileKind.ARTIFACT.value, str(file_id), name)
    stat = await storage.put(key, data, content_type=mime)
    record = File(
        id=file_id,
        project_id=project_id,
        kind=FileKind.ARTIFACT.value,
        name=name,
        s3_key=key,
        size=stat.size,
        sha256=stat.sha256 or "",
        mime=mime,
        meta=meta,
        created_by=created_by,
    )
    db.add(record)
    await db.flush()
    return record


def _stdout_head(text: str, limit: int = 200) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def _size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


@sandbox_toolset.tool(requires_approval=True)
async def run_code(
    ctx: RunContext[AgentDeps],
    code: str,
    purpose: str,
    inputs: list[str] | None = None,
    timeout_s: int = 300,
) -> ToolReturn:
    """Run a Python 3.12 snippet in the sandbox after the lexicographer approves it: no
    network, limited CPU, memory and time, a read-only file system except out/ and /tmp.
    Inputs are mounted read-only under in/; every file the snippet writes to out/ becomes an
    artifact the lexicographer can open. Print a short summary to stdout.

    Args:
        code: The program. Available: the standard library and lxml, pdfplumber, pypdfium2,
            jsonschema, xmlschema, saxonche. Nothing can be installed.
        purpose: One sentence saying what the snippet does and why; the lexicographer reads
            it on the approval card and it goes to the audit log.
        inputs: File ids from list_files, or "source" (the project's source file) or
            "latest_export" (the newest DMLex output). Each is mounted as in/<file name>.
        timeout_s: Seconds before the run is killed (default 300, at most 600).
    """
    deps = ctx.deps
    if deps.runner is None:
        return error_result("the sandbox is not configured on this server (no runner)")
    code = code.strip("\n")
    if not code.strip():
        return error_result("code is empty")
    if len(code) > MAX_CODE_CHARS:
        return error_result(f"code is longer than {MAX_CODE_CHARS // 1024} KB")
    purpose = purpose.strip()
    if not purpose:
        return error_result("purpose is required: say what the snippet does in one sentence")
    timeout = max(1, min(int(timeout_s), MAX_TIMEOUT_S))
    code_run_id = uuid.uuid4()
    short = str(code_run_id)[:8]
    project_id = deps.project.id

    async with deps.session() as db:
        try:
            resolved = await resolve_inputs(db, project_id, list(inputs or []))
        except SandboxToolError as exc:
            return error_result(str(exc))
        # the snippet is kept before the run, so a failed run still leaves it to look at
        code_file = await store_artifact(
            db,
            deps.storage,
            project_id=project_id,
            name=f"code-{short}.py",
            data=code.encode("utf-8"),
            mime="text/x-python",
            meta={"code_run": str(code_run_id), "role": "code", "purpose": purpose},
            created_by=deps.user.id,
        )
        await db.commit()
        request = RunRequest(
            run_id=str(code_run_id),
            project_id=str(project_id),
            code=code,
            inputs=[RunInput(s3_key=r.file.s3_key, mount_path=r.mount_path) for r in resolved],
            limits=RunLimits(timeout_s=timeout),
        )
        input_records = [
            {"file_id": str(r.file.id), "name": r.file.name, "mount_path": r.mount_path}
            for r in resolved
        ]
        code_file_id = str(code_file.id)

    try:
        response = await deps.runner.run(request)
    except RunnerError as exc:
        return error_result(str(exc), code_run_id=str(code_run_id), code_file_id=code_file_id)

    async with deps.session() as db:
        outputs, artifacts, ui_actions = await _register_run(
            db,
            deps,
            code_run_id=code_run_id,
            purpose=purpose,
            code_file_id=code_file_id,
            inputs=input_records,
            response=response,
        )
        await db.commit()

    status = (
        f"was killed at the timeout after {response.duration_s:.1f} s"
        if response.killed
        else f"finished with exit code {response.exit_code} in {response.duration_s:.1f} s"
    )
    listed = ", ".join(f"{o['name']}, {_size(o['size'])}" for o in outputs[:4])
    if len(outputs) > 4:
        listed += f", … ({len(outputs)} files)"
    summary = (
        f"Snippet {status}; {len(outputs)} output file{'s' if len(outputs) != 1 else ''}"
        + (f" ({listed})" if outputs else "")
        + "."
        + (f" stdout: {_stdout_head(response.stdout)}" if response.stdout.strip() else "")
        + (f" stderr: {_stdout_head(response.stderr, 160)}" if response.stderr.strip() else "")
    )
    return to_tool_return(
        ToolResult(
            summary=summary[:500],
            data={
                "code_run_id": str(code_run_id),
                "code_file_id": code_file_id,
                "purpose": purpose,
                "exit_code": response.exit_code,
                "killed": response.killed,
                "duration_s": response.duration_s,
                "stdout": response.stdout[:LOG_PREVIEW_CHARS],
                "stderr": response.stderr[:LOG_PREVIEW_CHARS],
                "logs_truncated": response.truncated
                or len(response.stdout) > LOG_PREVIEW_CHARS
                or len(response.stderr) > LOG_PREVIEW_CHARS,
                "inputs": input_records,
                "outputs": outputs,
                "note": "Outputs are artifacts: open them with their file_id or point the "
                "lexicographer at them. A snippet that transforms entries can become a "
                "conversion step later.",
            },
            artifacts=artifacts,
            ui_actions=ui_actions,
        )
    )


async def _register_run(
    db: AsyncSession,
    deps: AgentDeps,
    *,
    code_run_id: uuid.UUID,
    purpose: str,
    code_file_id: str,
    inputs: list[dict[str, Any]],
    response: RunResponse,
) -> tuple[list[dict[str, Any]], list[ArtifactRef], list[UiAction]]:
    """Files for the outputs (already in storage), the log and ``step.json``; the audit row."""
    project_id = deps.project.id
    run_meta = {"code_run": str(code_run_id), "purpose": purpose, "exit_code": response.exit_code}
    outputs: list[dict[str, Any]] = []
    artifacts: list[ArtifactRef] = [
        ArtifactRef(file_id=code_file_id, name=f"code-{str(code_run_id)[:8]}.py", kind="code")
    ]
    ui_actions: list[UiAction] = []
    for out in response.outputs:
        mime = guess_mime(out.name)
        record = File(
            id=uuid.uuid4(),
            project_id=project_id,
            kind=FileKind.ARTIFACT.value,
            name=out.name,
            s3_key=out.s3_key,
            size=out.size,
            sha256=out.sha256 or "",
            mime=mime,
            meta={**run_meta, "role": "output"},
            created_by=deps.user.id,
        )
        db.add(record)
        await db.flush()
        outputs.append(
            {"file_id": str(record.id), "name": out.name, "size": out.size, "mime": mime}
        )
        artifacts.append(ArtifactRef(file_id=str(record.id), name=out.name, kind="report"))
        if not ui_actions and is_previewable(mime, out.size):
            ui_actions.append(UiAction(type="open_artifact", file_id=str(record.id)))
    short = str(code_run_id)[:8]
    if response.stdout or response.stderr:
        log = await store_artifact(
            db,
            deps.storage,
            project_id=project_id,
            name=f"log-{short}.txt",
            data=(
                f"# exit code {response.exit_code}, {response.duration_s} s"
                + (", killed at the timeout" if response.killed else "")
                + "\n# stdout\n"
                + response.stdout
                + "\n# stderr\n"
                + response.stderr
            ).encode("utf-8"),
            mime="text/plain",
            meta={**run_meta, "role": "log"},
            created_by=deps.user.id,
        )
        artifacts.append(ArtifactRef(file_id=str(log.id), name=log.name, kind="log"))
    step = {
        "code_run_id": str(code_run_id),
        "purpose": purpose,
        "code_file_id": code_file_id,
        "inputs": inputs,
        "outputs": outputs,
        "exit_code": response.exit_code,
        "killed": response.killed,
        "duration_s": response.duration_s,
        "created_at": datetime.now(UTC).isoformat(),
        "created_by": str(deps.user.id),
    }
    await store_artifact(
        db,
        deps.storage,
        project_id=project_id,
        name=f"step-{short}.json",
        data=json.dumps(step, ensure_ascii=False, indent=1).encode("utf-8"),
        mime="application/json",
        meta={**run_meta, "role": "step"},
        created_by=deps.user.id,
    )
    db.add(
        AuditLog(
            user_id=deps.user.id,
            action="sandbox.run",
            target=str(code_run_id),
            meta={
                "project": str(project_id),
                "purpose": purpose,
                "exit_code": response.exit_code,
                "killed": response.killed,
                "duration_s": response.duration_s,
                "inputs": [i["name"] for i in inputs],
                "outputs": len(outputs),
            },
        )
    )
    return outputs, artifacts, ui_actions


__all__ = ["run_code", "sandbox_toolset", "store_artifact"]
