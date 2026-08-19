"""
accounts.py — login-account management (create / list / role / grants).

Mounted at ``/auth/accounts`` and guarded by ``accounts.manage_staff`` at the
router level. Because ``super_admin`` holds every permission (including
``accounts.manage_staff``), that single router guard admits both admins and
super_admins; the finer hierarchy — who may touch whom, and hand out which
permission — is enforced per-request by the policy helpers in
``auth_service`` (``assert_can_manage`` / ``assert_can_grant``).

The actor is always the live logged-in account (``get_current_account``), so
these endpoints require a session even while ``AUTH_ENABLED`` is False (the
router-level permission guard is a no-op then, but you still must be logged
in to administer accounts). Bootstrap the first super_admin with the CLI.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.core.deps import (
    get_current_account,
    require_permission,
    verify_csrf,
)
from backend.app.db.auth_models import Permission, Role, StaffAccount
from backend.app.db.postgres import get_db
from backend.app.services import audit_service, auth_service

logger = logging.getLogger("backend.auth")


def _client_ip(request) -> str:
    xff = request.headers.get("x-forwarded-for") if request else None
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request and request.client else "unknown"

router = APIRouter(
    prefix="/auth/accounts",
    tags=["accounts"],
    dependencies=[Depends(require_permission(Permission.ACCOUNTS_MANAGE_STAFF))],
)


# ── Schemas ──────────────────────────────────────────────────────────────
class AccountOut(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    granted_permissions: list[str]
    revoked_permissions: list[str]
    effective_permissions: list[str]


class CreateAccountIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=8, max_length=256)
    role: str = Field(..., description="staff | admin | super_admin")


class UpdateAccountIn(BaseModel):
    role: str | None = None
    is_active: bool | None = None


class PermissionsPatchIn(BaseModel):
    grant: list[str] = Field(default_factory=list)
    revoke: list[str] = Field(default_factory=list)


# ── Helpers ──────────────────────────────────────────────────────────────
def _to_out(acct: StaffAccount) -> AccountOut:
    eff = sorted(p.value for p in auth_service.effective_permissions(acct))
    return AccountOut(
        id=acct.id,
        username=acct.username,
        role=acct.role,
        is_active=acct.is_active,
        granted_permissions=list(acct.granted_permissions or []),
        revoked_permissions=list(acct.revoked_permissions or []),
        effective_permissions=eff,
    )


def _parse_role(raw: str) -> Role:
    try:
        return Role(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid role {raw!r}")


def _parse_perm(raw: str) -> Permission:
    try:
        return Permission(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid permission {raw!r}")


def _assert_can_set_role(actor: StaffAccount, target_role: Role) -> None:
    """Only manage_all (super_admin) may create/assign admin or super_admin."""
    actor_perms = auth_service.effective_permissions(actor)
    if target_role in (Role.ADMIN, Role.SUPER_ADMIN):
        if Permission.ACCOUNTS_MANAGE_ALL not in actor_perms:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="only a super_admin may create or assign that role",
            )


def _get_target(db: Session, account_id: int) -> StaffAccount:
    acct = auth_service.get_account(db, account_id)
    if acct is None:
        raise HTTPException(status_code=404, detail="account not found")
    return acct


# ── Routes ───────────────────────────────────────────────────────────────
@router.get("", response_model=list[AccountOut])
def list_accounts(
    actor: StaffAccount = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """List accounts the actor may see.

    Admins (manage_staff only) see staff accounts; super_admins (manage_all)
    see everyone.
    """
    actor_perms = auth_service.effective_permissions(actor)
    rows = db.query(StaffAccount).order_by(StaffAccount.id).all()
    if Permission.ACCOUNTS_MANAGE_ALL in actor_perms:
        visible = rows
    else:
        visible = [r for r in rows if r.role == Role.STAFF.value]
    return [_to_out(r) for r in visible]


@router.post("", response_model=AccountOut, status_code=201,
             dependencies=[Depends(verify_csrf)])
def create_account_endpoint(
    payload: CreateAccountIn,
    request: Request,
    actor: StaffAccount = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    target_role = _parse_role(payload.role)
    _assert_can_set_role(actor, target_role)
    try:
        acct = auth_service.create_account(
            db,
            username=payload.username,
            password=payload.password,
            role=target_role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    audit_service.record(
        db, action="account.create", actor=actor, ip=_client_ip(request),
        target_type="account", target_id=acct.id,
        detail={"username": acct.username, "role": acct.role},
    )
    return _to_out(acct)


@router.patch("/{account_id}", response_model=AccountOut,
              dependencies=[Depends(verify_csrf)])
def update_account_endpoint(
    account_id: int,
    payload: UpdateAccountIn,
    request: Request,
    actor: StaffAccount = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """Change an account's role and/or active flag."""
    target = _get_target(db, account_id)
    try:
        auth_service.assert_can_manage(actor, target)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    if payload.role is not None:
        new_role = _parse_role(payload.role)
        _assert_can_set_role(actor, new_role)
        target.role = new_role.value
    if payload.is_active is not None:
        target.is_active = payload.is_active

    db.commit()
    db.refresh(target)
    audit_service.record(
        db, action="account.update", actor=actor, ip=_client_ip(request),
        target_type="account", target_id=target.id,
        detail={"role": target.role, "is_active": target.is_active},
    )
    return _to_out(target)


@router.patch("/{account_id}/permissions", response_model=AccountOut,
              dependencies=[Depends(verify_csrf)])
def patch_permissions_endpoint(
    account_id: int,
    payload: PermissionsPatchIn,
    request: Request,
    actor: StaffAccount = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """Grant and/or revoke individual permissions on an account.

    Each permission is checked against the grant hierarchy: an admin may only
    touch permissions it itself holds and that are staff-grantable; a
    super_admin may touch anything (subject to the manage rules).
    """
    target = _get_target(db, account_id)

    grants = [_parse_perm(p) for p in payload.grant]
    revokes = [_parse_perm(p) for p in payload.revoke]

    # Authorise every change before applying any of them.
    try:
        for perm in grants + revokes:
            auth_service.assert_can_grant(actor, target, perm)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    granted = set(target.granted_permissions or [])
    revoked = set(target.revoked_permissions or [])
    for perm in grants:
        granted.add(perm.value)
        revoked.discard(perm.value)
    for perm in revokes:
        revoked.add(perm.value)
        granted.discard(perm.value)

    # Reassign (not mutate) so SQLAlchemy flags the JSONB columns dirty.
    target.granted_permissions = sorted(granted)
    target.revoked_permissions = sorted(revoked)
    db.commit()
    db.refresh(target)
    audit_service.record(
        db, action="account.permissions", actor=actor, ip=_client_ip(request),
        target_type="account", target_id=target.id,
        detail={"grant": payload.grant, "revoke": payload.revoke},
    )
    return _to_out(target)
