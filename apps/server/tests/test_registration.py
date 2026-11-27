"""Requests for an account: asking, confirming the address, approval (docs/PLAN.md 7.12)."""

from __future__ import annotations

import asyncio
import re
import smtplib
import sqlite3
import stat
import uuid
from datetime import timedelta
from email import message_from_bytes
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from pydantic import SecretStr, ValidationError
from sqlalchemy import select, update

from echolot_app import registration
from echolot_app.auth import LoginLimiter, hash_password
from echolot_app.cli import main as cli_main
from echolot_app.db import Database, EmailToken, User, utcnow
from echolot_app.mail import FileMailer, Mail, MailError, SmtpMailer, build_message
from echolot_app.main import create_app
from echolot_app.registration import AccountRequest
from echolot_app.settings import Settings
from echolot_app.state import AppState

from .conftest import API_HEADERS, PASSWORD, sign_in

MAJA_PASSWORD = "a long enough passphrase"
FORM: dict[str, str] = {
    "email": " Maja@Example.org ",
    "name": "Maja Novak",
    "organisation": "Example Institute",
    "purpose": "Reconcile the people in our archive\nwith Wikidata.",
    "password": MAJA_PASSWORD,
}


class MemoryMailer:
    def __init__(self) -> None:
        self.sent: list[Mail] = []

    async def send(self, mail: Mail) -> None:
        self.sent.append(mail)

    def to(self, address: str) -> list[Mail]:
        return [mail for mail in self.sent if mail.to == address]


class FailingMailer:
    async def send(self, mail: Mail) -> None:
        raise MailError("the mail server is down")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Registration open; links point at the site, not at the address the runner uses."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        data_dir=tmp_path / "data",
        model_provider="test",
        model_id="test",
        enable_test_model=True,
        public_url="http://app:8000",
        site_url="https://workbench.example.org/",
        allow_registration=True,
        mail_backend="file",
        registration_reviewer="An administrator at the Institute",
    )


@pytest.fixture
def outbox(state: AppState) -> MemoryMailer:
    mailer = MemoryMailer()
    state.mailer = mailer
    return mailer


def token_in(mail: Mail) -> str:
    found = re.search(r"https://workbench\.example\.org/confirm-email\?token=(\S+)", mail.body)
    assert found, mail.body
    return found.group(1)


async def request_account(anonymous: httpx.AsyncClient, **changes: str) -> httpx.Response:
    response = await anonymous.post("/api/auth/register", json={**FORM, **changes})
    assert response.status_code == 202, response.text
    assert response.json() == {"status": "check_email"}
    return response


async def find(state: AppState, email: str) -> User | None:
    async with state.db.session() as db:
        return (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()


async def login(c: httpx.AsyncClient, email: str, password: str) -> httpx.Response:
    return await c.post("/api/auth/login", json={"email": email, "password": password})


# --- open or closed ----------------------------------------------------------------------------


async def test_registration_is_closed_unless_turned_on(tmp_path: Path) -> None:
    app = create_app(Settings(_env_file=None, data_dir=tmp_path / "data"))  # type: ignore[call-arg]
    async with (
        LifespanManager(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t", headers=API_HEADERS
        ) as c,
    ):
        info = (await c.get("/api/auth/registration")).json()
        assert info == {"enabled": False, "reviewer": "An administrator"}
        assert (await c.post("/api/auth/register", json=FORM)).status_code == 404
        confirm = await c.post("/api/auth/confirm-email", json={"token": "x" * 40})
        assert confirm.status_code == 404


def test_registration_needs_mail_and_smtp_needs_a_server(tmp_path: Path) -> None:
    base: dict[str, Any] = {"_env_file": None, "data_dir": tmp_path}
    with pytest.raises(ValidationError, match="ECHO_MAIL_BACKEND"):
        Settings(**base, allow_registration=True)
    with pytest.raises(ValidationError, match="ECHO_SMTP_HOST"):
        Settings(**base, mail_backend="smtp", mail_from="Workbench <wb@example.org>")
    assert Settings(**base, mail_backend="file", allow_registration=True).allow_registration


async def test_a_request_needs_the_web_apps_header(app: Any) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.post("/api/auth/register", json=FORM)).status_code == 403


# --- the whole way -----------------------------------------------------------------------------


