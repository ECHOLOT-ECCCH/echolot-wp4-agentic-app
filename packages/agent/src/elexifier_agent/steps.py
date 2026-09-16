"""Custom conversion steps at run time (docs/PLAN.md M3.5): the enabled steps of a project,
their snapshot on a run, and their execution through the sandbox runner on the entry stream
(``in/entries.jsonl`` in, ``out/entries.jsonl`` out), one step after the other."""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from elexifier_agent.sandbox import RunnerClient, RunnerError
from elexifier_core.db.models import File, PipelineStep
from elexifier_core.sandbox_contract import RunInput, RunLimits, RunRequest
from elexifier_core.storage import Storage, make_key

ENTRIES_MOUNT = "in/entries.jsonl"
ENTRIES_OUTPUT = "entries.jsonl"
STEP_TIMEOUT_S = 600
LOG_TAIL_CHARS = 2000


@dataclass
class StepSpec:
    id: uuid.UUID
    name: str
    position: int
    code_artifact_id: uuid.UUID
    code_key: str  # storage key of the snippet
    code_sha256: str
    inputs: list[str] = field(default_factory=list)  # mount paths the snippet was tried with
    purpose: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "step_id": str(self.id),
            "name": self.name,
            "position": self.position,
            "code_artifact_id": str(self.code_artifact_id),
            "code_sha256": self.code_sha256,
        }


@dataclass
class StepRecord:
    """What a step did inside one run (stored on ``steps_snapshot``)."""

    step_id: str
    name: str
    position: int
    exit_code: int
    duration_s: float
    entries_in: int
    entries_out: int
    stdout: str = ""
    stderr: str = ""


class StepFailed(Exception):
    """A step failed; the run stops with this message."""


async def enabled_steps(
    db: AsyncSession, project_id: uuid.UUID, *, ids: Sequence[uuid.UUID] | None = None
) -> list[StepSpec]:
    """The project's enabled steps in order, with their snippet's storage key. With ``ids``
    (a run's snapshot), those steps in that order whether or not they are still enabled;
    a deleted one raises StepFailed."""
    statement = (
        select(PipelineStep, File)
        .join(File, File.id == PipelineStep.code_artifact_id)
        .where(PipelineStep.project_id == project_id)
        .order_by(PipelineStep.position, PipelineStep.created_at)
    )
    if ids is None:
        statement = statement.where(PipelineStep.enabled.is_(True))
    else:
        statement = statement.where(PipelineStep.id.in_(list(ids)))
    rows = (await db.execute(statement)).all()
    specs = {
        step.id: StepSpec(
            id=step.id,
            name=step.name,
            position=step.position,
            code_artifact_id=code.id,
            code_key=code.s3_key,
            code_sha256=code.sha256,
            inputs=[str(i) for i in (step.config.get("inputs") or [])],
            purpose=step.config.get("purpose"),
        )
        for step, code in rows
    }
    if ids is None:
        return list(specs.values())
    ordered: list[StepSpec] = []
    for step_id in ids:
        spec = specs.get(step_id)
        if spec is None:
            raise StepFailed(f"step {step_id} was deleted after the run was queued")
        ordered.append(spec)
    return ordered


def snapshot_of(steps: Sequence[StepSpec]) -> list[dict[str, Any]]:
    return [s.snapshot() for s in steps]


def _tail(text: str) -> str:
    return text if len(text) <= LOG_TAIL_CHARS else "…" + text[-LOG_TAIL_CHARS:]


