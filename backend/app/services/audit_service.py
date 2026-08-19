"""
audit_service.py — write + query the audit trail.

``record()`` is defensive: an audit write must never break the request it is
recording, so failures are swallowed (logged) rather than raised. Callers
pass the actor account (or None for pre-auth events like a failed login).
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from backend.app.db.audit_models import AuditLog

logger = logging.getLogger("backend.audit")


def record(
    db: Session,
    *,
    action: str,
    actor=None,
    username: str | None = None,
    target_type: str | None = None,
    target_id=None,
    ip: str | None = None,
    detail: dict | None = None,
) -> None:
    """Append one audit row. Never raises."""
    try:
        row = AuditLog(
            account_id=getattr(actor, "id", None),
            username=username if username is not None else getattr(actor, "username", None),
            role=getattr(actor, "role", None),
            action=action,
            target_type=target_type,
            target_id=None if target_id is None else str(target_id),
            ip=ip,
            detail=detail,
        )
        db.add(row)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit write failed for %s: %s", action, exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


def query(
    db: Session,
    *,
    limit: int = 100,
    offset: int = 0,
    action: str | None = None,
) -> list[AuditLog]:
    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    return (
        q.order_by(AuditLog.ts.desc())
        .offset(max(0, offset))
        .limit(max(1, min(limit, 500)))
        .all()
    )
