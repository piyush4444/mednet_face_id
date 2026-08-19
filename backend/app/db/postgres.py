"""
postgres.py — SQLAlchemy engine, session factory, and declarative base.

This module is the single entry point for the relational store. Every
piece of the backend that needs to talk to PostgreSQL goes through the
objects defined here:

    from app.db.postgres import Base, SessionLocal, engine, get_db, init_db

Design notes
------------
* A single ``engine`` is created at import time and reused for the
  lifetime of the process. SQLAlchemy manages an internal connection
  pool, so there is no need to build a new engine per request.
* ``pool_pre_ping=True`` emits a lightweight ``SELECT 1`` before handing
  out a pooled connection. This transparently recovers from stale
  connections (database restarts, idle timeouts, load-balancer cuts)
  without the caller ever seeing a ``OperationalError``.
* ``SessionLocal`` is a configured ``sessionmaker``. Call it to obtain
  a fresh ``Session`` bound to the engine.
* ``get_db`` is a FastAPI dependency: it yields a session and guarantees
  the session is closed even when the request handler raises.
* ``Base`` is the declarative base shared by every ORM model. Importing
  models anywhere before ``init_db()`` runs ensures their tables are
  registered on ``Base.metadata``.
"""

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from backend.app.core.config import settings


# ── Engine ───────────────────────────────────────────────────────────────
# ``pool_pre_ping`` protects against stale connections in the pool.
# ``future=True`` opts in to SQLAlchemy 2.0 style semantics.
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    future=True,
)


# ── Session factory ──────────────────────────────────────────────────────
# ``autocommit=False`` and ``autoflush=False`` give the caller explicit
# control over transaction boundaries, which is what we want for request
# handlers and background jobs.
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    future=True,
)


# ── Declarative base ─────────────────────────────────────────────────────
# All ORM models inherit from this. ``init_db()`` creates every table
# registered on ``Base.metadata``.
Base = declarative_base()


