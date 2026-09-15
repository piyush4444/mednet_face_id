"""
rbac_service.py — seed and read the DB-backed RBAC catalog.

The role/permission *vocabulary* still originates in code
(``auth_models``: :class:`Permission`, :class:`Role`, :data:`ROLE_DEFAULTS`)
— that remains the single definition. This service mirrors it into the DB
master tables (``role_master``, ``permission_master``,
``role_permission_mapping``) so that:

* the admin UI can list/manage roles and their permission bundles, and
* :func:`auth_service.effective_permissions` can source a role's default
  permissions from the DB instead of the in-code dict.

Seeding is idempotent and non-destructive:

* every :class:`Permission` / :class:`Role` value is upserted by ``code``;
* ``role_permission_mapping`` is seeded per role **only when that role has
  no mappings yet**, so operator edits made through the admin UI are never
  overwritten on the next boot.

Human role and permission assignments point directly to the canonical
``users`` row. Credentials are an optional one-to-one extension and service
accounts remain separate non-human principals.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.db.auth_models import ROLE_DEFAULTS, Permission, Role, ServiceAccount
from backend.app.db.identity_models import (
    PermissionMaster,
    RoleMaster,
    RolePermissionMapping,
)


def sync_catalog(db: Session) -> None:
    """Mirror the in-code Role/Permission vocabulary into the DB masters.

    Idempotent: safe to call on every boot. Adds anything missing; never
    deletes, and never re-seeds a role bundle that already has rows.
    """
    # ── Permissions ───────────────────────────────────────────────
    perm_by_code = {p.code: p for p in db.query(PermissionMaster).all()}
    new_permission_codes: set[str] = set()
    for perm in Permission:
        if perm.value not in perm_by_code:
            row = PermissionMaster(
                code=perm.value,
                name=perm.value.replace(".", " ").replace("_", " ").title(),
            )
            db.add(row)
            perm_by_code[perm.value] = row
            new_permission_codes.add(perm.value)

    # ── Roles ─────────────────────────────────────────────────────
    role_by_code = {r.code: r for r in db.query(RoleMaster).all()}
    for role in Role:
        if role.value not in role_by_code:
            row = RoleMaster(
                code=role.value,
                name=role.value.replace("_", " ").title(),
            )
            db.add(row)
            role_by_code[role.value] = row

    db.flush()  # assign ids to freshly added rows before mapping

    # Newly introduced admin capabilities must also reach existing role
    # catalogs; the normal first-time bundle seed intentionally skips roles
    # that operators may already have customised.
    for role in (Role.ADMIN, Role.SUPER_ADMIN):
        role_row = role_by_code[role.value]
        for perm in (
            Permission.ATTENDANCE_READ,
            Permission.ATTENDANCE_MANAGE,
            Permission.ATTENDANCE_RETRY,
        ):
            if perm.value not in new_permission_codes:
                continue
            permission_row = perm_by_code[perm.value]
            exists = db.query(RolePermissionMapping).filter(
                RolePermissionMapping.role_id == role_row.id,
                RolePermissionMapping.permission_id == permission_row.id,
            ).first()
            if exists is None:
                db.add(RolePermissionMapping(
                    role_id=role_row.id,
                    permission_id=permission_row.id,
                ))

    # One-time vocabulary rename for the single-facility product. Preserve
    # whichever roles previously held facilities.manage, then retire it.
    legacy = db.query(PermissionMaster).filter(
        PermissionMaster.code == "facilities.manage"
    ).first()
    replacement = perm_by_code.get(Permission.LOCATIONS_MANAGE.value)
    if legacy is not None and replacement is not None:
        legacy_links = db.query(RolePermissionMapping).filter(
            RolePermissionMapping.permission_id == legacy.id
        ).all()
        for link in legacy_links:
            exists = db.query(RolePermissionMapping).filter(
                RolePermissionMapping.role_id == link.role_id,
                RolePermissionMapping.permission_id == replacement.id,
            ).first()
            if exists is None:
                db.add(RolePermissionMapping(
                    role_id=link.role_id,
                    permission_id=replacement.id,
                ))
            db.delete(link)
        legacy.is_active = False

    # Kiosk is shelved on main. Retire its catalog entries and disable old
    # device principals so a preserved kiosk credential cannot retain stream
    # access after the routes and UI have been removed.
    for retired_code in ("kiosk.operate", "kiosk.manage"):
        retired = db.query(PermissionMaster).filter(
            PermissionMaster.code == retired_code
        ).first()
        if retired is None:
            continue
        db.query(RolePermissionMapping).filter(
            RolePermissionMapping.permission_id == retired.id
        ).delete(synchronize_session=False)
        retired.is_active = False
    db.query(ServiceAccount).filter(
        ServiceAccount.principal_type == "KIOSK"
    ).update({ServiceAccount.is_active: False}, synchronize_session=False)

    # SessionLocal disables autoflush, so persist any migration mappings before
    # deciding whether a role still needs its first-time default bundle.
    db.flush()

    # ── Role → permission bundles (first-time seed per role only) ──
    for role in Role:
        role_row = role_by_code[role.value]
        already = (
            db.query(RolePermissionMapping)
            .filter(RolePermissionMapping.role_id == role_row.id)
            .count()
        )
        if already:
            continue  # operator may have customised this bundle — leave it
        for perm in ROLE_DEFAULTS.get(role, frozenset()):
            db.add(
                RolePermissionMapping(
                    role_id=role_row.id,
                    permission_id=perm_by_code[perm.value].id,
                )
            )

    db.commit()


def role_permission_codes(db: Session, role_code: str) -> set[str]:
    """Return the permission code strings granted by ``role_code`` (DB)."""
    rows = (
        db.query(PermissionMaster.code)
        .join(
            RolePermissionMapping,
            RolePermissionMapping.permission_id == PermissionMaster.id,
        )
        .join(RoleMaster, RoleMaster.id == RolePermissionMapping.role_id)
        .filter(RoleMaster.code == role_code)
        .all()
    )
    return {code for (code,) in rows}
