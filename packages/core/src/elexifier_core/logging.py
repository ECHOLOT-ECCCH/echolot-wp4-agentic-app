"""structlog configuration with secret redaction."""

from __future__ import annotations

import logging
import re
from collections.abc import MutableMapping
from typing import Any

import structlog

_SECRET_KEYS = re.compile(r"(api[_-]?key|secret|token|password|authorization|credential)", re.I)
_SECRET_VALUES = re.compile(
    r"\b(sk-[A-Za-z0-9_\-]{8,}|sk-or-[A-Za-z0-9_\-]{8,}|gsk_[A-Za-z0-9]{8,})\b"
)


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return _SECRET_VALUES.sub("***", value)
    if isinstance(value, dict):
        return {k: ("***" if _SECRET_KEYS.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(redact(v) for v in value)
    return value


def redact_processor(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict.keys()):
        if key == "event":
            event_dict[key] = redact(event_dict[key])
        elif _SECRET_KEYS.search(key):
            event_dict[key] = "***"
        else:
            event_dict[key] = redact(event_dict[key])
    return event_dict


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        redact_processor,
    ]
    renderer = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*shared, structlog.processors.StackInfoRenderer(), renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=level.upper(), format="%(message)s")
