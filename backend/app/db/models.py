"""
models.py — SQLAlchemy ORM models for the relational store.

Phase 1 of the user-model expansion (see
``docs/CHANGELOG.md`` — Phases 1–8):

* The historical ``Patient`` class is renamed to :class:`User`. A
  module-level alias ``Patient = User`` is retained so older callers
  keep working unchanged.
* ``User`` carries a ``user_type`` column (``PATIENT`` / ``DOCTOR`` /
  ``EMPLOYEE`` / ``VISITOR`` / ``RELATIVE``) plus type-specific
  optional columns (``role``, ``specialty``, OPD assignments, …).
* A new :class:`UserRelation` table records user-to-user relationships
  — primarily relative-of-patient, but the schema is general.
* The underlying SQL table ``patients`` is renamed to ``users`` at
  boot by :func:`backend.app.db.postgres.init_db`. The doctor
  unification (``doctors`` table merged into ``users``) lands in a
  follow-up commit (Phase 1b).
"""

import enum
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from backend.app.db.postgres import Base
from backend.app.utils.time_ist import now_ist


class UserType(str, enum.Enum):
    """Allowed values for :attr:`User.user_type`.

    Stored as a plain ``VARCHAR(20)`` in PostgreSQL rather than a
    native ``ENUM`` type — keeps idempotent on-boot migrations simple
    and lets the application enforce the closed set at the validation
    layer.
    """

    PATIENT = "PATIENT"
    DOCTOR = "DOCTOR"
    EMPLOYEE = "EMPLOYEE"
    VISITOR = "VISITOR"
    RELATIVE = "RELATIVE"


class NationalIdType(str, enum.Enum):
    """Allowed values for :attr:`User.national_id_type`.

    ``UID`` is Aadhaar (the client HIS's term); ``ABHA`` is the 14-digit
    national health id pushed by ABDM. Stored as VARCHAR like
    :class:`UserType`.
    """

    UID = "UID"  # Aadhaar
    ABHA = "ABHA"
    PAN = "PAN"
    PASSPORT = "PASSPORT"
    DL = "DL"
    VOTER_ID = "VOTER_ID"


class RelationType(str, enum.Enum):
    """Allowed values for :attr:`UserRelation.relation_type`."""

    SPOUSE = "SPOUSE"
    PARENT = "PARENT"
    CHILD = "CHILD"
    SIBLING = "SIBLING"
    GUARDIAN = "GUARDIAN"
    OTHER = "OTHER"


