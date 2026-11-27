"""Application settings from ``ECHO_*`` environment variables and an optional .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_env_file() -> Path | None:
    """Walk up from the current directory to find a .env file (repo root in development)."""
    for directory in [Path.cwd(), *Path.cwd().parents]:
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ECHO_",
        env_file=_find_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # SQLite database (app.db) and stored files live here
    data_dir: Path = Path("./data")

    # The server's own model, offered to every user as "Server default" when it is usable (a
    # key is set, or the provider needs none). Users can bring their own keys instead
    # (docs/PLAN.md 7.11). Provider "test" needs enable_test_model and no key.
    model_provider: str = "anthropic"
    model_id: str = "claude-sonnet-5-5"
    model_api_key: SecretStr | None = None
    model_base_url: str | None = None
    enable_test_model: bool = False
    # lets users add an OpenAI-compatible endpoint of their own; off by default, because the
    # server then calls whatever URL a user enters (the server model's base URL is unaffected)
    allow_custom_endpoints: bool = False

    # Login (docs/PLAN.md 7.11). Encrypts the users' model keys at rest: one or more Fernet keys,
    # comma-separated, the first encrypts (rotation). Unset: generated once into
    # <data_dir>/secret.key.
    secret_key: SecretStr | None = None
    # a session ends after this many days without a request
    session_days: int = 14
    # the session cookie's Secure flag; unset: on when public_url is https
    cookie_secure: bool | None = None
    # failed logins per e-mail address and per client address in the window, then 429
    login_max_failures: int = 10
    login_window_s: int = 900

    # Self-registration (docs/PLAN.md 7.12): people ask for an account, confirm their e-mail
    # address, and an admin approves them. Off by default; needs a mail backend.
    allow_registration: bool = False
    # who reviews the requests, as the pages and e-mails put it ("... reviews each request")
    registration_reviewer: str = "An administrator"
    # requests waiting at once (unconfirmed or unapproved); beyond that, 503
    registration_max_pending: int = 50
    # requests per e-mail address and per client address in the window, then 429
    registration_max_attempts: int = 5
    registration_window_s: int = 3600
    # the address people open in a browser, for links in e-mails; unset: public_url (which in
    # a Compose stack may be an internal address only the runner uses)
    site_url: str | None = None

    # Mail (7.12): "smtp" sends through smtp_host; "file" writes each message as an .eml file to
    # <data_dir>/outbox (development, tests); "none" sends nothing
    mail_backend: Literal["none", "smtp", "file"] = "none"
    # the From address, e.g. "ECHOLOT Workbench <noreply@example.org>"
    mail_from: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    # "starttls" upgrades a plain connection (port 587), "tls" connects with TLS (port 465)
    smtp_security: Literal["starttls", "tls", "none"] = "starttls"
    smtp_username: str = ""
    smtp_password: SecretStr | None = None
    smtp_timeout_s: float = 20.0

    # Where runs execute (docs/PLAN.md 7.6) and the URL the runner uses to reach this app
    executor: Literal["local", "prefect"] = "local"
    public_url: str = "http://127.0.0.1:8000"
    prefect_api_url: str = "http://127.0.0.1:4200/api"
    # user:password for a server with PREFECT_SERVER_API_AUTH_STRING, or an API key (Prefect
    # Cloud, a proxy that wants a bearer token); the auth string wins when both are set
    prefect_auth_string: SecretStr | None = None
    prefect_api_key: SecretStr | None = None
    prefect_deployment: str = "run-script/default"
    # the agent-facing service docs (docs/PLAN.md 7.7)
    services_dir: Path = Path("./services")
    # LocalExecutor: the services the runner subprocess may call, and the variables (service
    # keys) it gets on top of this process's environment
    local_services_file: Path = Path("./services.toml")
    local_runner_env_file: Path = Path("./runner.env")
    run_poll_interval_s: float = 3.0
    # The poller stops a running run in which no record has finished for this long, or that has
    # run this long (docs/PLAN.md M6.3); 0 turns either off
    run_stall_minutes: float = 30.0
    run_max_hours: float = 24.0

    log_level: str = "INFO"
    log_json: bool = False
    # the API's documentation pages, /api/docs and /api/openapi.json, which answer without a
    # session; on for development, off in the Compose deployment (docs/PLAN.md M6.5)
    api_docs: bool = True

    @model_validator(mode="after")
    def _registration_needs_mail(self) -> Settings:
        if self.mail_backend == "smtp" and not (self.smtp_host and self.mail_from):
            raise ValueError("ECHO_MAIL_BACKEND=smtp needs ECHO_SMTP_HOST and ECHO_MAIL_FROM")
        if self.allow_registration and self.mail_backend == "none":
            raise ValueError(
                "ECHO_ALLOW_REGISTRATION needs mail to confirm addresses: set ECHO_MAIL_BACKEND"
            )
        return self

    @property
    def site_address(self) -> str:
        """Where a browser reaches this app, without a trailing slash."""
        return (self.site_url or self.public_url).rstrip("/")

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{(self.data_dir / 'app.db').resolve()}"

    @property
    def secure_cookies(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.public_url.startswith("https://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
