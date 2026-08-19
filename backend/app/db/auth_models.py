"""
auth_models.py — Login principals and the RBAC permission vocabulary.

Human identity lives only in ``app.db.models.User``. ``UserCredential`` is an
optional one-to-one secret record for users who may sign in; ``ServiceAccount``
represents non-human integration principals.

Permission model (see the ``auth-rbac`` skill for the full spec):

* Every principal has one :class:`Role`. A role is a *bundle* of default
  permissions (:data:`ROLE_DEFAULTS`).
* Human overrides live in ``UserPermissionMapping``; non-human service
  accounts store equivalent grant/revoke lists on their own row.
* Effective permissions = role defaults ∪ granted − revoked. That set is
  computed in exactly one place — ``auth_service.effective_permissions`` —
  and both the API guards and ``GET /auth/me`` read from it.

The stored token/cookie carries only principal kind and id. Role and
permissions are recomputed from the database on every request, so a change
takes effect on the target's next call without requiring another login.
"""

import enum

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB

from backend.app.db.postgres import Base
from backend.app.utils.time_ist import now_ist


class Role(str, enum.Enum):
    """The three management tiers. Stored as ``VARCHAR`` (see UserType)."""

    STAFF = "staff"              # front desk
    ADMIN = "admin"             # security / ops
    SUPER_ADMIN = "super_admin"  # dev / owner


class Permission(str, enum.Enum):
    """The flat capability vocabulary. Routes guard on these, never on roles.

    Keep this list as the single source of truth for the whole permission
    surface — Phase 2 route guards reference these values by name.
    """

    FRONTDESK_OPERATE = "frontdesk.operate"       # scan, visits, slips
    USERS_READ = "users.read"                     # list/search subjects
    USERS_WRITE = "users.write"                   # create/update/delete subjects
    FACES_ENROLL = "faces.enroll"                 # register / update-face
    TRACKING_READ = "tracking.read"               # presence, dashboard WS
    HISTORY_READ = "history.read"                 # historical sessions/visits
    STREAMS_VIEW = "streams.view"                 # MJPEG / snapshot
    CAMERAS_MANAGE = "cameras.manage"             # camera CRUD / test / restart
    FRONTDESK_ADMIN_MANAGE = "frontdesk_admin.manage"  # depts / rooms / doctors
    METRICS_READ = "metrics.read"                 # /metrics, /docs
    ACCOUNTS_MANAGE_STAFF = "accounts.manage_staff"    # manage staff logins
    ACCOUNTS_MANAGE_ALL = "accounts.manage_all"        # manage admin logins
    AUDIT_READ = "audit.read"                     # read the audit log
    BACKUP_MANAGE = "backup.manage"               # export / import (see backup skill)
    # ── B2B restructure surfaces ──
    LOCATIONS_MANAGE = "locations.manage"         # singleton facility locations
    INTEGRATIONS_MANAGE = "integrations.manage"   # /integrations/* status/flush/retry


# ── Role default permission bundles ──────────────────────────────────────
# The ONE place the role→permission matrix is encoded. Admin/super_admin
# grant rules (who may hand out what) live in ``auth_service`` alongside
# ``effective_permissions`` so the whole policy is in two adjacent files.
P = Permission

_STAFF_DEFAULTS = {
    P.FRONTDESK_OPERATE,
    P.USERS_READ,
    P.TRACKING_READ,
    P.HISTORY_READ,
}

_ADMIN_DEFAULTS = _STAFF_DEFAULTS | {
    P.USERS_WRITE,
    P.FACES_ENROLL,
    P.STREAMS_VIEW,
    P.CAMERAS_MANAGE,
    P.FRONTDESK_ADMIN_MANAGE,
    P.ACCOUNTS_MANAGE_STAFF,
    P.AUDIT_READ,
    # Deployment config + outbound integration health are admin-tier.
    P.LOCATIONS_MANAGE,
    P.INTEGRATIONS_MANAGE,
}

_SUPER_ADMIN_DEFAULTS = set(Permission)  # everything

ROLE_DEFAULTS: dict[Role, frozenset[Permission]] = {
    Role.STAFF: frozenset(_STAFF_DEFAULTS),
    Role.ADMIN: frozenset(_ADMIN_DEFAULTS),
    Role.SUPER_ADMIN: frozenset(_SUPER_ADMIN_DEFAULTS),
}

# Permissions an admin (accounts.manage_staff holder) is allowed to hand to
# a staff account. Intentionally excludes camera/account/backup/metrics tier
# so an admin can never escalate a staff account toward its own power. An
# admin may only grant from this set AND only permissions it itself holds.
STAFF_GRANTABLE: frozenset[Permission] = frozenset({
    P.STREAMS_VIEW,
    P.HISTORY_READ,
    P.FACES_ENROLL,
    P.USERS_WRITE,
    P.FRONTDESK_ADMIN_MANAGE,
})


class UserCredential(Base):
    """Optional login secret for one canonical ``users`` row."""

    __tablename__ = "user_credentials"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    password_changed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=now_ist)
    last_login_at = Column(DateTime(timezone=True), nullable=True)


class ServiceAccount(Base):
    """Non-human principal reserved for external integrations."""

    __tablename__ = "service_accounts"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    principal_type = Column(String(20), nullable=False, default="INTEGRATION")
    role = Column(String(20), nullable=False, default=Role.STAFF.value)
    granted_permissions = Column(JSONB, nullable=False, default=list)
    revoked_permissions = Column(JSONB, nullable=False, default=list)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=now_ist)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
