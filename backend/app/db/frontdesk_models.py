"""
frontdesk_models.py — ORM models for the front-desk OPD workflow.

Tables
------
- departments      : configurable list of OPD departments (e.g. Cardiology).
- doctors          : doctors belonging to a department.
- rooms            : physical rooms; a doctor or department may be mapped to one.
- opd_visits       : one row per generated token. The canonical log of
                     "which patient went to which department/doctor/room".
- token_counters   : (date, department) -> last issued number, used for
                     atomic per-department daily token allocation.

Design notes
------------
- Departments/doctors/rooms are DB-backed (admin UI manages them).
- A visit is permanent (never deleted); status transitions in-place.
- token_number is the printed/called number (e.g. "GEN-014") and resets
  daily per department. The DB row's own ``id`` is the permanent reference.
"""

from sqlalchemy import (
    Boolean,
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


class Department(Base):
    """An OPD department (General, Cardiology, Ortho, ...)."""

    __tablename__ = "departments"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(20), unique=True, nullable=False, index=True)  # e.g. "CARD"
    name = Column(String(100), nullable=False)                          # e.g. "Cardiology"
    token_prefix = Column(String(10), nullable=False)                   # e.g. "CARD"
    default_room_id = Column(
        Integer, ForeignKey("rooms.id", ondelete="SET NULL"), nullable=True
    )
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), default=now_ist)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)


class Room(Base):
    """A physical consultation room."""

    __tablename__ = "rooms"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, nullable=False)              # e.g. "Room 12"
    floor = Column(String(50), nullable=True)
    description = Column(String(200), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), default=now_ist)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)


# NOTE: The standalone ``Doctor`` ORM class has been retired as part of
# the user-model expansion (Phase 1b, see
# ``docs/CHANGELOG.md`` — Phases 1–8). Doctors now live in the
# ``users`` table with ``user_type='DOCTOR'``; their OPD assignments
# are carried in ``users.opd_department_id`` and ``users.opd_room_id``.
#
# Service code that needs a doctor queries:
#   from backend.app.db.models import User, UserType
#   db.query(User).filter(User.user_type == UserType.DOCTOR.value)
#
# The physical ``doctors`` table is migrated into ``users`` and dropped
# on the next backend restart by :func:`backend.app.db.postgres.init_db`.


class OPDVisit(Base):
    """
    One generated OPD token. Permanent audit row for the front-desk
    workflow — answers "which patient went where, when, and why".
    """

    __tablename__ = "opd_visits"

    id = Column(Integer, primary_key=True, index=True)
    # FK target is ``users.id`` (the table previously named ``patients``
    # was renamed by the user-model expansion — see
    # ``docs/CHANGELOG.md`` — Phases 1–8). The column name is kept
    # as ``patient_id`` for backward compatibility with the existing
    # service / API layer; OPD-token issuance is restricted to
    # ``user_type=PATIENT`` rows in the service layer.
    patient_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    token_number = Column(String(30), nullable=False, index=True)   # e.g. "CARD-014"
    visit_type = Column(String(20), nullable=False)                  # GENERAL|SPECIALIST|DOCTOR

    department_id = Column(
        Integer, ForeignKey("departments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Doctor reference now targets ``users.id`` (with
    # ``user_type='DOCTOR'``) — the legacy ``doctors`` table was
    # merged into ``users`` in Phase 1b. Column name preserved for
    # API / payload compatibility.
    doctor_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    room_id = Column(
        Integer, ForeignKey("rooms.id", ondelete="SET NULL"), nullable=True
    )

    # Snapshots of names at issue-time, so the slip and history stay
    # readable even if a doctor/room/department is later renamed or removed.
    department_name = Column(String(100), nullable=True)
    doctor_name = Column(String(100), nullable=True)
    room_name = Column(String(50), nullable=True)

    note = Column(Text, nullable=True)

    status = Column(String(20), default="WAITING", nullable=False, index=True)
    # WAITING | IN_CONSULT | DONE | CANCELLED

    source = Column(String(20), default="FACE_SCAN", nullable=False)
    # FACE_SCAN | MANUAL

    created_by = Column(String(100), nullable=True)  # operator/terminal id

    created_at = Column(DateTime(timezone=True), default=now_ist, index=True)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)
    printed_at = Column(DateTime(timezone=True), nullable=True)

    # ``patient`` and ``doctor`` both resolve to :class:`User` rows;
    # type discrimination is the service layer's responsibility.
    patient = relationship("User", foreign_keys=[patient_id])
    department = relationship("Department")
    doctor = relationship("User", foreign_keys=[doctor_id])
    room = relationship("Room")

    __table_args__ = (
        Index("idx_opd_visits_patient_created", "patient_id", "created_at"),
        Index("idx_opd_visits_dept_created", "department_id", "created_at"),
        Index("idx_opd_visits_status", "status"),
    )


class VisitStatusHistory(Base):
    """
    Append-only log of every status transition for an OPDVisit.

    One row is written when the visit is first created (from_status=NULL
    -> WAITING) and one row per subsequent ``update_visit_status`` call.
    Lets us compute wait-time / consult-time SLAs and audit any "who
    moved this patient and when" question.
    """

    __tablename__ = "visit_status_history"

    id = Column(Integer, primary_key=True, index=True)
    visit_id = Column(
        Integer, ForeignKey("opd_visits.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_status = Column(String(20), nullable=True)  # NULL on the creation row
    to_status = Column(String(20), nullable=False)
    changed_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)
    changed_by = Column(String(100), nullable=True)

    __table_args__ = (
        Index("idx_visit_status_history_visit", "visit_id", "changed_at"),
    )


class TokenCounter(Base):
    """
    Per-(date, department) running counter for token allocation.

    The allocator does ``SELECT ... FOR UPDATE`` on the row for the
    current (date, department), increments ``last_number``, and returns
    it. The unique constraint guarantees a single counter row per pair;
    inserts under contention are guarded with an ``ON CONFLICT DO NOTHING``
    pattern in the service layer.
    """

    __tablename__ = "token_counters"

    id = Column(Integer, primary_key=True, index=True)
    counter_date = Column(Date, nullable=False, index=True)
    department_id = Column(
        Integer, ForeignKey("departments.id", ondelete="CASCADE"), nullable=False
    )
    last_number = Column(Integer, default=0, nullable=False)

    __table_args__ = (
        UniqueConstraint("counter_date", "department_id", name="uq_token_counter_date_dept"),
    )
