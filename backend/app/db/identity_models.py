"""
identity_models.py — the person registry, login credentials, and RBAC.

B2B restructure, round 3.1 (see ``docs/SCHEMA_REVIEW_R3.1.md``). This
module holds the identity/auth half of the hardened model:

- ``user_profile``   : the PERSON_REGISTRY. One row per human; ``id`` is the
                       FAISS index key (kept integer). Identity only — a
                       person's *type* is per-facility (see
                       ``facility_models.PersonFacility``), so there is no
                       ``user_type`` column here.
- ``user_auth``      : login credentials for the subset of people who can
                       sign in. Optional 1:1 with ``user_profile``. Roles and
                       permissions live in the mapping tables below, not
                       inline. A system-generated one-time password forces a
                       reset on first login (``password_changed_at IS NULL``).
- ``role_master`` /
  ``permission_master`` /
  ``role_permission_mapping`` /
  ``user_role_mapping`` : per-facility RBAC. A role is a reusable bundle of
                       permissions (``role_permission_mapping``); a login gets
                       one or more roles *scoped to a facility*
                       (``user_role_mapping``) — the same person can be admin
                       at facility Z and a guest at facility Y. A NULL
                       ``facility_id`` means "all facilities" (global
                       principal / super-admin).

Design notes
------------
- Enum-like columns (role/permission codes) are plain strings validated at
  the service layer, matching the rest of the codebase.
- Timestamps are timezone-aware (``timestamptz``); ``now_ist`` supplies a
  tz-aware default, so Postgres stores UTC and renders local at the edge.
- Delete rules protect the audit chain: ``user_auth → user_profile`` is
  RESTRICT (never orphan an identity by deleting its login); role/permission
  membership rows CASCADE.
"""

import enum
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from backend.app.db.postgres import Base
from backend.app.utils.time_ist import now_ist


class UserProfile(Base):
    """A registered identity — the PERSON_REGISTRY.

    ``id`` doubles as the FAISS index key; it must never be renumbered.
    The face embedding is not stored here — it lives in the FAISS index
    keyed by ``user_profile.id``.
    """

    __tablename__ = "user_profile"

    id = Column(Integer, primary_key=True, index=True)
    person_guid = Column(
        String(36),
        unique=True,
        nullable=True,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )

    # ── Name ──────────────────────────────────────────────────────
    prefix = Column(String(10), nullable=True)          # Mr. / Mrs. / Dr.
    first_name = Column(String(100), nullable=True)
    middle_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    # Canonical display string (FAISS overlays, search).
    name = Column(String(255), nullable=False, index=True)

    # ── Demographics ──────────────────────────────────────────────
    age = Column(Integer, nullable=True)
    gender = Column(String(10), nullable=True)
    dob = Column(Date, nullable=True)

    # ── Contact ───────────────────────────────────────────────────
    contact_number = Column(String(20), nullable=True)
    whatsapp_number = Column(String(20), nullable=True)
    email = Column(String(255), nullable=True)
    address = Column(String(500), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    country = Column(String(100), nullable=True)
    pin_code = Column(String(10), nullable=True)

    # ── National id ───────────────────────────────────────────────
    national_id_type = Column(String(20), nullable=True)  # UID/ABHA/PAN/...
    national_id = Column(String(50), nullable=True)

    # ── Next of kin ───────────────────────────────────────────────
    next_of_kin_name = Column(String(255), nullable=True)
    next_of_kin_relation = Column(String(50), nullable=True)
    next_of_kin_contact = Column(String(20), nullable=True)

    photo_url = Column(String(500), nullable=True)

    # Retire here — never hard-delete (see docs/SCHEMA_REVIEW_R3.1.md 2.4).
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), default=now_ist)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)

    auth = relationship("UserAuth", back_populates="profile", uselist=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UserProfile id={self.id} name={self.name!r}>"


class UserAuth(Base):
    """Login credentials for a person who can sign in.

    Optional 1:1 with :class:`UserProfile`. ``username`` / ``password_hash``
    are system-generated on create; ``password_changed_at IS NULL`` marks a
    one-time password that must be reset on first login. Effective
    permissions are computed from the role mappings, never stored here.
    """

    __tablename__ = "user_auth"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("user_profile.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
        index=True,
    )

    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    user_salt = Column(String(64), nullable=False)
    # NULL ⇒ never changed since creation ⇒ force reset on first login.
    password_changed_at = Column(DateTime(timezone=True), nullable=True)

    is_staff = Column(Boolean, default=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    last_login_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=now_ist)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)

    profile = relationship("UserProfile", back_populates="auth")
    role_links = relationship(
        "UserRoleMapping", back_populates="account", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UserAuth id={self.id} username={self.username!r}>"


class RoleMaster(Base):
    """A named role — a reusable bundle of permissions."""

    __tablename__ = "role_master"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(40), unique=True, nullable=False, index=True)  # staff/admin/...
    name = Column(String(100), nullable=False)
    description = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), default=now_ist)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)

    permission_links = relationship(
        "RolePermissionMapping", back_populates="role", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RoleMaster id={self.id} code={self.code!r}>"


class PermissionMaster(Base):
    """A single capability the routes guard on (e.g. ``users.read``)."""

    __tablename__ = "permission_master"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), default=now_ist)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PermissionMaster id={self.id} code={self.code!r}>"


