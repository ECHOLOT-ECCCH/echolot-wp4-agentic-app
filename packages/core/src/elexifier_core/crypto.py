"""Encryption of secrets at rest (provider API keys) with Fernet and key rotation support."""

from __future__ import annotations

import base64
import binascii

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

REDACTED = "***"


class SecretBoxError(ValueError):
    """Raised for malformed keys or undecryptable tokens."""


class SecretBox:
    """Encrypts and decrypts strings.

    ``keys`` are Fernet keys (urlsafe base64 of 32 bytes). The first key encrypts; all keys can
    decrypt, which allows rotation: add the new key first, redeploy, then call ``rotate`` on stored
    tokens and finally drop the old key.
    """

    def __init__(self, keys: list[str]) -> None:
        if not keys:
            raise SecretBoxError("at least one secret key is required")
        fernets: list[Fernet] = []
        for key in keys:
            try:
                fernets.append(Fernet(key.encode() if isinstance(key, str) else key))
            except (ValueError, TypeError, binascii.Error) as exc:
                raise SecretBoxError(
                    "invalid Fernet key; generate one with "
                    "python -c 'from cryptography.fernet import Fernet; "
                    "print(Fernet.generate_key().decode())'"
                ) from exc
        self._fernet = MultiFernet(fernets)

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode()

    @staticmethod
    def is_valid_key(key: str) -> bool:
        try:
            return len(base64.urlsafe_b64decode(key.encode())) == 32
        except (ValueError, binascii.Error):
            return False

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise SecretBoxError("cannot decrypt token with the configured keys") from exc

    def rotate(self, token: str) -> str:
        """Re-encrypt a token with the primary key."""
        return self._fernet.rotate(token.encode("ascii")).decode("ascii")


def mask_secret(value: str | None, keep: int = 4) -> str:
    """Show only the last few characters of a secret for display, never for logs."""
    if not value:
        return ""
    if len(value) <= keep:
        return REDACTED
    return f"{REDACTED}{value[-keep:]}"
