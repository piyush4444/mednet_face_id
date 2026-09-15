"""Attendance policy, decision audit, and durable Mednet punch queue."""

from __future__ import annotations

from datetime import date, time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.core.deps import get_optional_account, require_permission
from backend.app.db.attendance_models import (
    AttendanceDailyState,
    PunchDeliveryAttempt,
    RecognitionObservation,
)
from backend.app.db.auth_models import Permission
from backend.app.db.facility_models import Camera, ExportStatus, PunchExport
from backend.app.db.models import User, UserType
from backend.app.db.postgres import get_db
from backend.app.services import attendance_service, audit_service, export_worker
from backend.app.utils.time_ist import now_ist

router = APIRouter(prefix="/attendance", tags=["attendance"])


class PolicyUpdate(BaseModel):
    enabled: bool
    shadow_mode: bool
    timezone: str = Field(min_length=1, max_length=64)
    in_window_start: time
    in_window_end: time
    out_window_start: time
    out_window_end: time
    minimum_work_minutes: int = Field(ge=0, le=1440)
    observation_cooldown_seconds: int = Field(ge=5, le=3600)
    eligible_user_types: list[UserType]
    attendance_camera_ids: list[str]
    camera_serial_numbers: dict[str, str]
    biometric_server_ip: str = Field(default="", max_length=64)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _camera_options(db: Session) -> list[dict]:
    rows = db.query(Camera).order_by(Camera.name.asc()).all()
    return [
        {
            "id": row.code,
            "name": row.name,
            "active": row.is_active,
            "location_id": row.location_id,
        }
        for row in rows
    ]


@router.get("/config")
def get_config(db: Session = Depends(get_db)):
    policy = attendance_service.get_or_create_policy(db)
    db.commit()
    return {
        "policy": attendance_service.policy_to_dict(policy),
        "cameras": _camera_options(db),
        "user_types": [UserType.EMPLOYEE.value, UserType.DOCTOR.value],
    }


