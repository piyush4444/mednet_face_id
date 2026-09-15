"""
integrations.py — observability + manual control for outbound delivery.

The outbound punch / pre-registration queues drain automatically via the
background export worker. These endpoints let the admin console see the
health of that pipeline and nudge it:

GET  /integrations/status          Enabled flags + queue counts by status.
POST /integrations/flush           Ask the worker to sweep now.
POST /integrations/exports/{id}/retry   Reset a dead-lettered punch to retry.
POST /integrations/preregs/{id}/retry   Reset a dead-lettered pre-reg to retry.

Manual retries only reschedule a row (status→PENDING, next_retry cleared)
— they never send inline, so this endpoint can't be used to hammer the
client API. Gate these behind the metrics/admin permission when
feat/auth-rbac lands.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.db.facility_models import (
    ExportStatus,
    PreRegistrationLog,
    PunchExport,
)
from backend.app.db.postgres import get_db
from backend.app.core.config import settings
from backend.app.services import client_api, export_worker

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _counts(db: Session, model) -> dict:
    rows = (
        db.query(model.status, func.count(model.id))
        .group_by(model.status)
        .all()
    )
    out = {s.value: 0 for s in ExportStatus}
    for status, n in rows:
        out[status] = n
    return out


@router.get("/status")
def integration_status(db: Session = Depends(get_db)):
    return {
        "punch_api_enabled": client_api.punch_enabled(),
        "prereg_api_enabled": client_api.prereg_enabled(),
        "punch_queue": _counts(db, PunchExport),
        "prereg_queue": _counts(db, PreRegistrationLog),
    }


@router.post("/flush")
def flush():
    """Wake the export worker for an immediate sweep (no-op if dormant)."""
    export_worker.wake()
    return {"ok": True}


def _reset_for_retry(db: Session, model, row_id: int):
    row = db.query(model).filter(model.id == row_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} {row_id} not found")
    if row.status == ExportStatus.SENT.value:
        raise HTTPException(status_code=409, detail="already sent")
    row.status = ExportStatus.PENDING.value
    row.attempts = (
        min(row.attempts or 0, max(0, settings.EXPORT_MAX_ATTEMPTS - 1))
        if model is PunchExport
        else 0
    )
    row.next_retry_at = None
    row.last_error = None
    db.commit()
    export_worker.wake()
    return {"ok": True, "id": row_id, "status": row.status}


@router.post("/exports/{export_id}/retry")
def retry_export(export_id: int, db: Session = Depends(get_db)):
    return _reset_for_retry(db, PunchExport, export_id)


@router.post("/preregs/{prereg_id}/retry")
def retry_prereg(prereg_id: int, db: Session = Depends(get_db)):
    return _reset_for_retry(db, PreRegistrationLog, prereg_id)
