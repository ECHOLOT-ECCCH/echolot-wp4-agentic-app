import pytest

from elexifier_core.crypto import SecretBox, SecretBoxError, mask_secret


def test_round_trip() -> None:
    box = SecretBox([SecretBox.generate_key()])
    token = box.encrypt("sk-or-v1-abcdef")
    assert token != "sk-or-v1-abcdef"
    assert box.decrypt(token) == "sk-or-v1-abcdef"


def test_wrong_key_fails() -> None:
    token = SecretBox([SecretBox.generate_key()]).encrypt("secret")
    with pytest.raises(SecretBoxError):
        SecretBox([SecretBox.generate_key()]).decrypt(token)


def test_rotation_keeps_old_tokens_readable() -> None:
    old, new = SecretBox.generate_key(), SecretBox.generate_key()
    token = SecretBox([old]).encrypt("value")
    rotated_box = SecretBox([new, old])
    assert rotated_box.decrypt(token) == "value"
    fresh = rotated_box.rotate(token)
    assert SecretBox([new]).decrypt(fresh) == "value"
    with pytest.raises(SecretBoxError):
        SecretBox([old]).decrypt(fresh)


def test_invalid_key_is_reported() -> None:
    with pytest.raises(SecretBoxError, match="invalid Fernet key"):
        SecretBox(["not-a-key"])
    with pytest.raises(SecretBoxError):
        SecretBox([])
    assert SecretBox.is_valid_key(SecretBox.generate_key())
    assert not SecretBox.is_valid_key("short")


def test_mask_secret() -> None:
    assert mask_secret("sk-or-v1-abcdef1234") == "***1234"
    assert mask_secret("ab") == "***"
    assert mask_secret(None) == ""
