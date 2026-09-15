"""
facility_models.py — singleton-facility operations and integration models.

Phase 1 of the B2B-partner restructure (see ``docs/CHANGELOG.md``):
The facility foreign keys remain internal relational anchors for locations,
cameras, visits, and client-HIS payloads. The application supports
exactly one active facility and never exposes tenant switching.

Tables
------
- facility_master       : hospitals / sites managed by this deployment.
                          Carries the client-HIS identifiers
                          (``client_facility_guid``, ``client_company_id``)
                          used in outbound integration payloads.
- location_master       : named places inside a facility (floor, corridor,
                          room, gate). Hierarchical via ``parent_location_id``
                          (FHIR-Location style: floor ⊃ corridor ⊃ room).
- person_facility       : one operational membership per person in the
                          singleton facility. Carries the client-facing person
                          type, MRN, and running ``visit_count``.
- person_tracking_logs  : append-only event log — every punch (IN/OUT from
                          gate cameras) and every zone sighting
                          (TRACKER_IN/TRACKER_OUT from internal cameras).
                          Presence state stays derived, never authoritative.
- punch_export_queue    : outbound punch deliveries to the client attendance
                          API (employees/doctors only). ``biometric_idx`` is
                          the client's unique transaction id, so retries are
                          idempotent on their side.
- pre_registration_log  : one row per pre-registration pushed to the client
                          HIS; stores their ``preRegnId`` / ``tokenNo`` for
                          operational display and audit.

Design notes
------------
- Person identity stays in ``users`` (``app.db.models``) — that table *is*
  the PERSON_REGISTRY; person ids stay integers because the client punch
  contract requires a numeric ``biometricUID``.
- Enum-like columns are plain VARCHARs validated at the service layer,
  matching :class:`backend.app.db.models.UserType`.
"""

import enum

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from backend.app.db.postgres import Base
from backend.app.utils.time_ist import now_ist


class VisitorSubtype(str, enum.Enum):
    """Allowed values for :attr:`PersonVisitMapping.visitor_subtype`."""

    VENDOR = "VENDOR"
    MR = "MR"  # medical representative
    ATTENDANT = "ATTENDANT"
    GUEST = "GUEST"


class VisitType(str, enum.Enum):
    """Allowed values for :attr:`PersonTrackingLog.visit_type`.

    IN / OUT are official punches from gate cameras and drive
    attendance. TRACKER_IN / TRACKER_OUT are zone-boundary sightings
    from internal cameras — movement history only, never exported.
    """

    IN = "IN"
    OUT = "OUT"
    TRACKER_IN = "TRACKER_IN"
    TRACKER_OUT = "TRACKER_OUT"


class LocationType(str, enum.Enum):
    """Allowed values for :attr:`Location.location_type`.

    ``DEPARTMENT`` absorbs the retired OPD ``departments`` table, and
    ``ROOM`` the retired ``rooms`` table — location_master is now the single
    hierarchy for every named place inside a facility (see
    ``docs/SCHEMA_REVIEW_R3.1.md``).
    """

    FLOOR = "FLOOR"
    CORRIDOR = "CORRIDOR"
    ROOM = "ROOM"
    GATE = "GATE"
    WARD = "WARD"
    DEPARTMENT = "DEPARTMENT"
    OTHER = "OTHER"


class CameraType(str, enum.Enum):
    """Allowed values for :attr:`Camera.camera_type`.

    IN / OUT cameras sit at punch gates (staff attendance); TRACKER_IN /
    TRACKER_OUT are internal zone-boundary sighting cameras (non-staff
    presence). The value drives which ``visit_type`` a camera's events
    produce.
    """

    IN = "IN"
    OUT = "OUT"
    TRACKER_IN = "TRACKER_IN"
    TRACKER_OUT = "TRACKER_OUT"


class TrackingSource(str, enum.Enum):
    """Allowed values for :attr:`PersonTrackingLog.source`."""

    CAMERA = "CAMERA"
    MANUAL = "MANUAL"