class RolePermissionMapping(Base):
    """Which permissions a role grants (facility-independent template)."""

    __tablename__ = "role_permission_mapping"

    id = Column(Integer, primary_key=True, index=True)
    role_id = Column(
        Integer,
        ForeignKey("role_master.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    permission_id = Column(
        Integer,
        ForeignKey("permission_master.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at = Column(DateTime(timezone=True), default=now_ist)

    role = relationship("RoleMaster", back_populates="permission_links")
    permission = relationship("PermissionMaster")

    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),
    )


class UserRoleMapping(Base):
    """A login's role, scoped to a facility.

    ``facility_id`` NULL ⇒ the grant applies to every facility (global
    principal / super-admin). The same account can hold different roles at
    different facilities.
    """

    __tablename__ = "user_role_mapping"

    id = Column(Integer, primary_key=True, index=True)
    user_auth_id = Column(
        Integer,
        ForeignKey("user_auth.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    facility_id = Column(
        Integer,
        ForeignKey("facility_master.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    role_id = Column(
        Integer,
        ForeignKey("role_master.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at = Column(DateTime(timezone=True), default=now_ist)

    account = relationship("UserAuth", back_populates="role_links")
    role = relationship("RoleMaster")
    facility = relationship("Facility")

    __table_args__ = (
        UniqueConstraint(
            "user_auth_id", "facility_id", "role_id", name="uq_user_facility_role"
        ),
    )


class PermissionEffect(str, enum.Enum):
    """Direction of a per-account permission delta."""

    GRANT = "GRANT"
    REVOKE = "REVOKE"


class UserPermissionMapping(Base):
    """A per-account permission delta over the role bundle, facility-scoped.

    The escape hatch in the super_admin → admin → staff hierarchy: a
    super_admin can hand one admin a capability their role lacks (GRANT), or
    take one away (REVOKE), at a given facility — without inventing a bespoke
    role for a one-off. Normal accounts have no rows here and get exactly
    their role's bundle.

    ``facility_id`` NULL ⇒ the delta applies at every facility (same wildcard
    as :class:`UserRoleMapping`).

    Effective permissions are therefore::

        (bundles of the account's roles at this facility)
          ∪ (its GRANTs)
          − (its REVOKEs)

    computed in exactly one place — ``auth_service.effective_permissions``.
    """

    __tablename__ = "user_permission_mapping"

    id = Column(Integer, primary_key=True, index=True)
    user_auth_id = Column(
        Integer,
        ForeignKey("user_auth.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    facility_id = Column(
        Integer,
        ForeignKey("facility_master.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    permission_id = Column(
        Integer,
        ForeignKey("permission_master.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    effect = Column(
        String(8), nullable=False, default=PermissionEffect.GRANT.value
    )
    created_at = Column(DateTime(timezone=True), default=now_ist)

    account = relationship("UserAuth")
    permission = relationship("PermissionMaster")
    facility = relationship("Facility")

    __table_args__ = (
        UniqueConstraint(
            "user_auth_id",
            "facility_id",
            "permission_id",
            name="uq_user_facility_permission",
        ),
    )


# ─────────────────────────────────────────────────────────────────────
# Side-effect import: ``user_role_mapping.facility_id`` references
# ``facility_master``. Import the facility models so that FK target is
# registered on the shared metadata regardless of import order.
# ─────────────────────────────────────────────────────────────────────
from backend.app.db import facility_models  # noqa: E402, F401