class User(Base):
    """A registered identity in the system.

    Every row carries a :attr:`user_type` that decides which
    type-specific fields apply. Patient-tracking columns
    (``current_status``, ``current_camera_id``, ``last_seen_at``) are
    shared across types so that staff/visitor presence can be tracked
    by the same pipeline as patients.

    The face embedding is **not** stored on this row — it lives in the
    FAISS index keyed by ``users.id``.
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    # ── Type discriminator ────────────────────────────────────────
    user_type = Column(
        String(20),
        nullable=False,
        default=UserType.PATIENT.value,
        index=True,
    )

    # ── Core identity (all types) ─────────────────────────────────
    # ``mrn`` is nullable because only patients carry one.
    mrn = Column(String(20), unique=True, index=True, nullable=True)
    name = Column(String(255), nullable=False, index=True)

    age = Column(Integer, nullable=True)
    gender = Column(String(10), nullable=True)
    dob = Column(Date, nullable=True)

    contact_number = Column(String(20), nullable=True)
    address = Column(String(500), nullable=True)

    # ── Registry expansion (B2B restructure, Phase 1) ─────────────
    # ``users`` doubles as the PERSON_REGISTRY: identity is universal,
    # while what a person *is* per facility lives in
    # ``person_visit_mapping`` (see ``facility_models``). ``name``
    # remains the canonical display string (FAISS overlays, search);
    # the split parts feed the client pre-registration payload.
    person_guid = Column(
        String(36),
        unique=True,
        nullable=True,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    prefix = Column(String(10), nullable=True)          # Mr. / Mrs. / Dr.
    first_name = Column(String(100), nullable=True)
    middle_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    whatsapp_number = Column(String(20), nullable=True)
    email = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    country = Column(String(100), nullable=True)
    pin_code = Column(String(10), nullable=True)
    national_id_type = Column(String(20), nullable=True)  # NationalIdType
    national_id = Column(String(50), nullable=True)
    next_of_kin_name = Column(String(255), nullable=True)
    next_of_kin_relation = Column(String(50), nullable=True)
    next_of_kin_contact = Column(String(20), nullable=True)
    photo_url = Column(String(500), nullable=True)

    # ── Legacy patient fields (kept for backward compatibility) ───
    # These predate the user-type split. Patient-specific intake
    # workflows still populate them; non-patient types ignore them.
    department = Column(String(100), nullable=True)
    doctor = Column(String(100), nullable=True)
    category = Column(String(100), nullable=True)

    # ── Type-specific fields ──────────────────────────────────────
    role = Column(String(64), nullable=True)              # EMPLOYEE: Nurse/Admin/Security/...
    specialty = Column(String(128), nullable=True)        # DOCTOR
    staff_department = Column(String(128), nullable=True)  # DOCTOR/EMPLOYEE free-text dept name
    opd_department_id = Column(
        Integer,
        ForeignKey("departments.id", ondelete="SET NULL"),
        nullable=True,
    )
    opd_room_id = Column(
        Integer,
        ForeignKey("rooms.id", ondelete="SET NULL"),
        nullable=True,
    )
    purpose = Column(String(256), nullable=True)          # VISITOR
    note = Column(Text, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)

    # ── Tracking / presence state (all types) ─────────────────────
    current_status = Column(String(20), default="OUT", index=True)
    current_floor = Column(String(50), nullable=True)
    current_camera_id = Column(String(50), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=now_ist)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)

    # ── Convenience predicates (read-only) ────────────────────────
    @property
    def is_patient(self) -> bool:
        return self.user_type == UserType.PATIENT.value

    @property
    def is_doctor(self) -> bool:
        return self.user_type == UserType.DOCTOR.value

    @property
    def is_employee(self) -> bool:
        return self.user_type == UserType.EMPLOYEE.value

    @property
    def is_visitor(self) -> bool:
        return self.user_type == UserType.VISITOR.value

    @property
    def is_relative(self) -> bool:
        return self.user_type == UserType.RELATIVE.value

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<User id={self.id} type={self.user_type!r} "
            f"name={self.name!r}>"
        )


# ─────────────────────────────────────────────────────────────────────
# Backward-compatibility alias.
#
# Older modules import ``Patient`` from this file (face_service,
# patient_service, tracking, frontdesk, camera workers, etc.). The
# alias lets them keep working while the refactor proceeds in later
# phases. New code should import :class:`User` directly.
# ─────────────────────────────────────────────────────────────────────
Patient = User


class PatientSession(Base):
    """A single presence session — one row per (user, entry → exit) visit.

    Despite the historical class and table names, a session may now be
    opened for any user type. The dashboard and history pages filter
    by type at the service layer; the schema itself does not
    discriminate.
    """

    __tablename__ = "patient_sessions"

    id = Column(Integer, primary_key=True, index=True)
    # FK target is now ``users.id`` (table renamed from ``patients``).
    # Column name is left as ``patient_id`` for backward compatibility
    # with every service that already queries by it; a rename can land
    # in a later phase if there is value in it.
    patient_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    status = Column(String(20), default="INSIDE")  # INSIDE | OUT

    current_floor = Column(String(50), nullable=True)
    current_camera = Column(String(50), nullable=True)

    entry_time = Column(DateTime(timezone=True), default=now_ist)
    exit_time = Column(DateTime(timezone=True), nullable=True)

    last_seen = Column(DateTime(timezone=True), default=now_ist)

    # ``patient`` attribute kept for caller compatibility; resolves to
    # a :class:`User` row.
    patient = relationship("User")

    __table_args__ = (
        Index("idx_active_sessions", "patient_id", "status"),
        Index(
            "uq_active_session_per_patient",
            "patient_id",
            unique=True,
            postgresql_where=(status == "INSIDE"),
        ),
    )


class UserRelation(Base):
    """A directed user-to-user relationship.

    Primary use case: ``RELATIVE → PATIENT`` (a relative linked to one
    or more patients). The schema is symmetric-safe — a reverse view
    (``patient → relatives``) is obtained via a SQL join, so we do not
    store the reciprocal row.

    ``relation_type`` is one of :class:`RelationType`; stored as a
    plain string for the same migration-simplicity reasons as
    :attr:`User.user_type`.
    """

    __tablename__ = "user_relations"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    related_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    relation_type = Column(String(20), nullable=False)
    note = Column(String(128), nullable=True)

    created_at = Column(DateTime(timezone=True), default=now_ist)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)

    user = relationship("User", foreign_keys=[user_id])
    related_user = relationship("User", foreign_keys=[related_user_id])

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "related_user_id",
            "relation_type",
            name="uq_user_relation",
        ),
        CheckConstraint(
            "user_id <> related_user_id",
            name="ck_user_relation_distinct",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<UserRelation id={self.id} "
            f"user_id={self.user_id} related_user_id={self.related_user_id} "
            f"type={self.relation_type!r}>"
        )


# ─────────────────────────────────────────────────────────────────────
# Side-effect import: ensure the OPD tables (``departments``, ``rooms``,
# ``doctors``, …) are registered on the same ``Base.metadata`` whenever
# this module is loaded.
#
# ``User`` declares foreign keys to ``rooms.id`` and ``departments.id``;
# if a caller (e.g. a camera-worker subprocess) imports ``models`` in
# isolation, SQLAlchemy cannot resolve those FK targets at flush time
# and raises ``NoReferencedTableError``. Importing ``frontdesk_models``
# below guarantees the OPD tables are visible on the shared metadata
# regardless of the entry point.
# ─────────────────────────────────────────────────────────────────────
from backend.app.db import frontdesk_models  # noqa: E402, F401
from backend.app.db import facility_models  # noqa: E402, F401
