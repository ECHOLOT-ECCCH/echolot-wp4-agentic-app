from __future__ import annotations

from pathlib import Path

import pytest

from echolot_runner.services import ServicesConfigError, load_services


def test_load_services(tmp_path: Path) -> None:
    path = tmp_path / "services.toml"
    path.write_text(
        '[echolot]\nurl = "http://127.0.0.1:5000"\nauth_header = "X-API-Key"\n'
        'key_env = "ECHOLOT_API_KEY"\nconcurrency = 4\ntimeout_s = 70\nretry_statuses = [502]\n',
        encoding="utf-8",
    )
    config = load_services(path)["echolot"]
    assert config.url == "http://127.0.0.1:5000"
    assert config.retry_statuses == (502,)
    assert load_services(tmp_path / "missing.toml") == {}


def test_unknown_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "services.toml"
    path.write_text('[x]\nurl = "http://x"\napi_key = "inline secret"\n', encoding="utf-8")
    with pytest.raises(ServicesConfigError, match=r"unknown keys \['api_key'\]"):
        load_services(path)


def test_the_example_file_loads() -> None:
    example = Path(__file__).parents[1] / "services.example.toml"
    assert set(load_services(example)) == {"echolot"}
