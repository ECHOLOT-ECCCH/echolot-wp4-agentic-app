"""Discovery of pipeline implementations (PLAN 7.7).

Built-in implementations are registered here; other packages add theirs with an entry point in
the group ``elexifier.pipeline`` whose name is ``<kind>.<name>`` (for example
``segmenter.mydictionary``) and whose value is the class.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

from elexifier_pipeline.pdf.segmenters import RuleSegmenter

GROUP = "elexifier.pipeline"

_BUILTIN: dict[str, dict[str, Any]] = {
    "segmenter": {"rule": RuleSegmenter},
}


def _plugins(kind: str) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for point in entry_points(group=GROUP):
        prefix = f"{kind}."
        if point.name.startswith(prefix):
            found[point.name[len(prefix) :]] = point.load()
    return found


def available(kind: str) -> list[str]:
    return sorted({**_BUILTIN.get(kind, {}), **_plugins(kind)})


def get_segmenter(name: str = "rule", params: dict[str, Any] | None = None) -> Any:
    registry = {**_BUILTIN["segmenter"], **_plugins("segmenter")}
    try:
        cls = registry[name]
    except KeyError as exc:
        raise KeyError(
            f"unknown segmenter {name!r}; available: {', '.join(sorted(registry))}"
        ) from exc
    return cls(params)


__all__ = ["GROUP", "available", "get_segmenter"]
