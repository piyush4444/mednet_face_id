"""
auth_service.py — password hashing, session tokens, and the RBAC policy.

Everything security-sensitive about accounts funnels through here so the
policy lives in one auditable place:

* Password hashing with argon2id (``argon2-cffi``).
* Stateless session cookies signed with ``itsdangerous`` carrying ONLY the
  account id (+ issue timestamp for TTL). No role/permission is baked into
  the token — see ``effective_permissions``.
* :func:`effective_permissions` — the single computation of a principal's
  capabilities (role defaults ∪ granted − revoked).
* :func:`assert_can_grant` — the hierarchy rules for who may hand which
  permission to whom.
* In-memory brute-force lockout keyed by (username, client-ip).

The camera subsystem never imports this module; it is web-tier only.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.auth_models import (
    ROLE_DEFAULTS,
    STAFF_GRANTABLE,
    Permission,
    Role,
    StaffAccount,
)
from backend.app.utils.time_ist import now_ist

logger = logging.getLogger("backend.auth")

# argon2id with library defaults — sane memory/time cost for a login path.
_ph = PasswordHasher()

# Cookie payload namespace. Rotating the salt invalidates all live sessions
# without changing the secret.
_SESSION_SALT = "iris.session.v1"


# ── Secret handling ──────────────────────────────────────────────────────
def _get_secret() -> str:
    """Return the signing secret, or fail loudly outside DEBUG.

    A blank secret in production would let anyone forge a session cookie, so
    we refuse to mint/verify tokens rather than fall back to a known value.
    In DEBUG we allow a fixed dev secret purely so local work doesn't need a
    .env — never rely on this off a developer machine.
    """
    secret = settings.SESSION_SECRET
    if secret:
        return secret
    if settings.DEBUG:
        return "dev-insecure-session-secret-do-not-use-in-prod"
    raise RuntimeError(
        "SESSION_SECRET is not set. Refusing to sign session cookies with a "
        "default. Set the SESSION_SECRET environment variable."
    )


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_get_secret(), salt=_SESSION_SALT)


# ── Password hashing ─────────────────────────────────────────────────────
def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, plain)
    except (VerifyMismatchError, InvalidHashError, Exception):  # noqa: BLE001
        return False


def needs_rehash(hashed: str) -> bool:
    """True if the stored hash was made with weaker params than current."""
    try:
        return _ph.check_needs_rehash(hashed)
    except Exception:  # noqa: BLE001
        return False


# ── Session tokens (account-id only) ─────────────────────────────────────
def mint_session_token(account_id: int) -> str:
    """Sign ``{"aid": id}`` into a URL-safe, timestamped token."""
    return _serializer().dumps({"aid": account_id})


def read_session_token(token: str) -> int | None:
    """Return the account id from a valid, unexpired token, else None."""
    if not token:
        return None
    max_age = settings.SESSION_TTL_HOURS * 3600
    try:
        data = _serializer().loads(token, max_age=max_age)
    except (SignatureExpired, BadSignature):
        return None
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    aid = data.get("aid")
    return aid if isinstance(aid, int) else None


# ── Account lookup / creation ────────────────────────────────────────────
def get_account(db: Session, account_id: int) -> StaffAccount | None:
    return db.get(StaffAccount, account_id)


def get_account_by_username(db: Session, username: str) -> StaffAccount | None:
    return (
        db.query(StaffAccount)
        .filter(StaffAccount.username == username)
        .one_or_none()
    )


# A kiosk device login is a STAFF account stripped to exactly the two
# permissions the unattended kiosk needs. The single source of truth for
# that delta — reused by the CLI (--kiosk) and the kiosk-device admin.
KIOSK_GRANTED: list[str] = [Permission.STREAMS_VIEW.value]
KIOSK_REVOKED: list[str] = [
    Permission.FRONTDESK_OPERATE.value,
    Permission.USERS_READ.value,
    Permission.TRACKING_READ.value,
    Permission.HISTORY_READ.value,
]


def create_kiosk_account(db: Session, *, username: str, password: str) -> StaffAccount:
    """Create a minimal kiosk device login (kiosk.operate + streams.view)."""
    return create_account(
        db, username=username, password=password, role=Role.STAFF,
        granted=KIOSK_GRANTED, revoked=KIOSK_REVOKED,
    )


def create_account(
    db: Session,
    *,
    username: str,
    password: str,
    role: Role,
    granted: list[str] | None = None,
    revoked: list[str] | None = None,
) -> StaffAccount:
    """Create and persist an account. Raises ValueError on duplicate username.

    ``granted`` / ``revoked`` are optional per-account permission deltas
    over the role default (used e.g. to provision a minimal kiosk device
    account). Callers pass permission *value* strings.
    """
    username = username.strip()
    if not username:
        raise ValueError("username must not be empty")
    if get_account_by_username(db, username) is not None:
        raise ValueError(f"username {username!r} already exists")
    acct = StaffAccount(
        username=username,
        password_hash=hash_password(password),
        role=role.value,
        is_active=True,
        granted_permissions=list(granted or []),
        revoked_permissions=list(revoked or []),
    )
    db.add(acct)
    db.commit()
    db.refresh(acct)
    return acct


# ── Effective permissions (THE computation) ──────────────────────────────
def _parse_perm_list(raw) -> set[Permission]:
    out: set[Permission] = set()
    for item in raw or []:
        try:
            out.add(Permission(item))
        except ValueError:
            # Unknown permission string (e.g. renamed in a later version).
            # Ignore rather than crash the request.
            logger.warning("Ignoring unknown permission %r on account", item)
    return out


def _role_default_permissions(
    account: StaffAccount, db: Session | None
) -> set[Permission]:
    """Base permissions for the account's role.

    Sourced from the DB catalog (``role_permission_mapping``) when a session
    is supplied and the role is seeded there; otherwise falls back to the
    in-code :data:`ROLE_DEFAULTS`. ``rbac_service.sync_catalog`` keeps the two
    identical on boot, so the fallback never changes the answer — it only
    covers the pre-seed / no-session paths.
    """
    if db is not None:
        from backend.app.services import rbac_service

        try:
            codes = rbac_service.role_permission_codes(db, account.role)
        except Exception as exc:  # noqa: BLE001
            # Catalog unreadable (tables not yet created, DB hiccup, …).
            # Never fail a permission check on it — fall through to the
            # in-code defaults, which carry the same data.
            logger.warning("RBAC catalog unreadable (%s); using ROLE_DEFAULTS", exc)
            db.rollback()  # clear the failed transaction for later queries
        else:
            if codes:
                return _parse_perm_list(codes)
    role = _safe_role(account.role)
    if role is None:
        logger.error("Account %s has unknown role %r; no permissions",
                     account.id, account.role)
        return set()
    return set(ROLE_DEFAULTS.get(role, frozenset()))


def effective_permissions(
    account: StaffAccount, db: Session | None = None
) -> frozenset[Permission]:
    """Role defaults ∪ granted − revoked. The one place this is computed.

    Pass ``db`` to source the role bundle from the DB RBAC catalog; without
    it the in-code :data:`ROLE_DEFAULTS` is used (identical data).
    """
    base = _role_default_permissions(account, db)
    base |= _parse_perm_list(account.granted_permissions)
    base -= _parse_perm_list(account.revoked_permissions)
    return frozenset(base)


def has_permission(
    account: StaffAccount, perm: Permission, db: Session | None = None
) -> bool:
    return perm in effective_permissions(account, db)


# ── Grant hierarchy policy ───────────────────────────────────────────────
def assert_can_manage(actor: StaffAccount, target: StaffAccount) -> None:
    """Raise PermissionError unless ``actor`` may administer ``target``.

    * super_admin (accounts.manage_all) may manage anyone below super_admin.
    * admin (accounts.manage_staff) may manage staff accounts only.
    * nobody may manage their own account (no self-escalation / self-lockout
      via this path — password change is a separate, self-only flow).
    """
    if actor.id == target.id:
        raise PermissionError("cannot manage your own account here")

    actor_perms = effective_permissions(actor)
    target_role = _safe_role(target.role)

    if Permission.ACCOUNTS_MANAGE_ALL in actor_perms:
        if target_role == Role.SUPER_ADMIN:
            raise PermissionError("cannot manage another super_admin")
        return
    if Permission.ACCOUNTS_MANAGE_STAFF in actor_perms:
        if target_role != Role.STAFF:
            raise PermissionError("admins may only manage staff accounts")
        return
    raise PermissionError("insufficient rights to manage accounts")


def assert_can_grant(
    actor: StaffAccount, target: StaffAccount, perm: Permission
) -> None:
    """Raise PermissionError unless ``actor`` may grant ``perm`` to ``target``.

    Enforces the no-escalation-by-proxy rule: an admin may only grant a
    permission it itself holds, and only from :data:`STAFF_GRANTABLE`.
    super_admin may grant anything (subject to :func:`assert_can_manage`).
    """
    assert_can_manage(actor, target)
    actor_perms = effective_permissions(actor)

    if Permission.ACCOUNTS_MANAGE_ALL in actor_perms:
        return  # super_admin — unrestricted within manage rules
    # admin path
    if perm not in STAFF_GRANTABLE:
        raise PermissionError(f"{perm.value} is not grantable to staff")
    if perm not in actor_perms:
        raise PermissionError(f"cannot grant {perm.value} you do not hold")


def _safe_role(raw: str) -> Role | None:
    try:
        return Role(raw)
    except ValueError:
        return None


# ── Brute-force lockout (in-memory, single-process) ──────────────────────
# The camera invariant forbids multiple uvicorn workers, so a single process
# owns all login traffic and an in-memory map is sufficient. Resets on
# restart, which is acceptable for a throttle. Keyed by (username, ip).
@dataclass
class _Attempts:
    count: int = 0
    locked_until: datetime | None = None


_attempts: dict[tuple[str, str], _Attempts] = {}
_attempts_lock = threading.Lock()


def is_locked_out(username: str, ip: str) -> bool:
    key = (username, ip)
    with _attempts_lock:
        rec = _attempts.get(key)
        if rec is None or rec.locked_until is None:
            return False
        if now_ist() >= rec.locked_until:
            # Window elapsed — clear and allow.
            _attempts.pop(key, None)
            return False
        return True


def record_login_failure(username: str, ip: str) -> None:
    key = (username, ip)
    with _attempts_lock:
        rec = _attempts.setdefault(key, _Attempts())
        rec.count += 1
        if rec.count >= settings.LOGIN_MAX_ATTEMPTS:
            rec.locked_until = now_ist() + timedelta(
                minutes=settings.LOGIN_LOCKOUT_MINUTES
            )


def record_login_success(username: str, ip: str) -> None:
    with _attempts_lock:
        _attempts.pop((username, ip), None)


# ── Authentication ───────────────────────────────────────────────────────
def authenticate(
    db: Session, *, username: str, password: str, ip: str
) -> StaffAccount | None:
    """Verify credentials. Returns the account on success, else None.

    Callers must check :func:`is_locked_out` first and record failure /
    success via the helpers so the throttle stays accurate. Transparently
    upgrades the stored hash if argon2 params have strengthened.
    """
    acct = get_account_by_username(db, username)
    if acct is None or not acct.is_active:
        return None
    if not verify_password(password, acct.password_hash):
        return None
    if needs_rehash(acct.password_hash):
        acct.password_hash = hash_password(password)
    acct.last_login_at = now_ist()
    db.commit()
    return acct
