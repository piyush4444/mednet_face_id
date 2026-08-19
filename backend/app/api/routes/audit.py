"""
audit.py — read the audit trail (audit.read permission).

Mounted at /audit and guarded in router.py. Read-only: the log is
append-only and there is deliberately no delete/edit surface.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.db.postgres import get_db
from backend.app.services import audit_service

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditRow(BaseModel):
    id: int
    ts: str
    account_id: int | None
    username: str | None
    role: str | None
    action: str
    target_type: str | None
    target_id: str | None
    ip: str | None
    detail: dict | None


@router.get("", response_model=list[AuditRow])
def list_audit(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    action: str | None = Query(None, description="Exact action filter, e.g. auth.login"),
    db: Session = Depends(get_db),
):
    rows = audit_service.query(db, limit=limit, offset=offset, action=action)
    return [
        AuditRow(
            id=r.id,
            ts=r.ts.isoformat() if r.ts else "",
            account_id=r.account_id,
            username=r.username,
            role=r.role,
            action=r.action,
            target_type=r.target_type,
            target_id=r.target_id,
            ip=r.ip,
            detail=r.detail,
        )
        for r in rows
    ]