# ── FastAPI dependency ───────────────────────────────────────────────────
def get_db() -> Generator[Session, None, None]:
    """
    Yield a database session scoped to a single request.

    Usage inside a FastAPI route::

        from fastapi import Depends
        from sqlalchemy.orm import Session
        from app.db.postgres import get_db

        @router.get("/patients")
        def list_patients(db: Session = Depends(get_db)):
            return db.query(Patient).all()

    The session is always closed, even if the handler raises.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Schema bootstrap ─────────────────────────────────────────────────────
def init_db() -> None:
    """
    Create every table registered on ``Base.metadata`` and apply
    lightweight on-boot schema migrations.

    Import side effect: all modules that define ORM models must be
    imported before this function runs, otherwise their tables will
    not be registered on the metadata. ``app.db.models`` and
    ``app.db.frontdesk_models`` are imported here to guarantee the
    ``users`` and OPD tables are always picked up.

    Safe to call multiple times — ``create_all`` is idempotent and
    will not touch tables that already exist. The lightweight-
    migration block below handles the schema deltas that
    ``create_all`` cannot apply (column additions on existing tables,
    table renames, nullability changes). For full migration tooling,
    Alembic would be appropriate; this helper is what the codebase
    relies on today.
    """
    from sqlalchemy import inspect, text

    # ── Pre-create_all migrations ────────────────────────────────
    # These run *before* ``Base.metadata.create_all`` so the freshly
    # registered ORM doesn't try to recreate a table whose physical
    # name has just changed.
    inspector = inspect(engine)

    # The pre-release RBAC schema used separate user_profile/user_auth rows
    # and facility-scoped mappings. A reset database may still contain those
    # empty table definitions because create_all never alters existing tables.
    # Replace them only when every legacy identity table is empty; deployments
    # with data require an explicit data migration and are left untouched.
    legacy_tables = [
        name
        for name in (
            "user_permission_mapping",
            "user_role_mapping",
            "user_auth",
            "user_profile",
            "staff_accounts",
        )
        if inspector.has_table(name)
    ]
    old_role_columns = (
        {column["name"] for column in inspector.get_columns("user_role_mapping")}
        if inspector.has_table("user_role_mapping")
        else set()
    )
    if "user_auth_id" in old_role_columns and legacy_tables:
        with engine.connect() as conn:
            legacy_empty = all(
                conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() == 0
                for table in legacy_tables
            )
        if legacy_empty:
            with engine.begin() as conn:
                if inspector.has_table("kiosk_devices"):
                    for foreign_key in inspector.get_foreign_keys("kiosk_devices"):
                        if foreign_key.get("referred_table") == "staff_accounts":
                            constraint = foreign_key["name"].replace('"', '""')
                            conn.execute(text(
                                f'ALTER TABLE kiosk_devices '
                                f'DROP CONSTRAINT "{constraint}"'
                            ))
                for table in legacy_tables:
                    conn.execute(text(f'DROP TABLE "{table}"'))
            inspector = inspect(engine)
        else:
            raise RuntimeError(
                "Legacy RBAC tables contain data. Migrate them before starting "
                "the canonical-user RBAC schema."
            )

    # User-model expansion, Phase 1: rename ``patients`` → ``users``.
    # PostgreSQL preserves all FK constraints that targeted the
    # renamed table by OID, so foreign keys from
    # ``patient_sessions.patient_id`` and ``opd_visits.patient_id``
    # follow the rename without intervention.
    if inspector.has_table("patients") and not inspector.has_table("users"):
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE patients RENAME TO users"))

    # Import models for their side effect: registering tables on Base.
    from backend.app.db import models  # noqa: F401
    from backend.app.db import frontdesk_models  # noqa: F401
    from backend.app.db import auth_models  # noqa: F401  (credentials/service accounts)
    from backend.app.db import audit_models  # noqa: F401  (audit_log)
    # Global role/permission catalog and canonical-user mappings.
    from backend.app.db import identity_models  # noqa: F401

    Base.metadata.create_all(bind=engine)

    # ── Post-create_all column/index patches ─────────────────────
    inspector = inspect(engine)  # refresh after create_all

    # User-model expansion, Phase 1: patch columns on ``users``.
    # ``create_all`` does not alter existing tables, so every new
    # column introduced by the rename is added here when missing.
    if inspector.has_table("users"):
        columns = {col["name"] for col in inspector.get_columns("users")}
        with engine.begin() as conn:
            if "user_type" not in columns:
                conn.execute(text(
                    "ALTER TABLE users ADD COLUMN user_type VARCHAR(20) "
                    "NOT NULL DEFAULT 'PATIENT'"
                ))
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_users_type "
                    "ON users (user_type)"
                ))
            if "role" not in columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(64)"))
            if "specialty" not in columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN specialty VARCHAR(128)"))
            if "staff_department" not in columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN staff_department VARCHAR(128)"))
            if "opd_department_id" not in columns:
                conn.execute(text(
                    "ALTER TABLE users ADD COLUMN opd_department_id INTEGER "
                    "REFERENCES departments(id) ON DELETE SET NULL"
                ))
            if "opd_room_id" not in columns:
                conn.execute(text(
                    "ALTER TABLE users ADD COLUMN opd_room_id INTEGER "
                    "REFERENCES rooms(id) ON DELETE SET NULL"
                ))
            if "purpose" not in columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN purpose VARCHAR(256)"))
            if "note" not in columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN note TEXT"))
            if "is_active" not in columns:
                conn.execute(text(
                    "ALTER TABLE users ADD COLUMN is_active BOOLEAN "
                    "NOT NULL DEFAULT TRUE"
                ))

            # ``mrn`` was NOT NULL on the old patients table; relax it
            # so non-patient user types can be created. Idempotent
            # because PostgreSQL accepts DROP NOT NULL on an already-
            # nullable column.
            conn.execute(text("ALTER TABLE users ALTER COLUMN mrn DROP NOT NULL"))

            # Indexes — the old ``idx_patient_*`` indexes follow the
            # rename automatically (PG indexes are bound to the table
            # by OID, not by name). Add the new user-type index and
            # ensure the old indexes still exist on the renamed table.
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_user_mrn ON users (mrn)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_user_name ON users (name)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_user_status "
                "ON users (current_status)"
            ))

    # User-model expansion, Phase 1b: migrate ``doctors`` table into
    # ``users`` with ``user_type='DOCTOR'``, retarget
    # ``opd_visits.doctor_id`` to ``users.id``, and drop the legacy
    # ``doctors`` table. Runs only when the legacy table is still
    # present, so the block is idempotent across restarts.
    if inspector.has_table("doctors"):
        with engine.begin() as conn:
            # Copy each doctor row into ``users`` and remember the
            # old-id → new-id mapping so we can retarget opd_visits.
            old_rows = conn.execute(text(
                "SELECT id, name, department_id, room_id, specialty, "
                "is_active, created_at, updated_at FROM doctors"
            )).mappings().all()

            old_to_new: dict[int, int] = {}
            for r in old_rows:
                new_id = conn.execute(
                    text(
                        "INSERT INTO users ("
                        "user_type, name, opd_department_id, opd_room_id, "
                        "specialty, is_active, current_status, "
                        "created_at, updated_at"
                        ") VALUES ("
                        "'DOCTOR', :name, :dept, :room, :specialty, "
                        ":active, 'OUT', :ca, :ua"
                        ") RETURNING id"
                    ),
                    {
                        "name": r["name"],
                        "dept": r["department_id"],
                        "room": r["room_id"],
                        "specialty": r["specialty"],
                        "active": r["is_active"],
                        "ca": r["created_at"],
                        "ua": r["updated_at"],
                    },
                ).scalar()
                old_to_new[r["id"]] = new_id

            # Drop the existing FK that points at doctors(id) BEFORE
            # retargeting the column — otherwise the UPDATE below
            # checks the new id against the old doctors table and
            # fails. The FK name is discovered from information_schema
            # rather than guessed.
            fk_rows = conn.execute(text(
                "SELECT tc.constraint_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON tc.constraint_name = kcu.constraint_name "
                "WHERE tc.table_name = 'opd_visits' "
                "  AND tc.constraint_type = 'FOREIGN KEY' "
                "  AND kcu.column_name = 'doctor_id'"
            )).fetchall()
            for fk in fk_rows:
                conn.execute(text(
                    f'ALTER TABLE opd_visits '
                    f'DROP CONSTRAINT "{fk[0]}"'
                ))

            # Retarget every opd_visits row whose doctor_id still
            # points at the legacy table.
            for old_id, new_id in old_to_new.items():
                conn.execute(
                    text(
                        "UPDATE opd_visits SET doctor_id = :new "
                        "WHERE doctor_id = :old"
                    ),
                    {"new": new_id, "old": old_id},
                )

            # Add the replacement FK that points at users(id).
            conn.execute(text(
                "ALTER TABLE opd_visits "
                "ADD CONSTRAINT opd_visits_doctor_id_fkey "
                "FOREIGN KEY (doctor_id) REFERENCES users(id) "
                "ON DELETE SET NULL"
            ))

            # The legacy table is no longer referenced by any FK
            # and can be removed.
            conn.execute(text("DROP TABLE doctors"))

    if inspector.has_table("patient_sessions"):
        columns = {col["name"] for col in inspector.get_columns("patient_sessions")}
        with engine.begin() as conn:
            if "current_floor" not in columns:
                conn.execute(text(
                    "ALTER TABLE patient_sessions "
                    "ADD COLUMN current_floor VARCHAR(50)"
                ))
            if "current_camera" not in columns:
                conn.execute(text(
                    "ALTER TABLE patient_sessions "
                    "ADD COLUMN current_camera VARCHAR(50)"
                ))
            if "last_seen" not in columns and "last_seen_at" in columns:
                conn.execute(text(
                    "ALTER TABLE patient_sessions "
                    "RENAME COLUMN last_seen_at TO last_seen"
                ))
            elif "last_seen" not in columns:
                conn.execute(text(
                    "ALTER TABLE patient_sessions "
                    "ADD COLUMN last_seen TIMESTAMP WITH TIME ZONE"
                ))

            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_active_sessions "
                "ON patient_sessions (patient_id, status)"
            ))
            # Drop the stale ACTIVE-based unique index, rebuild for INSIDE.
            conn.execute(text(
                "DROP INDEX IF EXISTS uq_active_session_per_patient"
            ))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_active_session_per_patient "
                "ON patient_sessions (patient_id) WHERE status = 'INSIDE'"
            ))

    # ── Canonical person registry + singleton facility membership ──
    # ``users`` is the one human identity table. The new tables are created
    # by ``create_all`` above; this block patches registry columns and, after
    # explicit bootstrap, creates one operational membership per user.
    if inspector.has_table("users"):
        columns = {col["name"] for col in inspector.get_columns("users")}
        _REGISTRY_COLUMNS = {
            "person_guid": "VARCHAR(36)",
            "prefix": "VARCHAR(10)",
            "first_name": "VARCHAR(100)",
            "middle_name": "VARCHAR(100)",
            "last_name": "VARCHAR(100)",
            "whatsapp_number": "VARCHAR(20)",
            "email": "VARCHAR(255)",
            "city": "VARCHAR(100)",
            "state": "VARCHAR(100)",
            "country": "VARCHAR(100)",
            "pin_code": "VARCHAR(10)",
            "national_id_type": "VARCHAR(20)",
            "national_id": "VARCHAR(50)",
            "next_of_kin_name": "VARCHAR(255)",
            "next_of_kin_relation": "VARCHAR(50)",
            "next_of_kin_contact": "VARCHAR(20)",
            "photo_url": "VARCHAR(500)",
        }
        with engine.begin() as conn:
            for col, ddl_type in _REGISTRY_COLUMNS.items():
                if col not in columns:
                    conn.execute(text(
                        f"ALTER TABLE users ADD COLUMN {col} {ddl_type}"
                    ))

            # Backfill a stable GUID per person (future cross-site sync
            # key; ``gen_random_uuid`` is built-in since PostgreSQL 13).
            conn.execute(text(
                "UPDATE users SET person_guid = gen_random_uuid()::text "
                "WHERE person_guid IS NULL"
            ))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_person_guid "
                "ON users (person_guid)"
            ))

            # Best-effort split of the legacy single ``name`` into
            # first/middle/last. Only touches rows never split before,
            # so operator corrections are not overwritten on restart.
            conn.execute(text(
                "UPDATE users SET "
                "first_name = split_part(btrim(name), ' ', 1), "
                "last_name = CASE "
                "  WHEN array_length(string_to_array(btrim(name), ' '), 1) > 1 "
                "  THEN (string_to_array(btrim(name), ' '))"
                "       [array_length(string_to_array(btrim(name), ' '), 1)] "
                "END, "
                "middle_name = CASE "
                "  WHEN array_length(string_to_array(btrim(name), ' '), 1) > 2 "
                "  THEN array_to_string((string_to_array(btrim(name), ' '))"
                "       [2:array_length(string_to_array(btrim(name), ' '), 1) - 1], ' ') "
                "END "
                "WHERE first_name IS NULL AND name IS NOT NULL"
            ))

    # Backfill one mapping per existing user when the deployment has been
    # bootstrapped. Facility creation belongs to ``scripts.seed_initial`` so
    # startup never silently invents configuration or credentials.
    if inspector.has_table("facility_master"):
        with engine.begin() as conn:
            facility_id = conn.execute(text(
                "SELECT id FROM facility_master ORDER BY id LIMIT 1"
            )).scalar()
            # Legacy backfill: one person_facility row per existing user.
            # No-op until the initial seed has created the singleton facility.
            if facility_id is not None:
                conn.execute(
                    text(
                        "INSERT INTO person_facility ("
                        "person_id, facility_id, person_type, mrn, "
                        "visit_count, last_visit_at, is_active, created_at"
                        ") "
                        "SELECT u.id, :fid, u.user_type, u.mrn, "
                        "COALESCE((SELECT COUNT(*) FROM patient_sessions ps "
                        "          WHERE ps.patient_id = u.id), 0), "
                        "(SELECT MAX(ps.entry_time) FROM patient_sessions ps "
                        " WHERE ps.patient_id = u.id), "
                        "u.is_active, NOW() "
                        "FROM users u "
                        "WHERE NOT EXISTS ("
                        "  SELECT 1 FROM person_facility m "
                        "  WHERE m.person_id = u.id AND m.facility_id = :fid"
                        ")"
                    ),
                    {"fid": facility_id},
                )

    # Camera roster DB cutover. Fresh databases receive these columns from
    # the ORM model; this additive patch keeps an earlier, unused
    # camera_master table compatible without requiring a destructive reset.
    if inspector.has_table("camera_master"):
        camera_columns = {
            col["name"] for col in inspector.get_columns("camera_master")
        }
        with engine.begin() as conn:
            if "source_type" not in camera_columns:
                conn.execute(text(
                    "ALTER TABLE camera_master ADD COLUMN source_type "
                    "VARCHAR(10) NOT NULL DEFAULT 'rtsp'"
                ))
            if "floor" not in camera_columns:
                conn.execute(text(
                    "ALTER TABLE camera_master ADD COLUMN floor "
                    "VARCHAR(40) NOT NULL DEFAULT 'unknown'"
                ))
            if "role" not in camera_columns:
                conn.execute(text(
                    "ALTER TABLE camera_master ADD COLUMN role "
                    "VARCHAR(10) NOT NULL DEFAULT 'inside'"
                ))

    # B2B restructure, Phase 3: retry bookkeeping on pre_registration_log.
    # New columns on a table that P1 may already have created without them.
    if inspector.has_table("pre_registration_log"):
        cols = {c["name"] for c in inspector.get_columns("pre_registration_log")}
        with engine.begin() as conn:
            if "attempts" not in cols:
                conn.execute(text(
                    "ALTER TABLE pre_registration_log "
                    "ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
                ))
            if "next_retry_at" not in cols:
                conn.execute(text(
                    "ALTER TABLE pre_registration_log "
                    "ADD COLUMN next_retry_at TIMESTAMP WITH TIME ZONE"
                ))

    # B2B restructure r3.1: seed the DB-backed RBAC catalog
    # (role_master / permission_master / role_permission_mapping) from the
    # in-code Role/Permission vocabulary. Idempotent and non-destructive —
    # see rbac_service.sync_catalog. Runs after create_all so the tables
    # exist; uses its own session.
    if inspector.has_table("role_master"):
        from backend.app.services import rbac_service

        _seed_db = SessionLocal()
        try:
            rbac_service.sync_catalog(_seed_db)
        finally:
            _seed_db.close()
