"""Create the singleton facility and first super-administrator.

Run from the repository root after configuring the BOOTSTRAP_* variables::

    python -m backend.scripts.seed_initial

The command is idempotent: existing rows are preserved and passwords are
never printed or silently rotated on a later run.
"""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.app.core.config import settings  # noqa: E402
from backend.app.db.auth_models import Role  # noqa: E402
from backend.app.db.facility_models import Facility  # noqa: E402
from backend.app.db.models import User, UserType  # noqa: E402
from backend.app.db.postgres import SessionLocal, init_db  # noqa: E402
from backend.app.services import auth_service  # noqa: E402


def main() -> int:
    username = settings.BOOTSTRAP_SUPERADMIN_USERNAME.strip()
    password = settings.BOOTSTRAP_SUPERADMIN_PASSWORD
    facility_name = settings.BOOTSTRAP_FACILITY_NAME.strip() or "Mednet"
    superadmin_name = settings.BOOTSTRAP_SUPERADMIN_NAME.strip() or "Mednet Superadmin"

    if not username:
        print("BOOTSTRAP_SUPERADMIN_USERNAME must not be empty.", file=sys.stderr)
        return 2
    if len(password) < 8:
        print(
            "BOOTSTRAP_SUPERADMIN_PASSWORD must contain at least 8 characters.",
            file=sys.stderr,
        )
        return 2

    init_db()
    db = SessionLocal()
    try:
        facilities = db.query(Facility).order_by(Facility.id).limit(2).all()
        if len(facilities) > 1:
            print(
                "Cannot seed: multiple facility rows exist in a single-facility deployment.",
                file=sys.stderr,
            )
            return 1

        if facilities:
            facility = facilities[0]
            # Replace the old automatic placeholder left by pre-singleton
            # builds. Preserve any facility that an operator already named.
            if facility.display_name == "Main Facility" and facility.code == "MAIN":
                facility.display_name = facility_name
                facility.code = "MEDNET"
            if not facility.is_active:
                facility.is_active = True
            db.commit()
            print(f"Facility already present: {facility.display_name} (id={facility.id})")
        else:
            facility = Facility(
                facility_guid=str(uuid4()),
                regn_number=1,
                display_name=facility_name,
                contact_number="0000000000",
                code="MEDNET",
                is_active=True,
            )
            db.add(facility)
            db.commit()
            db.refresh(facility)
            print(f"Created facility: {facility.display_name} (id={facility.id})")

        account = auth_service.get_account_by_username(db, username)
        if account is not None:
            if account.role != Role.SUPER_ADMIN.value:
                print(
                    f"Cannot seed: account {username!r} exists with role {account.role!r}.",
                    file=sys.stderr,
                )
                return 1
            print(f"Superadmin already present: {account.username} (id={account.id})")
        else:
            user = (
                db.query(User)
                .filter(User.note == "bootstrap-superadmin")
                .first()
            )
            if user is None:
                user = User(
                    name=superadmin_name,
                    user_type=UserType.EMPLOYEE.value,
                    role="System Administrator",
                    note="bootstrap-superadmin",
                    is_active=True,
                    current_status="OUT",
                )
                db.add(user)
                db.commit()
                db.refresh(user)
            account = auth_service.create_account(
                db,
                user_id=user.id,
                username=username,
                password=password,
                role=Role.SUPER_ADMIN,
            )
            print(
                f"Created superadmin access for {superadmin_name}: "
                f"{account.username} (user_id={account.id})"
            )
    finally:
        db.close()

    # Populate legacy person-to-facility mappings now that the facility exists.
    init_db()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
