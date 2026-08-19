"""
auth_models.py — Login principals and the RBAC permission vocabulary.

This is deliberately SEPARATE from ``app.db.models`` (the ``users`` table).
That table holds recognition *subjects* — patients, doctors, visitors — who
never log in. A :class:`StaffAccount` is a *login principal*: a human who
authenticates to operate the system.

Permission model (see the ``auth-rbac`` skill for the full spec):

* Every account has one :class:`Role`. A role is a *bundle* of default
  permissions (:data:`ROLE_DEFAULTS`).
* On top of the role default, an account may carry per-account
  ``granted_permissions`` (additions) and ``revoked_permissions``
  (subtractions). This is what lets an admin hand a single staff member,
  say, ``streams.view`` without promoting them.
* Effective permissions = role defaults ∪ granted − revoked. That set is
  computed in exactly one place — ``auth_service.effective_permissions`` —
  and both the API guards and ``GET /auth/me`` read from it.

The stored token/cookie carries only the account id; role and permissions
are recomputed from this row on every request, so a grant/revoke or a
disable takes effect on the target's very next call with no re-login.
"""

import enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
)
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
    FACILITIES_MANAGE = "facilities.manage"       # facilities + locations CRUD
    KIOSK_OPERATE = "kiosk.operate"               # /kiosk/* (front desk + kiosk device)
    KIOSK_MANAGE = "kiosk.manage"                 # /kiosk-admin/* — kiosk devices + logs
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
    # Front-desk staff run the entry-gate kiosk (and the unattended kiosk
    # device principal is granted this same permission).
    P.KIOSK_OPERATE,
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
    P.FACILITIES_MANAGE,
    P.INTEGRATIONS_MANAGE,
    # Kiosk devices + kiosk activity/pre-reg/attendance logs (PII).
    P.KIOSK_MANAGE,
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


class StaffAccount(Base):
    """A human login principal.

    ``granted_permissions`` / ``revoked_permissions`` store lists of
    :class:`Permission` *values* (the dotted strings). They are applied on
    top of the role default by ``auth_service.effective_permissions``.
    """

    __tablename__ = "staff_accounts"

    id = Column(Integer, primary_key=True, index=True)

    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)

    role = Column(String(20), nullable=False, default=Role.STAFF.value, index=True)

    is_active = Column(Boolean, nullable=False, default=True)

    # Per-account permission deltas over the role default. JSONB lists of
    # permission strings. Default-empty via a server default so rows created
    # by raw SQL are well-formed too.
    granted_permissions = Column(JSONB, nullable=False, default=list)
    revoked_permissions = Column(JSONB, nullable=False, default=list)

    created_at = Column(DateTime(timezone=True), default=now_ist)
    last_login_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<StaffAccount id={self.id} username={self.username!r} "
            f"role={self.role!r} active={self.is_active}>"
        )