def _count_lines(path: Path) -> int:
    with path.open("r", encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


class StepRunner:
    """Runs a project's steps on an entries file through the sandbox runner."""

    def __init__(
        self,
        runner: RunnerClient,
        storage: Storage,
        *,
        project_id: uuid.UUID,
        run_id: str,
        source_key: str | None = None,
        timeout_s: int = STEP_TIMEOUT_S,
    ) -> None:
        self.runner = runner
        self.storage = storage
        self.project_id = project_id
        self.run_id = run_id
        self.source_key = source_key
        self.timeout_s = timeout_s
        self.records: list[StepRecord] = []

    async def apply(
        self, steps: Sequence[StepSpec], plain_jsonl: Path, workdir: Path
    ) -> tuple[Path, list[StepRecord]]:
        current = plain_jsonl
        for index, step in enumerate(steps):
            current = await self._apply_one(index, step, current, workdir)
        return current, list(self.records)

    async def _apply_one(self, index: int, step: StepSpec, current: Path, workdir: Path) -> Path:
        entries_in = _count_lines(current)
        code = b"".join([c async for c in self.storage.open(step.code_key)]).decode("utf-8")
        in_key = make_key(
            str(self.project_id), "derived", f"steps-{self.run_id}", f"step{index}-in.jsonl"
        )
        with current.open("rb") as fh:
            await self.storage.put(in_key, fh.read(), content_type="application/x-ndjson")
        inputs = [RunInput(s3_key=in_key, mount_path=ENTRIES_MOUNT)]
        if self.source_key:
            # the snippet may also read the source it was tried with, at the same mount path
            for mount in step.inputs:
                if mount != ENTRIES_MOUNT and mount.startswith("in/"):
                    inputs.append(RunInput(s3_key=self.source_key, mount_path=mount))
                    break
        request = RunRequest(
            run_id=f"{self.run_id}-step{index}",
            project_id=str(self.project_id),
            code=code,
            inputs=inputs,
            limits=RunLimits(timeout_s=self.timeout_s),
        )
        try:
            response = await self.runner.run(request)
        except RunnerError as exc:
            raise StepFailed(f"step {index + 1} {step.name!r} could not run: {exc}") from exc
        finally:
            await self.storage.delete(in_key)
        record = StepRecord(
            step_id=str(step.id),
            name=step.name,
            position=step.position,
            exit_code=response.exit_code,
            duration_s=response.duration_s,
            entries_in=entries_in,
            entries_out=0,
            stdout=_tail(response.stdout),
            stderr=_tail(response.stderr),
        )
        self.records.append(record)
        output = next((o for o in response.outputs if o.name == ENTRIES_OUTPUT), None)
        for out in response.outputs:  # the run keeps only the entries; nothing else is needed
            if out is not output:
                await self.storage.delete(out.s3_key)
        if response.killed or response.exit_code != 0:
            if output is not None:
                await self.storage.delete(output.s3_key)
            reason = (
                f"was killed after {self.timeout_s} s"
                if response.killed
                else f"failed with exit code {response.exit_code}"
            )
            detail = _tail(response.stderr).strip() or _tail(response.stdout).strip()
            raise StepFailed(
                f"step {index + 1} {step.name!r} {reason}" + (f": {detail}" if detail else "")
            )
        if output is None:
            raise StepFailed(f"step {index + 1} {step.name!r} wrote no out/{ENTRIES_OUTPUT}")
        target = workdir / f"step{index}.jsonl"
        with target.open("wb") as fh:
            async for chunk in self.storage.open(output.s3_key):
                fh.write(chunk)
        await self.storage.delete(output.s3_key)
        record.entries_out = _count_lines(target)
        return target


async def apply_steps_to_entries(
    runner: RunnerClient,
    storage: Storage,
    steps: Sequence[StepSpec],
    entries: Sequence[dict[str, Any]],
    *,
    project_id: uuid.UUID,
    run_id: str,
    workdir: Path,
    source_key: str | None = None,
) -> tuple[list[dict[str, Any]], list[StepRecord]]:
    """The steps applied to a list of entries (samples): the transformed entries back."""
    plain = workdir / "entries.plain.jsonl"
    with plain.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, ensure_ascii=False))
            fh.write("\n")
    step_runner = StepRunner(
        runner, storage, project_id=project_id, run_id=run_id, source_key=source_key
    )
    result, records = await step_runner.apply(steps, plain, workdir)
    out: list[dict[str, Any]] = []
    with result.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                entry = json.loads(text)
            except ValueError as exc:
                raise StepFailed(f"line {line_no} of the step output is not JSON: {exc}") from exc
            if not isinstance(entry, dict):
                raise StepFailed(f"line {line_no} of the step output is not an entry object")
            out.append(entry)
    return out, records


def records_snapshot(
    steps: Sequence[StepSpec], records: Sequence[StepRecord]
) -> list[dict[str, Any]]:
    """The run's ``steps_snapshot``: every step with what it did (or nothing yet)."""
    by_id = {r.step_id: asdict(r) for r in records}
    return [{**s.snapshot(), **by_id.get(str(s.id), {})} for s in steps]


__all__ = [
    "ENTRIES_MOUNT",
    "ENTRIES_OUTPUT",
    "StepFailed",
    "StepRecord",
    "StepRunner",
    "StepSpec",
    "apply_steps_to_entries",
    "enabled_steps",
    "records_snapshot",
    "snapshot_of",
]
