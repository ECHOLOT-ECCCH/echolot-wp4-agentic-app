"""Agent-facing service documentation: ``services/<name>.md`` (docs/PLAN.md 7.7).

Each file starts with a front matter block (``name`` and ``description``), then the Markdown the
agent reads before writing a script against the service. The app never holds a service's URL or
key; those live in the runner host's ``services.toml`` under the same name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class ServiceDoc:
    name: str
    description: str
    body: str


def parse_service_doc(text: str, fallback_name: str) -> ServiceDoc:
    meta: dict[str, str] = {}
    body = text
    match = _FRONT.match(text)
    if match:
        for line in match.group(1).splitlines():
            key, sep, value = line.partition(":")
            if sep:
                meta[key.strip().lower()] = value.strip().strip("\"'")
        body = text[match.end() :]
    return ServiceDoc(
        name=meta.get("name") or fallback_name,
        description=meta.get("description", ""),
        body=body.strip() + "\n",
    )


def load_service_docs(directory: Path) -> dict[str, ServiceDoc]:
    """Every ``*.md`` in the directory with a valid name; read on each call (docs change)."""
    docs: dict[str, ServiceDoc] = {}
    if not directory.is_dir():
        return docs
    for path in sorted(directory.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        doc = parse_service_doc(path.read_text(encoding="utf-8"), path.stem)
        if _NAME.match(doc.name):
            docs[doc.name] = doc
    return docs
