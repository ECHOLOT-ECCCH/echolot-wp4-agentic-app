"""The connection between the harness and a script's worker (docs/DECISIONS.md 2026-11-24):
one JSON object per line over a stream socket, each with its kind in ``"t"``. The messages are
listed in worker.py. Either side treats what the other sends as untrusted. Standard library
only."""

from __future__ import annotations

import contextlib
import json
import socket
import threading
from collections.abc import Callable
from typing import Any

MAX_MESSAGE_BYTES = 32 * 1024 * 1024


class ProtocolError(Exception):
    """The other side sent something that is not a message."""


class Channel:
    """Sends from any thread (one message at a time); receives from one thread."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self._reader = sock.makefile("rb")
        self._lock = threading.Lock()

    def send(self, message: dict[str, Any], *, default: Callable[[Any], Any] | None = None) -> None:
        self.send_bytes(encode(message, default=default))

    def send_bytes(self, data: bytes) -> None:
        with self._lock:
            self.sock.sendall(data)

    def receive(self) -> dict[str, Any] | None:
        """The next message, or None when the other side has closed the connection."""
        line = self._reader.readline(MAX_MESSAGE_BYTES + 1)
        if not line:
            return None
        if not line.endswith(b"\n"):
            raise ProtocolError("a message longer than the limit, or cut off")
        try:
            message = json.loads(line)
        except ValueError as exc:
            raise ProtocolError("a message that is not JSON") from exc
        if not isinstance(message, dict) or not isinstance(message.get("t"), str):
            raise ProtocolError("JSON that is not a message")
        return message

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.sock.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(OSError):
            self._reader.close()
        with contextlib.suppress(OSError):
            self.sock.close()


def encode(message: dict[str, Any], *, default: Callable[[Any], Any] | None = None) -> bytes:
    """One message as a line; ValueError when it is too large, TypeError when it is not JSON."""
    data = json.dumps(message, ensure_ascii=False, default=default, separators=(",", ":"))
    line = data.encode("utf-8") + b"\n"
    if len(line) > MAX_MESSAGE_BYTES:
        raise ValueError(f"{len(line)} bytes, more than {MAX_MESSAGE_BYTES} in one message")
    return line
