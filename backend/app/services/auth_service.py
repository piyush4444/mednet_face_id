"""Authentication and authorization for human users and service principals."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.auth_models import (
    ROLE_DEFAULTS,
    STAFF_GRANTABLE,
    Permission,
    Role,
    ServiceAccount,
    UserCredential,
)
from backend.app.db.identity_models import (
    PermissionMaster,
    RoleMaster,
    UserPermissionMapping,
    UserRoleMapping,
)
from backend.app.db.models import User, UserType
from backend.app.utils.time_ist import now_ist

logger = logging.getLogger("backend.auth")
_ph = PasswordHasher()
_SESSION_SALT = "iris.session.v2"


@dataclass(frozen=True)
class AuthPrincipal:
    """Resolved request actor; a human User or a non-human service account."""

    kind: str
    id: int
    username: str
    role: str
    is_active: bool
    granted_permissions: tuple[str, ...] = ()
    revoked_permissions: tuple[str, ...] = ()
    name: str | None = None
    user_type: str | None = None


def _get_secret() -> str:
    if settings.SESSION_SECRET:
        return settings.SESSION_SECRET
    if settings.DEBUG:
        return "dev-insecure-session-secret-do-not-use-in-prod"
    raise RuntimeError("SESSION_SECRET is not set")


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_get_secret(), salt=_SESSION_SALT)


def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, plain)
    except (VerifyMismatchError, InvalidHashError, Exception):  # noqa: BLE001
        return False


def needs_rehash(hashed: str) -> bool:
    try:
        return _ph.check_needs_rehash(hashed)
    except Exception:  # noqa: BLE001
        return False


def mint_session_token(principal: AuthPrincipal) -> str:
    return _serializer().dumps({"kind": principal.kind, "pid": principal.id})


def read_session_token(token: str) -> tuple[str, int] | None:
    try:
        data = _serializer().loads(token, max_age=settings.SESSION_TTL_HOURS * 3600)
    except (SignatureExpired, BadSignature, Exception):  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    kind, principal_id = data.get("kind"), data.get("pid")
    if kind not in {"human", "service"} or not isinstance(principal_id, int):
        return None
    return kind, principal_id


def _permission_deltas(db: Session, user_id: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
    rows = (
        db.query(UserPermissionMapping, PermissionMaster.code)
        .join(PermissionMaster, PermissionMaster.id == UserPermissionMapping.permission_id)
        .filter(UserPermissionMapping.user_id == user_id)
        .all()
    )
    granted = tuple(sorted(code for row, code in rows if row.effect == "GRANT"))
    revoked = tuple(sorted(code for row, code in rows if row.effect == "REVOKE"))
    return granted, revoked


def principal_for_user(
    db: Session, user: User, credential: UserCredential | None = None
) -> AuthPrincipal | None:
    credential = credential or (
        db.query(UserCredential).filter(UserCredential.user_id == user.id).first()
    )
    if credential is None:
        return None
    role_code = (
        db.query(RoleMaster.code)
        .join(UserRoleMapping, UserRoleMapping.role_id == RoleMaster.id)
        .filter(UserRoleMapping.user_id == user.id)
        .scalar()
    )
    if role_code is None:
        return None
    granted, revoked = _permission_deltas(db, user.id)
    return AuthPrincipal(
        kind="human",
        id=user.id,
        username=credential.username,
        role=role_code,
        is_active=bool(user.is_active and credential.is_active),
        granted_permissions=granted,
        revoked_permissions=revoked,
        name=user.name,
        user_type=user.user_type,
    )


def principal_for_service(account: ServiceAccount) -> AuthPrincipal:
    return AuthPrincipal(
        kind="service",
        id=account.id,
        username=account.username,
        role=account.role,
        is_active=account.is_active,
        granted_permissions=tuple(account.granted_permissions or []),
        revoked_permissions=tuple(account.revoked_permissions or []),
        name=account.username,
        user_type=account.principal_type,
    )


def get_account(db: Session, principal_id: int, kind: str = "human") -> AuthPrincipal | None:
    if kind == "service":
        row = db.get(ServiceAccount, principal_id)
        return principal_for_service(row) if row else None
    user = db.get(User, principal_id)
    return principal_for_user(db, user) if user else None


def get_account_by_username(db: Session, username: str) -> AuthPrincipal | None:
    credential = (
        db.query(UserCredential)
        .filter(UserCredential.username == username.strip())
        .first()
    )
    if credential:
        return principal_for_user(db, db.get(User, credential.user_id), credential)
    service = (
        db.query(ServiceAccount)
        .filter(ServiceAccount.username == username.strip())
        .first()
    )
    return principal_for_service(service) if service else None


def _username_exists(db: Session, username: str) -> bool:
    return bool(
        db.query(UserCredential.id).filter(UserCredential.username == username).first()
        or db.query(ServiceAccount.id).filter(ServiceAccount.username == username).first()
    )


def create_account(
    db: Session, *, user_id: int, username: str, password: str, role: Role
) -> AuthPrincipal:
    username = username.strip()
    if not username or len(password) < 8:
        raise ValueError("username is required and password must be at least 8 characters")
    if _username_exists(db, username):
        raise ValueError(f"username {username!r} already exists")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise ValueError("active user not found")
    if user.user_type not in {UserType.EMPLOYEE.value, UserType.DOCTOR.value}:
        raise ValueError("system access may only be assigned to an employee or doctor")
    if db.query(UserCredential).filter(UserCredential.user_id == user_id).first():
        raise ValueError("this user already has system access")
    role_row = db.query(RoleMaster).filter(RoleMaster.code == role.value).first()
    if role_row is None:
        raise ValueError(f"role {role.value!r} is not initialized")
    db.add(UserCredential(
        user_id=user.id,
        username=username,
        password_hash=hash_password(password),
        is_active=True,
        password_changed_at=now_ist(),
    ))
    db.add(UserRoleMapping(user_id=user.id, role_id=role_row.id))
    db.commit()
    return principal_for_user(db, user)


KIOSK_GRANTED = [Permission.STREAMS_VIEW.value]
KIOSK_REVOKED = [
    Permission.FRONTDESK_OPERATE.value,
    Permission.USERS_READ.value,
    Permission.TRACKING_READ.value,
    Permission.HISTORY_READ.value,
]


def create_kiosk_account(db: Session, *, username: str, password: str) -> AuthPrincipal:
    username = username.strip()
    if not username or len(password) < 8:
        raise ValueError("username is required and password must be at least 8 characters")
    if _username_exists(db, username):
        raise ValueError(f"username {username!r} already exists")
    row = ServiceAccount(
        username=username,
        password_hash=hash_password(password),
        principal_type="KIOSK",
        role=Role.STAFF.value,
        granted_permissions=KIOSK_GRANTED,
        revoked_permissions=KIOSK_REVOKED,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return principal_for_service(row)


def _parse_permissions(values) -> set[Permission]:
    out = set()
    for value in values or []:
        try:
            out.add(Permission(value))
        except ValueError:
            logger.warning("ignoring unknown permission %r", value)
    return out


def effective_permissions(
    principal: AuthPrincipal, db: Session | None = None
) -> frozenset[Permission]:
    codes = set()
    if db is not None:
        from backend.app.services import rbac_service

        codes = rbac_service.role_permission_codes(db, principal.role)
    if codes:
        base = _parse_permissions(codes)
    else:
        try:
            base = set(ROLE_DEFAULTS.get(Role(principal.role), frozenset()))
        except ValueError:
            base = set()
    base |= _parse_permissions(principal.granted_permissions)
    base -= _parse_permissions(principal.revoked_permissions)
    return frozenset(base)


def has_permission(
    principal: AuthPrincipal, permission: Permission, db: Session | None = None
) -> bool:
    return permission in effective_permissions(principal, db)


def assert_can_manage(
    actor: AuthPrincipal, target: AuthPrincipal, db: Session | None = None
) -> None:
    if actor.kind != "human":
        raise PermissionError("service accounts cannot manage users")
    if actor.id == target.id and actor.kind == target.kind:
        raise PermissionError("cannot manage your own access here")
    actor_perms = effective_permissions(actor, db)
    if Permission.ACCOUNTS_MANAGE_ALL in actor_perms:
        if target.role == Role.SUPER_ADMIN.value:
            raise PermissionError("cannot manage another superadmin")
        return
    if Permission.ACCOUNTS_MANAGE_STAFF in actor_perms and target.role == Role.STAFF.value:
        return
    raise PermissionError("insufficient rights to manage this user")


def assert_can_grant(
    actor: AuthPrincipal,
    target: AuthPrincipal,
    permission: Permission,
    db: Session | None = None,
) -> None:
    assert_can_manage(actor, target, db)
    actor_perms = effective_permissions(actor, db)
    if Permission.ACCOUNTS_MANAGE_ALL in actor_perms:
        return
    if permission not in STAFF_GRANTABLE or permission not in actor_perms:
        raise PermissionError(f"{permission.value} is not grantable to staff")


@dataclass
class _Attempts:
    count: int = 0
    locked_until: datetime | None = None


_attempts: dict[tuple[str, str], _Attempts] = {}
_attempts_lock = threading.Lock()


def is_locked_out(username: str, ip: str) -> bool:
    with _attempts_lock:
        record = _attempts.get((username, ip))
        if record is None or record.locked_until is None:
            return False
        if now_ist() >= record.locked_until:
            _attempts.pop((username, ip), None)
            return False
        return True


def record_login_failure(username: str, ip: str) -> None:
    with _attempts_lock:
        record = _attempts.setdefault((username, ip), _Attempts())
        record.count += 1
        if record.count >= settings.LOGIN_MAX_ATTEMPTS:
            record.locked_until = now_ist() + timedelta(
                minutes=settings.LOGIN_LOCKOUT_MINUTES
            )


def record_login_success(username: str, ip: str) -> None:
    with _attempts_lock:
        _attempts.pop((username, ip), None)


def authenticate(
    db: Session, *, username: str, password: str, ip: str
) -> AuthPrincipal | None:
    credential = (
        db.query(UserCredential)
        .filter(UserCredential.username == username.strip())
        .first()
    )
    if credential:
        user = db.get(User, credential.user_id)
        principal = principal_for_user(db, user, credential) if user else None
        if not principal or not principal.is_active or not verify_password(
            password, credential.password_hash
        ):
            return None
        if needs_rehash(credential.password_hash):
            credential.password_hash = hash_password(password)
        credential.last_login_at = now_ist()
        db.commit()
        return principal_for_user(db, user, credential)

    service = (
        db.query(ServiceAccount)
        .filter(ServiceAccount.username == username.strip())
        .first()
    )
    if not service or not service.is_active or not verify_password(
        password, service.password_hash
    ):
        return None
    if needs_rehash(service.password_hash):
        service.password_hash = hash_password(password)
    service.last_login_at = now_ist()
    db.commit()
    return principal_for_service(service)
