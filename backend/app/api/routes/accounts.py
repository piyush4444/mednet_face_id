"""Grant application access to canonical employee/doctor users."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.core.deps import get_current_account, require_permission, verify_csrf
from backend.app.db.auth_models import Permission, Role, UserCredential
from backend.app.db.identity_models import (
    PermissionMaster,
    RoleMaster,
    UserPermissionMapping,
    UserRoleMapping,
)
from backend.app.db.models import User, UserType
from backend.app.db.postgres import get_db
from backend.app.services import audit_service, auth_service

router = APIRouter(
    prefix="/auth/accounts",
    tags=["accounts"],
    dependencies=[Depends(require_permission(Permission.ACCOUNTS_MANAGE_STAFF))],
)


class AccountOut(BaseModel):
    id: int
    user_id: int
    name: str
    user_type: str
    username: str
    role: str
    is_active: bool
    granted_permissions: list[str]
    revoked_permissions: list[str]
    effective_permissions: list[str]


class CandidateOut(BaseModel):
    id: int
    name: str
    user_type: str
    employee_role: str | None


class CreateAccountIn(BaseModel):
    user_id: int
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    role: str


class UpdateAccountIn(BaseModel):
    role: str | None = None
    is_active: bool | None = None


class PermissionsPatchIn(BaseModel):
    grant: list[str] = Field(default_factory=list)
    revoke: list[str] = Field(default_factory=list)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _parse_role(raw: str) -> Role:
    try:
        return Role(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid role {raw!r}")


def _parse_permission(raw: str) -> Permission:
    try:
        return Permission(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid permission {raw!r}")


def _target(db: Session, user_id: int) -> auth_service.AuthPrincipal:
    target = auth_service.get_account(db, user_id, "human")
    if target is None:
        raise HTTPException(status_code=404, detail="user access not found")
    return target


def _to_out(principal: auth_service.AuthPrincipal, db: Session) -> AccountOut:
    return AccountOut(
        id=principal.id,
        user_id=principal.id,
        name=principal.name or principal.username,
        user_type=principal.user_type or "EMPLOYEE",
        username=principal.username,
        role=principal.role,
        is_active=principal.is_active,
        granted_permissions=list(principal.granted_permissions),
        revoked_permissions=list(principal.revoked_permissions),
        effective_permissions=sorted(
            p.value for p in auth_service.effective_permissions(principal, db)
        ),
    )


def _can_assign(actor: auth_service.AuthPrincipal, role: Role, db: Session) -> None:
    if role in {Role.ADMIN, Role.SUPER_ADMIN} and not auth_service.has_permission(
        actor, Permission.ACCOUNTS_MANAGE_ALL, db
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only a superadmin may assign that role",
        )


@router.get("", response_model=list[AccountOut])
def list_accounts(
    actor: auth_service.AuthPrincipal = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    credentials = db.query(UserCredential).order_by(UserCredential.id).all()
    principals = [
        auth_service.get_account(db, credential.user_id, "human")
        for credential in credentials
    ]
    if not auth_service.has_permission(actor, Permission.ACCOUNTS_MANAGE_ALL, db):
        principals = [p for p in principals if p and p.role == Role.STAFF.value]
    return [_to_out(p, db) for p in principals if p]


@router.get("/candidates", response_model=list[CandidateOut])
def access_candidates(
    _actor: auth_service.AuthPrincipal = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(User)
        .outerjoin(UserCredential, UserCredential.user_id == User.id)
        .filter(
            User.is_active.is_(True),
            User.user_type.in_([UserType.EMPLOYEE.value, UserType.DOCTOR.value]),
            UserCredential.id.is_(None),
        )
        .order_by(User.name)
        .all()
    )
    return [
        CandidateOut(
            id=user.id,
            name=user.name,
            user_type=user.user_type,
            employee_role=user.role,
        )
        for user in rows
    ]


@router.post("", response_model=AccountOut, status_code=201,
             dependencies=[Depends(verify_csrf)])
def create_account_endpoint(
    payload: CreateAccountIn,
    request: Request,
    actor: auth_service.AuthPrincipal = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    role = _parse_role(payload.role)
    _can_assign(actor, role, db)
    try:
        principal = auth_service.create_account(
            db,
            user_id=payload.user_id,
            username=payload.username,
            password=payload.password,
            role=role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    audit_service.record(
        db,
        action="account.create",
        actor=actor,
        ip=_client_ip(request),
        target_type="user",
        target_id=principal.id,
        detail={"username": principal.username, "role": principal.role},
    )
    return _to_out(principal, db)


@router.patch("/{user_id}", response_model=AccountOut,
              dependencies=[Depends(verify_csrf)])
def update_account_endpoint(
    user_id: int,
    payload: UpdateAccountIn,
    request: Request,
    actor: auth_service.AuthPrincipal = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    target = _target(db, user_id)
    try:
        auth_service.assert_can_manage(actor, target, db)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    if payload.role is not None:
        role = _parse_role(payload.role)
        _can_assign(actor, role, db)
        role_row = db.query(RoleMaster).filter(RoleMaster.code == role.value).one()
        mapping = db.query(UserRoleMapping).filter(UserRoleMapping.user_id == user_id).one()
        mapping.role_id = role_row.id
    if payload.is_active is not None:
        credential = (
            db.query(UserCredential).filter(UserCredential.user_id == user_id).one()
        )
        credential.is_active = payload.is_active
    db.commit()
    updated = _target(db, user_id)
    audit_service.record(
        db,
        action="account.update",
        actor=actor,
        ip=_client_ip(request),
        target_type="user",
        target_id=user_id,
        detail={"role": updated.role, "is_active": updated.is_active},
    )
    return _to_out(updated, db)


@router.patch("/{user_id}/permissions", response_model=AccountOut,
              dependencies=[Depends(verify_csrf)])
def patch_permissions_endpoint(
    user_id: int,
    payload: PermissionsPatchIn,
    request: Request,
    actor: auth_service.AuthPrincipal = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    target = _target(db, user_id)
    changes = [(_parse_permission(p), "GRANT") for p in payload.grant]
    changes += [(_parse_permission(p), "REVOKE") for p in payload.revoke]
    try:
        for permission, _effect in changes:
            auth_service.assert_can_grant(actor, target, permission, db)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    for permission, effect in changes:
        permission_row = (
            db.query(PermissionMaster)
            .filter(PermissionMaster.code == permission.value)
            .one()
        )
        row = (
            db.query(UserPermissionMapping)
            .filter(
                UserPermissionMapping.user_id == user_id,
                UserPermissionMapping.permission_id == permission_row.id,
            )
            .first()
        )
        if row is None:
            row = UserPermissionMapping(
                user_id=user_id, permission_id=permission_row.id, effect=effect
            )
            db.add(row)
        else:
            row.effect = effect
    db.commit()
    updated = _target(db, user_id)
    audit_service.record(
        db,
        action="account.permissions",
        actor=actor,
        ip=_client_ip(request),
        target_type="user",
        target_id=user_id,
        detail={"grant": payload.grant, "revoke": payload.revoke},
    )
    return _to_out(updated, db)
