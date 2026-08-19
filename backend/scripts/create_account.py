"""
create_account.py — seed a login account (no default credentials, ever).

Usage (from the repo root)::

    python -m backend.scripts.create_account --user-id 7 --username admin --role super_admin

The password is read interactively (getpass) and never accepted on the
command line, so it can't leak into shell history or the process table.
Pass ``--username`` on the CLI or you'll be prompted for it too.

This is the only supported way to create the first ``super_admin``; after
that, accounts are managed through the API (Phase 2).
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

# Make ``backend.*`` importable when run as a module or a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.app.db.auth_models import Role  # noqa: E402
from backend.app.db.postgres import SessionLocal, init_db  # noqa: E402
from backend.app.services import auth_service  # noqa: E402


def _prompt_password() -> str:
    while True:
        pw = getpass.getpass("Password: ")
        if len(pw) < 8:
            print("  Password must be at least 8 characters.", file=sys.stderr)
            continue
        confirm = getpass.getpass("Confirm password: ")
        if pw != confirm:
            print("  Passwords do not match. Try again.", file=sys.stderr)
            continue
        return pw


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an Iris login account.")
    parser.add_argument("--username", help="Login username (prompted if omitted).")
    parser.add_argument(
        "--user-id",
        type=int,
        help="Existing employee/doctor user id that receives access.",
    )
    parser.add_argument(
        "--role",
        choices=[r.value for r in Role],
        default=Role.SUPER_ADMIN.value,
        help="Account role (default: super_admin).",
    )
    args = parser.parse_args()

    username = args.username or input("Username: ").strip()
    if not username:
        print("Username must not be empty.", file=sys.stderr)
        return 2

    role = Role(args.role)
    if args.user_id is None:
        print("--user-id is required for a human login.", file=sys.stderr)
        return 2
    password = _prompt_password()

    # Ensure the table exists (first-run bootstrap).
    init_db()

    db = SessionLocal()
    try:
        acct = auth_service.create_account(
            db,
            user_id=args.user_id,
            username=username,
            password=password,
            role=role,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()

    print(f"Created account #{acct.id}: {acct.username} ({acct.role})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
