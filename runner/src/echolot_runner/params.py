"""The runner's one parameter (docs/PLAN.md 7.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ParamsError(ValueError):
    pass


@dataclass(frozen=True)
class RunnerParams:
    run_id: str
    script: str
    input_url: str
    progress_url: str
    result_url: str
    token: str
    services: list[str] = field(default_factory=list)
    script_params: dict[str, Any] = field(default_factory=dict)
    script_sha256: str | None = None
    limit: int | None = None
    concurrency: int = 4

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RunnerParams:
        required = ("run_id", "script", "input_url", "progress_url", "result_url", "token")
        missing = [k for k in required if not isinstance(raw.get(k), str) or not raw.get(k)]
        if missing:
            raise ParamsError(f"missing parameters: {', '.join(missing)}")
        known = set(cls.__dataclass_fields__)
        values = {k: v for k, v in raw.items() if k in known}
        concurrency = int(values.get("concurrency") or 4)
        values["concurrency"] = max(1, min(concurrency, 64))
        if values.get("limit") is not None:
            values["limit"] = max(0, int(values["limit"]))
        values["services"] = [str(s) for s in values.get("services") or []]
        values["script_params"] = dict(values.get("script_params") or {})
        return cls(**values)
