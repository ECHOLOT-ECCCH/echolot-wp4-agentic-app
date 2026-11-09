"""Account administration from the server's shell (docs/PLAN.md 7.11).

    uv run echolot-admin create-user ana@example.org --name "Ana" --admin
    uv run echolot-admin set-password ana@example.org
    uv run echolot-admin list-users
    uv run echolot-admin disable-user ana@example.org   (enable-user to undo)
    uv run echolot-admin check-runs     (the Prefect server answers, a runner serves the flow)

Uses the same settings as the app (ECHO_DATA_DIR, .env). Without --password-stdin a temporary
password is generated and printed once; the user changes it under Settings → Account.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from urllib.parse import urlsplit

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
from echolot_app.runs.prefect import PrefectExecutor, display_url
from echolot_app.settings import Settings, get_settings
from echolot_app.state import build_executor


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


async def _check_runs(settings: Settings) -> int:
    """Where runs go, and whether that works from here: the same settings as the app's."""
    executor = build_executor(settings)
    if not isinstance(executor, PrefectExecutor):
        print("Runs execute as subprocesses of the app (ECHO_EXECUTOR=local).")
        return 0
    print(
        f"Runs go to the Prefect server at {display_url(settings.prefect_api_url)} "
        f"({executor.auth}), deployment {settings.prefect_deployment}."
    )
    try:
        found = await executor.check()
    finally:
        await executor.aclose()
    for fine, what in found:
        print(f"{'ok  ' if fine else 'FAIL'}  {what}")
    host = urlsplit(settings.public_url).hostname or ""
    reach = f"the runner fetches input and sends results to {settings.public_url} (ECHO_PUBLIC_URL)"
    if host in ("localhost", "127.0.0.1", "::1"):
        reach += ": only a runner on this machine reaches that"
    elif "." not in host:
        reach += ": only a runner on the same Docker network reaches that"
    print(f"note  {reach}")
    return 0 if all(fine for fine, _ in found) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="echolot-admin", description="Manage user accounts; check the run setup."
    )
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
    commands.add_parser("check-runs", help="check the Prefect server and the runner")
    args = parser.parse_args(argv)
    if args.command == "check-runs":
        return asyncio.run(_check_runs(get_settings()))
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
