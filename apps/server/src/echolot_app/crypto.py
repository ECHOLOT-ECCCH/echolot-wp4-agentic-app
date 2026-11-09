"""Encryption at rest for the users' model keys (Fernet, with key rotation)."""

from __future__ import annotations

import binascii
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class SecretBoxError(ValueError):
    """A malformed key, or a token the configured keys cannot decrypt."""


class SecretBox:
    """Encrypts and decrypts strings.

    ``keys`` are Fernet keys. The first encrypts; all decrypt. To rotate: put a new key first,
    restart, have every user's key re-saved (or re-encrypt with ``rotate``), then drop the old one.
    """

    def __init__(self, keys: list[str]) -> None:
        if not keys:
            raise SecretBoxError("at least one secret key is required")
        fernets: list[Fernet] = []
        for key in keys:
            try:
                fernets.append(Fernet(key.strip().encode()))
            except (ValueError, TypeError, binascii.Error) as exc:
                raise SecretBoxError(
                    "ECHO_SECRET_KEY is not a valid Fernet key; generate one with "
                    "python -c 'from cryptography.fernet import Fernet; "
                    "print(Fernet.generate_key().decode())'"
                ) from exc
        self._fernet = MultiFernet(fernets)

    @classmethod
    def from_setting(cls, value: str | None, key_file: Path) -> SecretBox:
        """Keys from the setting (comma-separated), else from ``key_file``, created with a new
        key (mode 0600) when it does not exist yet."""
        if value and value.strip():
            return cls([k for k in value.split(",") if k.strip()])
        if not key_file.exists():
            key_file.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(Fernet.generate_key().decode() + "\n")
        return cls([k for k in key_file.read_text(encoding="ascii").split(",") if k.strip()])

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise SecretBoxError("cannot decrypt a stored key with the configured keys") from exc

    def rotate(self, token: str) -> str:
        """Re-encrypt a token with the first key."""
        return self._fernet.rotate(token.encode("ascii")).decode("ascii")


def key_hint(value: str) -> str:
    """The last four characters of a key, for the user to recognise it; never for logs."""
    value = value.strip()
    return f"…{value[-4:]}" if len(value) > 8 else "…"