class ExportStatus(str, enum.Enum):
    """Delivery lifecycle shared by the outbound integration tables.

    ``SENDING`` is a transient claim marker: the worker flips a row to
    SENDING before the HTTP call so no other
    worker/thread can pick the same row, then to SENT or FAILED. A row
    stuck in SENDING (process crashed mid-send) is reclaimed to PENDING
    after ``EXPORT_STALE_SECONDS``.
    """

    PENDING = "PENDING"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class Facility(Base):
    """A hospital / site served by this deployment (FACILITY_MASTER)."""

    __tablename__ = "facility_master"

    id = Column(Integer, primary_key=True, index=True)

    # ── Client facility record (columns mirror the partner HIS sheet) ──
    facility_guid = Column(String(64), unique=True, nullable=False, index=True)  # FACILITY_GUID
    regn_number = Column(Integer, nullable=False)                  # REGN_NUMBER
    display_name = Column(String(255), nullable=False)            # DISPLAY_NAME
    contact_number = Column(String(15), nullable=False)           # CONTACT_NUMBER
    primary_contact_person = Column(String(15), nullable=True)    # PRIMARY_CONTACT_PERSON

    address = Column(String(500), nullable=True)                  # ADDRESS
    street = Column(String(500), nullable=True)                   # STREET
    city = Column(String(100), nullable=True)                     # CITY
    state = Column(String(100), nullable=True)                    # STATE
    pin_code = Column(String(10), nullable=True)                  # PINCODE

    # ── Kept beyond the client sheet (load-bearing) ───────────────
    # ``code`` is our short internal handle — the unique lookup / seed key
    # used across the service layer. ``client_company_id`` is the partner
    # companyID required in the punch and pre-registration payloads.
    # Removing either breaks lookups / outbound integration, so they stay.
    code = Column(String(20), unique=True, nullable=False, index=True)
    client_company_id = Column(Integer, nullable=True)
    integration_config = Column(JSONB, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), default=now_ist)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)


class Location(Base):
    """A named place inside a facility (LOCATION_MASTER).

    Hierarchical: ``parent_location_id`` chains floor ⊃ corridor ⊃ room.
    Cameras and tracking logs reference rows here; the OPD ``rooms``
    table is untouched for now (OPD flow is shelved).
    """

    __tablename__ = "location_master"

    id = Column(Integer, primary_key=True, index=True)
    facility_id = Column(
        Integer,
        ForeignKey("facility_master.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    parent_location_id = Column(
        Integer,
        ForeignKey("location_master.id", ondelete="SET NULL"),
        nullable=True,
    )

    name = Column(String(100), nullable=False)  # e.g. "Floor 2", "Main Gate"
    location_type = Column(String(20), nullable=False, default=LocationType.OTHER.value)
    description = Column(String(200), nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), default=now_ist)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)

    facility = relationship("Facility")
    parent = relationship("Location", remote_side=[id])