async def test_from_request_to_sign_in(
    client: httpx.AsyncClient,
    anonymous: httpx.AsyncClient,
    outbox: MemoryMailer,
    state: AppState,
) -> None:
    info = (await anonymous.get("/api/auth/registration")).json()
    assert info == {"enabled": True, "reviewer": "An administrator at the Institute"}

    await request_account(anonymous)
    [confirmation] = outbox.to("maja@example.org")
    assert confirmation.subject == "Confirm your e-mail address for the ECHOLOT Workbench"
    assert "An administrator at the Institute reviews each request" in confirmation.body
    token = token_in(confirmation)

    # the right password is told what is missing; a wrong one learns nothing
    refused = await login(anonymous, "maja@example.org", MAJA_PASSWORD)
    assert refused.status_code == 403
    assert "Confirm your e-mail address" in refused.json()["detail"]
    assert (await login(anonymous, "maja@example.org", "not her password")).status_code == 401

    [maja] = [u for u in (await client.get("/api/admin/users")).json() if u["name"] == "Maja Novak"]
    assert maja["status"] == "unconfirmed"
    assert maja["organisation"] == "Example Institute"
    assert maja["is_active"] is False
    assert (await client.post(f"/api/admin/users/{maja['id']}/approve")).status_code == 409

    confirmed = await anonymous.post("/api/auth/confirm-email", json={"token": token})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json() == {"status": "pending"}
    [notice] = outbox.to("ana@example.org")
    assert notice.subject == "Account request: Maja Novak (Example Institute)"
    assert "Reconcile the people in our archive\nwith Wikidata." in notice.body
    assert "https://workbench.example.org/settings?tab=users" in notice.body
    used = await anonymous.post("/api/auth/confirm-email", json={"token": token})
    assert used.status_code == 400

    waiting = await login(anonymous, "maja@example.org", MAJA_PASSWORD)
    assert waiting.status_code == 403
    assert "waiting for approval" in waiting.json()["detail"]
    # a request is approved or rejected, not edited
    patch = await client.patch(f"/api/admin/users/{maja['id']}", json={"is_active": True})
    assert patch.status_code == 409
    reset = await client.post(f"/api/admin/users/{maja['id']}/reset-password")
    assert reset.status_code == 409

    approved = await client.post(f"/api/admin/users/{maja['id']}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["is_active"] is True
    assert approved.json()["approved_at"]
    assert approved.json()["is_admin"] is False
    welcome = outbox.to("maja@example.org")[-1]
    assert "approved" in welcome.subject
    assert "https://workbench.example.org/login" in welcome.body

    assert (await login(anonymous, "maja@example.org", MAJA_PASSWORD)).status_code == 200
    me = (await anonymous.get("/api/me")).json()
    assert me["email"] == "maja@example.org"
    assert me["email_confirmed_at"]


async def test_an_address_with_an_account_gets_the_same_answer(
    client: httpx.AsyncClient, anonymous: httpx.AsyncClient, outbox: MemoryMailer
) -> None:
    await request_account(anonymous, email="ana@example.org", password="someone elses password")
    [mail] = outbox.to("ana@example.org")
    assert "you already have one" in mail.body
    assert (await login(anonymous, "ana@example.org", PASSWORD)).status_code == 200


async def test_a_second_request_cannot_swap_the_password(
    client: httpx.AsyncClient, anonymous: httpx.AsyncClient, outbox: MemoryMailer, state: AppState
) -> None:
    await request_account(anonymous)
    await request_account(anonymous, password="the intruders password", name="Someone Else")
    first, second = outbox.to("maja@example.org")
    assert (
        await anonymous.post("/api/auth/confirm-email", json={"token": token_in(first)})
    ).status_code == 400  # replaced by the second link
    assert (
        await anonymous.post("/api/auth/confirm-email", json={"token": token_in(second)})
    ).status_code == 200
    maja = await find(state, "maja@example.org")
    assert maja is not None and maja.name == "Maja Novak"
    assert (await client.post(f"/api/admin/users/{maja.id}/approve")).status_code == 200
    assert (await login(anonymous, "maja@example.org", "the intruders password")).status_code == 401
    assert (await login(anonymous, "maja@example.org", MAJA_PASSWORD)).status_code == 200

    # asking once more while approved: told by e-mail, nothing changes
    await request_account(anonymous)
    assert "you already have one" in outbox.to("maja@example.org")[-1].body


async def test_a_pending_request_asked_again_is_reminded(
    anonymous: httpx.AsyncClient, outbox: MemoryMailer
) -> None:
    await request_account(anonymous)
    token = token_in(outbox.sent[0])
    await anonymous.post("/api/auth/confirm-email", json={"token": token})
    await request_account(anonymous)
    assert "waiting for review" in outbox.to("maja@example.org")[-1].body


async def test_rejecting_deletes_the_request(
    client: httpx.AsyncClient, anonymous: httpx.AsyncClient, outbox: MemoryMailer, state: AppState
) -> None:
    await request_account(anonymous)
    await anonymous.post("/api/auth/confirm-email", json={"token": token_in(outbox.sent[0])})
    await request_account(anonymous, email="bot@example.org")
    maja, bot = await find(state, "maja@example.org"), await find(state, "bot@example.org")
    assert maja is not None and bot is not None

    assert (await client.post(f"/api/admin/users/{maja.id}/reject")).status_code == 204
    assert await find(state, "maja@example.org") is None
    assert "was not approved" in outbox.to("maja@example.org")[-1].body
    # an unconfirmed address may not be the requester's: deleted without a word
    sent_before = len(outbox.sent)
    assert (await client.post(f"/api/admin/users/{bot.id}/reject")).status_code == 204
    assert await find(state, "bot@example.org") is None
    assert len(outbox.sent) == sent_before
    ana = await find(state, "ana@example.org")
    assert ana is not None
    assert (await client.post(f"/api/admin/users/{ana.id}/reject")).status_code == 409
    # strangers and plain users cannot approve or reject
    assert (await anonymous.post(f"/api/admin/users/{ana.id}/approve")).status_code == 401


# --- abuse -------------------------------------------------------------------------------------


async def test_a_filled_hidden_field_records_nothing(
    anonymous: httpx.AsyncClient, outbox: MemoryMailer, state: AppState
) -> None:
    await request_account(anonymous, website="https://spam.example")
    assert outbox.sent == []
    assert await find(state, "maja@example.org") is None


async def test_requests_are_throttled(anonymous: httpx.AsyncClient, state: AppState) -> None:
    state.registration_limiter = LoginLimiter(max_failures=2, window_s=60)
    state.mailer = MemoryMailer()
    await request_account(anonymous)
    await request_account(anonymous, email="other@example.org")
    third = await anonymous.post("/api/auth/register", json={**FORM, "email": "x@example.org"})
    assert third.status_code == 429  # the same client address
    assert int(third.headers["Retry-After"]) > 0


async def test_too_many_waiting_requests_close_the_door(
    anonymous: httpx.AsyncClient, outbox: MemoryMailer, state: AppState
) -> None:
    state.settings.registration_max_pending = 1
    await request_account(anonymous)
    full = await anonymous.post("/api/auth/register", json={**FORM, "email": "b@example.org"})
    assert full.status_code == 503


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"password": "short"}, "at least 12 characters"),
        ({"email": "not-an-address"}, "not an e-mail address"),
        ({"name": "  "}, "fill in your name"),
        ({"organisation": "Bad\x00Org"}, "control characters"),
        ({"name": "Maja ‮evoN"}, "control characters"),
        ({"purpose": "\n\n"}, "fill in what you will use"),
    ],
)
async def test_what_people_type_is_checked(
    anonymous: httpx.AsyncClient, outbox: MemoryMailer, changes: dict[str, str], message: str
) -> None:
    response = await anonymous.post("/api/auth/register", json={**FORM, **changes})
    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert outbox.sent == []


