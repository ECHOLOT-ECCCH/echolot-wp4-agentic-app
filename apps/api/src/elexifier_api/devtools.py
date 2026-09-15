"""Development and test helpers that talk to the database directly (never exposed over HTTP).

    python -m elexifier_api.devtools session --email dev@example.com [--name "Dev"] [--admin]

prints ``{"session_id", "csrf", "user_id", "email"}`` for a fresh browser session of that user,
creating the user if needed. Used by the Playwright smoke tests so they do not depend on Keycloak.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid

from sqlalchemy import select

from elexifier_api.auth.session import SessionStore
from elexifier_core.db.models import User, UserRole
from elexifier_core.db.session import Database
from elexifier_core.settings import get_settings


async def create_session(email: str, name: str, admin: bool) -> dict[str, str]:
    settings = get_settings()
    if settings.is_production:
        raise SystemExit("refusing to create sessions in production")
    db = Database(settings)
    store = SessionStore(settings.elx_session_secret, settings.cookie_secure)
    try:
        async with db.session() as session:
            user = (
                await session.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            if user is None:
                user = User(oidc_sub=f"devtools-{uuid.uuid4()}", email=email, name=name)
                session.add(user)
                await session.flush()
            user.role = UserRole.ADMIN.value if admin else UserRole.USER.value
            if name:
                user.name = name
            created = await store.create(session, user, user_agent="devtools")
            await session.commit()
            return {
                "session_id": str(created.id),
                "csrf": created.csrf_token,
                "user_id": str(user.id),
                "email": user.email,
            }
    finally:
        await db.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="elexifier-devtools")
    sub = parser.add_subparsers(dest="command", required=True)
    session = sub.add_parser("session", help="create a browser session for a user")
    session.add_argument("--email", required=True)
    session.add_argument("--name", default="")
    session.add_argument("--admin", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "session":
        result = asyncio.run(create_session(args.email, args.name, args.admin))
        json.dump(result, sys.stdout)
        sys.stdout.write("\n")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
