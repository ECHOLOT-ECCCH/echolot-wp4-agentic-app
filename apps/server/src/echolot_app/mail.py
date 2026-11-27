"""Outgoing e-mail (docs/PLAN.md 7.12): through an SMTP server, or as .eml files in the data
directory for development and tests. Plain text only."""

from __future__ import annotations

import asyncio
import os
import smtplib
import ssl
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path
from typing import Protocol

import structlog

from echolot_app.settings import Settings

log = structlog.get_logger(__name__)

# the sender of .eml files when ECHO_MAIL_FROM is unset
_FILE_SENDER = "ECHOLOT Workbench <workbench@localhost>"


class MailError(RuntimeError):
    """A message that could not be sent, with a reason fit for an operator."""


@dataclass(frozen=True)
class Mail:
    to: str
    subject: str
    body: str


class Mailer(Protocol):
    async def send(self, mail: Mail) -> None: ...


def build_message(sender: str, mail: Mail) -> EmailMessage:
    """The message as it goes out. Header values with a line break are refused by the email
    package, so a subject made from what a person typed cannot add headers."""
    domain = parseaddr(sender)[1].rpartition("@")[2] or "localhost"
    message = EmailMessage()
    message["From"] = sender
    message["To"] = mail.to
    message["Subject"] = mail.subject
    message["Date"] = formatdate(usegmt=True)
    # given a domain, make_msgid does not look up this host's name, which can be slow
    message["Message-ID"] = make_msgid(domain=domain)
    # RFC 3834: no out-of-office replies to a machine
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(mail.body)
    return message


class SmtpMailer:
    """Sends through an SMTP server with STARTTLS, TLS or neither, logging in when a user name
    is set. smtplib blocks, so each message is sent in a thread."""

    def __init__(self, settings: Settings) -> None:
        self.host = settings.smtp_host
        self.port = settings.smtp_port
        self.security = settings.smtp_security
        self.username = settings.smtp_username
        self._password = settings.smtp_password
        self.timeout = settings.smtp_timeout_s
        self.sender = settings.mail_from

    async def send(self, mail: Mail) -> None:
        await asyncio.to_thread(self._send, build_message(self.sender, mail))

    def _send(self, message: EmailMessage) -> None:
        context = ssl.create_default_context()
        try:
            client: smtplib.SMTP
            if self.security == "tls":
                client = smtplib.SMTP_SSL(
                    self.host, self.port, timeout=self.timeout, context=context
                )
            else:
                client = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            with client:
                if self.security == "starttls":
                    client.starttls(context=context)
                if self.username:
                    password = self._password.get_secret_value() if self._password else ""
                    client.login(self.username, password)
                client.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            raise MailError(f"{self.host}:{self.port}: {type(exc).__name__}: {exc}") from exc


class FileMailer:
    """Writes each message to a directory as an .eml file, readable only by this user: the
    files hold links that work like passwords."""

    def __init__(self, directory: Path, sender: str) -> None:
        self.directory = directory
        self.sender = sender or _FILE_SENDER

    async def send(self, mail: Mail) -> None:
        data = build_message(self.sender, mail).as_bytes()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        path = self.directory / f"{stamp}-{uuid.uuid4().hex[:8]}.eml"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
        except OSError as exc:
            raise MailError(f"cannot write {path}: {exc}") from exc


class NoMailer:
    async def send(self, mail: Mail) -> None:
        raise MailError("no mail backend is configured (ECHO_MAIL_BACKEND)")


def build_mailer(settings: Settings) -> Mailer:
    match settings.mail_backend:
        case "smtp":
            return SmtpMailer(settings)
        case "file":
            return FileMailer(settings.data_dir / "outbox", settings.mail_from)
        case _:
            return NoMailer()


def _domain(address: str) -> str:
    return address.rpartition("@")[2] or "?"


async def send_all(mailer: Mailer, mails: Sequence[Mail]) -> None:
    """Send each message; a failure is logged and the rest still go. The log names the subject
    and the recipient's domain, never the body, which may hold a link."""
    for mail in mails:
        try:
            await mailer.send(mail)
        except MailError as exc:
            log.warning("mail.failed", subject=mail.subject, to=_domain(mail.to), error=str(exc))
        else:
            log.info("mail.sent", subject=mail.subject, to=_domain(mail.to))