def test_names_become_one_line_and_text_loses_control_characters() -> None:
    assert registration.one_line("  Maja\n  Novak ", "name") == "Maja Novak"
    assert registration.one_line("Ali‌Reza", "name") == "Ali‌Reza"  # Persian ZWNJ
    assert registration.free_text("a\r\nb\x07c\t", "x") == "a\nbc"


async def test_links_expire_and_unconfirmed_requests_go(
    anonymous: httpx.AsyncClient, outbox: MemoryMailer, state: AppState
) -> None:
    await request_account(anonymous)
    async with state.db.session() as db:
        await db.execute(update(EmailToken).values(expires_at=utcnow() - timedelta(minutes=1)))
        await db.commit()
    token = token_in(outbox.sent[0])
    expired = await anonymous.post("/api/auth/confirm-email", json={"token": token})
    assert expired.status_code == 400
    assert "expired" in expired.json()["detail"]

    async with state.db.session() as db:
        await db.execute(update(User).values(created_at=utcnow() - timedelta(days=3)))
        await db.commit()
    await request_account(anonymous, email="next@example.org")
    assert await find(state, "maja@example.org") is None
    async with state.db.session() as db:
        assert len((await db.execute(select(EmailToken))).scalars().all()) == 1


async def test_a_mail_server_failure_does_not_fail_the_request(
    anonymous: httpx.AsyncClient, state: AppState
) -> None:
    state.mailer = FailingMailer()
    await request_account(anonymous)
    assert await find(state, "maja@example.org") is not None


# --- an older database -------------------------------------------------------------------------