@router.put(
    "/config",
    dependencies=[Depends(require_permission(Permission.ATTENDANCE_MANAGE))],
)
def save_config(
    payload: PolicyUpdate,
    request: Request,
    account=Depends(get_optional_account),
    db: Session = Depends(get_db),
):
    policy = attendance_service.get_or_create_policy(db)
    values = payload.model_dump()
    values["eligible_user_types"] = [value.value for value in payload.eligible_user_types]
    values["attendance_camera_ids"] = list(dict.fromkeys(payload.attendance_camera_ids))
    values["camera_serial_numbers"] = {
        key: value.strip() for key, value in payload.camera_serial_numbers.items()
    }
    actor_id = (
        getattr(account, "id", None)
        if getattr(account, "kind", None) == "human"
        else None
    )
    if actor_id is None:
        actor_id = policy.updated_by
    try:
        policy = attendance_service.update_policy(
            db, policy, values, actor_id=actor_id
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_service.record(
        db,
        action="attendance.policy.update",
        actor=account,
        ip=_client_ip(request),
        target_type="attendance_policy",
        target_id=policy.id,
        detail={
            "version": policy.version,
            "enabled": policy.enabled,
            "shadow_mode": policy.shadow_mode,
            "attendance_camera_ids": policy.attendance_camera_ids,
        },
    )
    return attendance_service.policy_to_dict(policy)


@router.get("/dashboard")
def dashboard(
    attendance_date: date | None = Query(default=None, alias="date"),
    db: Session = Depends(get_db),
):
    target = attendance_date or now_ist().date()
    states = (
        db.query(AttendanceDailyState, User)
        .join(User, User.id == AttendanceDailyState.user_id)
        .filter(AttendanceDailyState.attendance_date == target)
        .order_by(AttendanceDailyState.first_seen_at.desc())
        .all()
    )
    queue_counts = dict(
        db.query(PunchExport.status, func.count(PunchExport.id))
        .filter(PunchExport.attendance_date == target)
        .group_by(PunchExport.status)
        .all()
    )
    return {
        "date": target,
        "summary": {
            "recognized": len(states),
            "in_decided": sum(state.in_decided_at is not None for state, _ in states),
            "out_decided": sum(state.out_decided_at is not None for state, _ in states),
            "queued": queue_counts,
        },
        "rows": [
            {
                "id": state.id,
                "user_id": user.id,
                "name": user.name,
                "user_type": user.user_type,
                "first_seen_at": state.first_seen_at,
                "last_seen_at": state.last_seen_at,
                "in_decided_at": state.in_decided_at,
                "out_decided_at": state.out_decided_at,
                "in_shadow": state.in_shadow,
                "out_shadow": state.out_shadow,
                "in_export_id": state.in_export_id,
                "out_export_id": state.out_export_id,
            }
            for state, user in states
        ],
    }


@router.get("/observations")
def observations(
    attendance_date: date | None = Query(default=None, alias="date"),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = (
        db.query(RecognitionObservation, User)
        .join(User, User.id == RecognitionObservation.user_id)
    )
    if attendance_date:
        query = query.filter(RecognitionObservation.attendance_date == attendance_date)
    rows = query.order_by(RecognitionObservation.observed_at.desc()).limit(limit).all()
    return [
        {
            "id": row.id,
            "user_id": user.id,
            "name": user.name,
            "camera_id": row.camera_id,
            "observed_at": row.observed_at,
            "confidence": row.confidence,
            "outcome": row.outcome,
            "reason": row.reason,
            "policy_version": row.policy_version,
        }
        for row, user in rows
    ]


@router.get("/queue")
def queue(
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = db.query(PunchExport, User).outerjoin(User, User.id == PunchExport.user_id)
    if status:
        query = query.filter(PunchExport.status == status.upper())
    rows = query.order_by(PunchExport.created_at.desc()).limit(limit).all()
    return [
        {
            "id": row.id,
            "user_id": row.user_id,
            "name": user.name if user else None,
            "attendance_date": row.attendance_date,
            "direction": row.direction,
            "camera_id": row.camera_id,
            "status": row.status,
            "attempts": row.attempts,
            "next_retry_at": row.next_retry_at,
            "sent_at": row.sent_at,
            "last_error": row.last_error,
            "last_latency_ms": row.last_latency_ms,
            "depends_on_id": row.depends_on_id,
            "biometric_idx": row.biometric_idx,
            "created_at": row.created_at,
        }
        for row, user in rows
    ]


@router.get("/queue/{export_id}/attempts")
def attempts(export_id: int, db: Session = Depends(get_db)):
    rows = (
        db.query(PunchDeliveryAttempt)
        .filter(PunchDeliveryAttempt.export_id == export_id)
        .order_by(PunchDeliveryAttempt.attempt_number.desc())
        .all()
    )
    return [
        {
            "id": row.id,
            "attempt_number": row.attempt_number,
            "attempted_at": row.attempted_at,
            "completed_at": row.completed_at,
            "outcome": row.outcome,
            "http_status": row.http_status,
            "latency_ms": row.latency_ms,
            "error_category": row.error_category,
            "error_detail": row.error_detail,
        }
        for row in rows
    ]


@router.post(
    "/queue/{export_id}/retry",
    dependencies=[Depends(require_permission(Permission.ATTENDANCE_RETRY))],
)
def retry(
    export_id: int,
    request: Request,
    account=Depends(get_optional_account),
    db: Session = Depends(get_db),
):
    row = db.get(PunchExport, export_id)
    if row is None:
        raise HTTPException(status_code=404, detail="punch not found")
    if row.status == ExportStatus.SENT.value:
        raise HTTPException(status_code=409, detail="punch already sent")
    row.status = ExportStatus.PENDING.value
    # Open one new delivery slot while preserving immutable attempt history.
    row.attempts = min(
        row.attempts or 0,
        max(0, settings.EXPORT_MAX_ATTEMPTS - 1),
    )
    row.next_retry_at = None
    row.last_error = None
    db.commit()
    audit_service.record(
        db,
        action="attendance.punch.retry",
        actor=account,
        ip=_client_ip(request),
        target_type="punch_export_sync",
        target_id=row.id,
        detail={"direction": row.direction, "attendance_date": str(row.attendance_date)},
    )
    export_worker.wake()
    return {"ok": True, "id": row.id, "status": row.status}
