"""Workspace scripts: validation per the script contract (docs/PLAN.md 7.1) and versions."""

from __future__ import annotations

import ast
import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from echolot_app.db import Script

MAX_SCRIPT_BYTES = 512 * 1024


class ScriptError(ValueError):
    """The script breaks the contract; the message says where and how."""


@dataclass(frozen=True)
class ScriptCheck:
    services: list[str]  # literal names passed to ctx.service, in order of first use
    has_setup: bool


def _positional(fn: ast.FunctionDef) -> list[str]:
    return [a.arg for a in [*fn.args.posonlyargs, *fn.args.args]]


def _required(fn: ast.FunctionDef) -> int:
    return len(_positional(fn)) - len(fn.args.defaults)


def check_script(code: str) -> ScriptCheck:
    """Parse ``code`` and check it against the contract; raise ScriptError otherwise."""
    if not code.strip():
        raise ScriptError("the script is empty")
    if len(code.encode("utf-8")) > MAX_SCRIPT_BYTES:
        raise ScriptError(f"the script is larger than {MAX_SCRIPT_BYTES // 1024} KB")
    try:
        tree = ast.parse(code, filename="script.py")
    except SyntaxError as exc:
        where = f"line {exc.lineno}" + (f", column {exc.offset}" if exc.offset else "")
        raise ScriptError(f"syntax error at {where}: {exc.msg}") from exc

    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    asynchronous = {n.name for n in tree.body if isinstance(n, ast.AsyncFunctionDef)}
    if "process" in asynchronous:
        raise ScriptError("process must be a plain function (def), not async def")
    process = functions.get("process")
    if process is None:
        raise ScriptError("the script needs a top-level function: def process(record, ctx)")
    if len(_positional(process)) < 2 or _required(process) > 2:
        raise ScriptError(
            f"process takes (record, ctx); it is defined with ({', '.join(_positional(process))})"
        )
    setup = functions.get("setup")
    if "setup" in asynchronous:
        raise ScriptError("setup must be a plain function (def), not async def")
    if setup is not None and (len(_positional(setup)) < 1 or _required(setup) > 1):
        raise ScriptError(
            f"setup takes (ctx); it is defined with ({', '.join(_positional(setup))})"
        )

    services: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "service"
        ):
            continue
        first = node.args[0] if node.args else None
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            raise ScriptError(
                f"line {node.lineno}: ctx.service needs the service name as a literal string, "
                'e.g. ctx.service("echolot")'
            )
        if first.value not in services:
            services.append(first.value)
    return ScriptCheck(services=services, has_setup=setup is not None)


async def latest_script(db: AsyncSession, workspace_id: uuid.UUID) -> Script | None:
    return (
        await db.execute(
            select(Script)
            .where(Script.workspace_id == workspace_id)
            .order_by(Script.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def get_script(db: AsyncSession, workspace_id: uuid.UUID, version: int) -> Script | None:
    return (
        await db.execute(
            select(Script).where(Script.workspace_id == workspace_id, Script.version == version)
        )
    ).scalar_one_or_none()


async def add_script_version(
    db: AsyncSession, workspace_id: uuid.UUID, code: str, note: str | None, author: str
) -> Script:
    """Check the code and store it as the next version (the caller commits)."""
    checked = check_script(code)
    current = (
        await db.execute(
            select(func.max(Script.version)).where(Script.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()
    script = Script(
        workspace_id=workspace_id,
        version=(current or 0) + 1,
        code=code,
        sha256=hashlib.sha256(code.encode("utf-8")).hexdigest(),
        note=(note or "").strip() or None,
        author=author,
        services=checked.services,
    )
    db.add(script)
    await db.flush()
    return script