class Camera(Base):
    """A camera in the roster (CAMERA_SETUP).

    PostgreSQL is the camera-roster source of truth. ``code`` is the stable
    external id the recognition pipeline keys on (formerly ``camera_id`` in
    JSON), so workers and historical tracking references remain unchanged.
    ``camera_type`` decides which ``visit_type`` this camera's events produce.
    """

    __tablename__ = "camera_master"

    id = Column(Integer, primary_key=True, index=True)
    facility_id = Column(
        Integer,
        ForeignKey("facility_master.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    location_id = Column(
        Integer,
        ForeignKey("location_master.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Stable external id (was the cameras.json id) — single source of truth.
    code = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(120), nullable=False)
    # Capture configuration. These columns preserve the dictionary contract
    # used by camera workers while PostgreSQL becomes the roster authority.
    source_type = Column(String(10), nullable=False, default="rtsp")
    stream_url = Column(String(500), nullable=False)
    floor = Column(String(40), nullable=False, default="unknown")
    role = Column(String(10), nullable=False, default="inside")

    # B2B event classification remains separate from runtime entry/exit role.
    camera_type = Column(
        String(15), nullable=False, default=CameraType.TRACKER_IN.value
    )

    is_active = Column(Boolean, default=True, nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=now_ist)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)

    facility = relationship("Facility")
    location = relationship("Location")

    __table_args__ = (
        Index("idx_camera_facility_active", "facility_id", "is_active"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Camera id={self.id} code={self.code!r} type={self.camera_type!r}>"


class DataMigration(Base):
    """Durable markers for one-time data imports outside schema creation."""

    __tablename__ = "data_migrations"

    key = Column(String(100), primary_key=True)
    applied_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)


class PersonFacility(Base):
    """A person's operational record in the singleton facility.

    One row per person for this deployment. The database retains the
    ``facility_id`` relation as an integration and referential-integrity
    anchor. The *current* value of the versioned
    attributes (``person_type`` / ``visitor_subtype`` / ``mrn`` /
    ``is_active``) lives here for fast reads; their history is in
    :class:`PersonFacilityVersion`. ``current_version`` points at the
    version row in effect now — every mutation of a versioned attribute
    closes the current version and opens the next (SCD Type 2), so historic
    events stay truthful. See ``docs/SCHEMA_REVIEW_R3.1.md`` 2.2.

    Renamed from ``person_visit_mapping``; the alias below keeps old callers
    working.
    """

    __tablename__ = "person_facility"

    id = Column(Integer, primary_key=True, index=True)
    person_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    facility_id = Column(
        Integer,
        ForeignKey("facility_master.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Current client-facing values (history in person_facility_version).
    person_type = Column(String(20), nullable=False, index=True)
    visitor_subtype = Column(String(20), nullable=True)  # VisitorSubtype

    # Facility-issued patient number. Unique per facility (partial index).
    mrn = Column(String(20), nullable=True)

    # Points at the person_facility_version currently in effect.
    current_version = Column(
        Integer, nullable=False, default=1, server_default="1"
    )

    visit_count = Column(Integer, default=0, nullable=False)  # client's COUNT
    last_visit_at = Column(DateTime(timezone=True), nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), default=now_ist)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)

    person = relationship("User")
    facility = relationship("Facility")
    versions = relationship(
        "PersonFacilityVersion",
        back_populates="mapping",
        cascade="all, delete-orphan",
        order_by="PersonFacilityVersion.version_number",
    )

    # NOTE: index/constraint names are schema-global in PostgreSQL. These
    # deliberately differ from the legacy person_visit_mapping names
    # (uq_person_facility / uq_mapping_facility_mrn) so this table can be
    # created while the old one still exists — a clash aborts the whole
    # create_all, not just this table.
    __table_args__ = (
        UniqueConstraint("person_id", "facility_id", name="uq_pf_person_facility"),
        Index(
            "uq_pf_facility_mrn",
            "facility_id",
            "mrn",
            unique=True,
            postgresql_where=(mrn.isnot(None)),
        ),
    )


# Back-compat alias: services still import ``PersonVisitMapping``.
PersonVisitMapping = PersonFacility


class PersonFacilityVersion(Base):
    """SCD Type 2 history of a :class:`PersonFacility` membership.

    Each change to a versioned attribute writes a new row with the next
    ``version_number`` and a ``[valid_from, valid_to)`` interval; exactly one
    row per membership has ``is_current = True``. ``person_visit_log`` rows
    record the ``version_number`` in effect at event time, so history replays
    against the attributes that were true then.
    """

    __tablename__ = "person_facility_version"

    id = Column(Integer, primary_key=True, index=True)
    person_facility_id = Column(
        Integer,
        ForeignKey("person_facility.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version_number = Column(Integer, nullable=False)

    # Snapshot of the versioned attributes at this version.
    person_type = Column(String(20), nullable=False)
    visitor_subtype = Column(String(20), nullable=True)
    mrn = Column(String(20), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)

    valid_from = Column(DateTime(timezone=True), default=now_ist, nullable=False)
    valid_to = Column(DateTime(timezone=True), nullable=True)  # NULL = current
    is_current = Column(Boolean, default=True, nullable=False)

    change_reason = Column(String(255), nullable=True)
    # Snapshot of the acting principal id — NOT a hard FK (mirrors
    # audit_log.account_id), so a deleted login never blocks history.
    changed_by = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), default=now_ist)

    mapping = relationship("PersonFacility", back_populates="versions")

    __table_args__ = (
        UniqueConstraint(
            "person_facility_id", "version_number", name="uq_pf_version"
        ),
        Index("idx_pf_version_current", "person_facility_id", "is_current"),
    )


class PersonVisitLog(Base):
    """Append-only punch / sighting event (was ``person_tracking_logs``).

    Immutable — rows are inserted, never updated. Staff/employees/doctors get
    IN/OUT attendance punches; visitors/guests/patients get TRACKER_IN /
    TRACKER_OUT presence sightings. Presence and sessions are *derived* from
    this table, never stored.

    ``visit_number`` snapshots the membership's ``visit_count`` at event time
    (the client sheet's COUNT) and ``person_facility_version`` the membership
    version then in effect, so historic rows stay meaningful even as the
    counter and the membership move on.
    """

    __tablename__ = "person_visit_log"

    id = Column(Integer, primary_key=True, index=True)
    person_facility_id = Column(
        Integer,
        # CASCADE for now; hardened to RESTRICT with the soft-delete policy
        # in the Phase 7 pass (docs/SCHEMA_REVIEW_R3.1.md 2.4).
        ForeignKey("person_facility.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The membership version in effect at ``event_time``
    # (person_facility_version.version_number). A logical composite
    # reference, not a hard FK — the service stamps it on write so a
    # historic row always replays against the attributes true back then.
    person_facility_version = Column(Integer, nullable=False, default=1)

    visit_type = Column(String(15), nullable=False)  # VisitType
    # Purpose of visit, captured at entry for non-staff — this is what
    # replaced the retired ``user_relations`` table.
    purpose = Column(String(256), nullable=True)
    # Stable ``camera_master.code`` value. Kept nullable for manual entries;
    # a formal FK can be added after legacy history cleanup.
    camera_id = Column(String(50), nullable=True)
    location_id = Column(
        Integer,
        ForeignKey("location_master.id", ondelete="SET NULL"),
        nullable=True,
    )

    event_time = Column(DateTime(timezone=True), default=now_ist, nullable=False)
    visit_number = Column(Integer, nullable=True)
    source = Column(String(10), default=TrackingSource.CAMERA.value, nullable=False)

    created_at = Column(DateTime(timezone=True), default=now_ist)

    mapping = relationship("PersonFacility")
    location = relationship("Location")

    # Index names must not collide with the legacy person_tracking_logs
    # table's (schema-global in PostgreSQL).
    __table_args__ = (
        Index("idx_pvl_pf_time", "person_facility_id", "event_time"),
        Index("idx_pvl_type_time", "visit_type", "event_time"),
    )


class PunchExportSync(Base):
    """Outbound punch delivery to the client attendance API.

    A *mutable* work queue, deliberately kept out of the append-only
    ``person_visit_log`` so retry UPDATEs never contend with the event
    firehose (docs/SCHEMA_REVIEW_R3.1.md 2.1). One row per exportable punch
    (EMPLOYEE/DOCTOR memberships only — non-staff are tracked, never
    punched). The pusher sends one punch per request (client requirement)
    and marks the row SENT/FAILED; ``biometric_idx`` mirrors the client's
    unique transaction id (``{serial}_{uid}_{date}_{time}``) so a retried
    delivery can never double-count on their side.
    """

    __tablename__ = "punch_export_sync"

    id = Column(Integer, primary_key=True, index=True)
    visit_log_id = Column(
        Integer,
        ForeignKey("person_visit_log.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # Denormalised to preserve the facility identifier in outbound records.
    # Nullable while writers backfill it; tighten to NOT NULL in Phase 7.
    facility_id = Column(
        Integer,
        ForeignKey("facility_master.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    attendance_date = Column(Date, index=True)
    direction = Column(String(3))  # IN | OUT
    camera_id = Column(String(50))
    depends_on_id = Column(
        Integer,
        ForeignKey("punch_export_sync.id", ondelete="SET NULL"),
        nullable=True,
    )
    policy_version = Column(Integer)

    biometric_idx = Column(String(120), unique=True, nullable=False)
    # Client contract: "0" = IN, "1" = OUT (mirrors the value in ``payload``).
    bio_status = Column(String(1), nullable=True)
    payload = Column(JSONB, nullable=False)

    status = Column(String(10), default=ExportStatus.PENDING.value, nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    last_error = Column(Text, nullable=True)
    response_status = Column(Integer, nullable=True)
    response_payload = Column(JSONB, nullable=True)
    last_latency_ms = Column(Integer, nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=now_ist)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)

    visit_log = relationship("PersonVisitLog")

    __table_args__ = (
        Index("idx_pes_status_retry", "status", "next_retry_at"),
        UniqueConstraint(
            "user_id",
            "attendance_date",
            "direction",
            name="uq_punch_user_date_direction",
        ),
    )


class PreRegistration(Base):
    """One pre-registration pushed to the client HIS (patients only).

    A mutable transaction record with fat JSONB payloads — kept off the
    append-only ``person_visit_log`` for the same reason as the punch queue
    (docs/SCHEMA_REVIEW_R3.1.md 2.1). ``token_no`` / ``pre_regn_id`` come
    back in the client response and are shown on the screen ("Your token:
    G-1"); the raw request and response payloads are kept verbatim for audit
    and replay.
    """

    __tablename__ = "pre_registration"

    id = Column(Integer, primary_key=True, index=True)
    person_facility_id = Column(
        Integer,
        ForeignKey("person_facility.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # The entry event that triggered this pre-registration.
    visit_log_id = Column(
        Integer,
        ForeignKey("person_visit_log.id", ondelete="SET NULL"),
        nullable=True,
    )
    facility_id = Column(
        Integer,
        ForeignKey("facility_master.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status = Column(String(10), default=ExportStatus.PENDING.value, nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)

    # Extracted from the client response for quick lookup.
    pre_regn_id = Column(Integer, nullable=True)
    token_no = Column(String(20), nullable=True)
    queue_setup_id = Column(Integer, nullable=True)

    request_payload = Column(JSONB, nullable=True)
    response_payload = Column(JSONB, nullable=True)
    last_error = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=now_ist, index=True)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)

    mapping = relationship("PersonFacility")
    facility = relationship("Facility")


# ─────────────────────────────────────────────────────────────────────
# Back-compat aliases: services still import the pre-rename class names.
# (Aliases are plain Python names — relationship() strings must use the
# canonical class names, e.g. "PersonVisitLog".)
# ─────────────────────────────────────────────────────────────────────
PersonTrackingLog = PersonVisitLog
PunchExport = PunchExportSync
PreRegistrationLog = PreRegistration
