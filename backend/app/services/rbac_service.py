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

Note (round 3.1): the *per-facility* principal side —
``user_role_mapping`` keyed to ``user_auth`` — is wired in the later auth
migration phase. Today the login principal is still ``StaffAccount`` and its
single ``role`` string selects the bundle; this service only moves the
role→permission bundle into the DB.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.db.auth_models import ROLE_DEFAULTS, Permission, Role
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
    for perm in Permission:
        if perm.value not in perm_by_code:
            row = PermissionMaster(
                code=perm.value,
                name=perm.value.replace(".", " ").replace("_", " ").title(),
            )
            db.add(row)
            perm_by_code[perm.value] = row

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