async def test_accounts_from_before_registration_are_approved(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    with sqlite3.connect(data / "app.db") as old:  # the M4 schema of users
        old.execute(
            "CREATE TABLE users (email VARCHAR(320) NOT NULL UNIQUE, name VARCHAR(200) NOT NULL, "
            "password_hash VARCHAR(255) NOT NULL, is_admin BOOLEAN NOT NULL, "
            "is_active BOOLEAN NOT NULL, model JSON NOT NULL, last_login_at DATETIME, "
            "id CHAR(32) NOT NULL PRIMARY KEY, updated_at DATETIME NOT NULL, "
            "created_at DATETIME NOT NULL)"
        )
        old.execute(
            "INSERT INTO users VALUES ('old@example.org', 'Old', ?, 1, 1, '{}', NULL, ?, "
            "'2026-11-09 10:00:00', '2026-11-09 10:00:00')",
            (hash_password(PASSWORD), uuid.uuid4().hex),
        )
    app = create_app(Settings(_env_file=None, data_dir=data))  # type: ignore[call-arg]
    async with (
        LifespanManager(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t", headers=API_HEADERS
        ) as c,
    ):
        await sign_in(c, "old@example.org")
        me = (await c.get("/api/me")).json()
        assert me["status"] == "approved"
        assert me["organisation"] == ""


# --- mail backends -----------------------------------------------------------------------------


async def test_the_file_backend_writes_private_messages(tmp_path: Path) -> None:
    mailer = FileMailer(tmp_path / "outbox", "")
    await mailer.send(Mail("maja@example.org", "Hello", "Body with a link\n"))
    [path] = (tmp_path / "outbox").glob("*.eml")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    message = message_from_bytes(path.read_bytes())
    assert message["To"] == "maja@example.org"
    assert message["Auto-Submitted"] == "auto-generated"
    assert message["Message-ID"].endswith("@localhost>")


def test_a_subject_cannot_carry_extra_headers() -> None:
    with pytest.raises(ValueError):
        build_message("wb@example.org", Mail("a@example.org", "Hi\r\nBcc: x@example.org", "b"))


async def test_the_smtp_backend_upgrades_and_logs_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            calls.append(("connect", host, port))

        def __enter__(self) -> FakeSMTP:
            return self

        def __exit__(self, *exc: object) -> None:
            calls.append("quit")

        def starttls(self, context: object) -> None:
            calls.append("starttls")

        def login(self, user: str, password: str) -> None:
            calls.append(("login", user, password))

        def send_message(self, message: EmailMessage) -> None:
            calls.append(("send", message["From"], message["To"]))

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        data_dir=tmp_path,
        mail_backend="smtp",
        mail_from="Workbench <wb@example.org>",
        smtp_host="smtp.example.org",
        smtp_username="wb",
        smtp_password=SecretStr("pw"),
    )
    await SmtpMailer(settings).send(Mail("maja@example.org", "Hi", "body"))
    assert calls == [
        ("connect", "smtp.example.org", 587),
        "starttls",
        ("login", "wb", "pw"),
        ("send", "Workbench <wb@example.org>", "maja@example.org"),
        "quit",
    ]

    def refuse(*args: object, **kwargs: object) -> None:
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(smtplib, "SMTP", refuse)
    with pytest.raises(MailError, match=r"smtp\.example\.org:587"):
        await SmtpMailer(settings).send(Mail("maja@example.org", "Hi", "body"))


# --- the command line --------------------------------------------------------------------------


def test_the_command_line_approves_rejects_and_tests_mail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from echolot_app.settings import get_settings

    data = tmp_path / "data"
    monkeypatch.setenv("ECHO_DATA_DIR", str(data))
    monkeypatch.setenv("ECHO_ALLOW_REGISTRATION", "true")
    monkeypatch.setenv("ECHO_MAIL_BACKEND", "file")
    monkeypatch.setenv("ECHO_SITE_URL", "https://workbench.example.org")
    get_settings.cache_clear()

    async def make_requests() -> None:
        settings = get_settings()
        database = Database(settings)
        await database.create_all()
        async with database.session() as db:
            for email in ("maja@example.org", "bot@example.org"):
                mails = await registration.register(
                    db, settings, AccountRequest(**{**FORM, "email": email})
                )
                if email.startswith("maja"):
                    await registration.confirm(db, settings, token_in(mails[0]))
            await db.commit()
        await database.dispose()

    try:
        asyncio.run(make_requests())
        assert cli_main(["list-users"]) == 0
        listed = capsys.readouterr().out
        assert "maja@example.org\tMaja Novak\tpending" in listed
        assert "bot@example.org\tMaja Novak\tunconfirmed" in listed
        assert cli_main(["disable-user", "maja@example.org"]) == 1
        assert cli_main(["approve-user", "bot@example.org"]) == 1  # not confirmed
        assert cli_main(["approve-user", "maja@example.org"]) == 0
        assert "told by e-mail" in capsys.readouterr().out
        assert cli_main(["reject-user", "bot@example.org"]) == 0
        assert "rejected and deleted" in capsys.readouterr().out
        assert cli_main(["list-users"]) == 0
        listed = capsys.readouterr().out
        assert "bot@example.org" not in listed
        assert "maja@example.org\tMaja Novak\t\n" in listed
        assert cli_main(["send-test-mail", "ops@example.org"]) == 0
        assert len(list((data / "outbox").glob("*.eml"))) == 2  # the approval and the test
    finally:
        get_settings.cache_clear()
