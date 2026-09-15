from elexifier_core.settings import Settings


def test_dsn_conversion_and_key_list(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/app")
    monkeypatch.setenv("ELX_SECRET_KEY", "k1, k2 ,k3")
    s = Settings()  # type: ignore[call-arg]
    assert s.database_dsn == "postgresql://u:p@db:5432/app"
    assert s.secret_keys == ["k1", "k2", "k3"]
    assert s.cookie_secure is False


def test_rejects_non_async_database_url(monkeypatch) -> None:
    import pytest

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/app")
    with pytest.raises(ValueError, match="asyncpg"):
        Settings()  # type: ignore[call-arg]
