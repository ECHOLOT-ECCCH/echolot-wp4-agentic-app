"""Account administration from the server's shell (docs/PLAN.md 7.11).

    uv run echolot-admin create-user ana@example.org --name "Ana" --admin
    uv run echolot-admin set-password ana@example.org
    uv run echolot-admin list-users
    uv run echolot-admin disable-user ana@example.org   (enable-user to undo)

Uses the same settings as the app (ECHO_DATA_DIR, .env). Without --password-stdin a temporary
password is generated and printed once; the user changes it under Settings → Account.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from echolot_app.auth import (
    AccountError,
    create_user,
    end_all_sessions,
    find_user,
    generate_password,
    set_password,
)
from echolot_app.db import Database, User
from echolot_app.settings import get_settings


def _password(args: argparse.Namespace) -> tuple[str, bool]:
    """The password from stdin, or a generated one; and whether it was generated."""
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n"), False
    return generate_password(), True


async def _run(args: argparse.Namespace) -> int:
    database = Database(get_settings())
    await database.create_all()
    try:
        async with database.session() as db:
            if args.command == "list-users":
                users = (await db.execute(select(User).order_by(User.email))).scalars()
                for user in users:
                    flags = ",".join(
                        f
                        for f, on in (("admin", user.is_admin), ("disabled", not user.is_active))
                        if on
                    )
                    print(f"{user.email}\t{user.name}\t{flags}")
                return 0
            if args.command == "create-user":
                password, generated = _password(args)
                user = await create_user(
                    db, args.email, args.name or "", password, is_admin=args.admin
                )
                await db.commit()
                role = "admin" if user.is_admin else "user"
                print(f"Created {role} {user.email}.")
                if generated:
                    print(f"Temporary password: {password}")
                return 0
            user = await find_user(db, args.email)
            if user is None:
                print(f"No account for {args.email}.", file=sys.stderr)
                return 1
            if args.command == "set-password":
                password, generated = _password(args)
                await set_password(db, user, password)
                await db.commit()
                print(f"New password set for {user.email}; their sessions ended.")
                if generated:
                    print(f"Temporary password: {password}")
            elif args.command in ("disable-user", "enable-user"):
                user.is_active = args.command == "enable-user"
                if not user.is_active:
                    await end_all_sessions(db, user.id)
                await db.commit()
                print(f"{user.email} is {'enabled' if user.is_active else 'disabled'}.")
            return 0
    except AccountError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        await database.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="echolot-admin", description="Manage user accounts.")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-user", help="create an account")
    create.add_argument("email")
    create.add_argument("--name", default="")
    create.add_argument("--admin", action="store_true", help="may manage accounts")
    create.add_argument("--password-stdin", action="store_true", help="read the password")
    reset = commands.add_parser("set-password", help="set a new password, ending sessions")
    reset.add_argument("email")
    reset.add_argument("--password-stdin", action="store_true", help="read the password")
    commands.add_parser("list-users", help="list accounts")
    for name in ("disable-user", "enable-user"):
        commands.add_parser(name, help=f"{name.split('-')[0]} an account").add_argument("email")
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
