"""Attendance observation, timing decisions, and transactional punch outbox."""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db.attendance_models import (
    AttendanceDailyState,
    AttendancePolicy,
    RecognitionObservation,
)
from backend.app.db.facility_models import (
    ExportStatus,
    PersonFacility,
    PersonTrackingLog,
    PunchExport,
    TrackingSource,
    VisitType,
)
from backend.app.db.models import User
from backend.app.services import facility_service, mapping_service
from backend.app.utils.time_ist import now_ist, to_ist

logger = logging.getLogger("backend.attendance")
POLICY_ID = 1


def get_or_create_policy(db: Session) -> AttendancePolicy:
    policy = db.get(AttendancePolicy, POLICY_ID)
    if policy is None:
        policy = AttendancePolicy(id=POLICY_ID)
        db.add(policy)
        db.flush()
    return policy


def policy_to_dict(policy: AttendancePolicy) -> dict:
    return {
        "id": policy.id,
        "enabled": policy.enabled,
        "shadow_mode": policy.shadow_mode,
        "timezone": policy.timezone,
        "in_window_start": policy.in_window_start.strftime("%H:%M"),
        "in_window_end": policy.in_window_end.strftime("%H:%M"),
        "out_window_start": policy.out_window_start.strftime("%H:%M"),
        "out_window_end": policy.out_window_end.strftime("%H:%M"),
        "minimum_work_minutes": policy.minimum_work_minutes,
        "observation_cooldown_seconds": policy.observation_cooldown_seconds,
        "eligible_user_types": list(policy.eligible_user_types or []),
        "attendance_camera_ids": list(policy.attendance_camera_ids or []),
        "camera_serial_numbers": dict(policy.camera_serial_numbers or {}),
        "biometric_server_ip": policy.biometric_server_ip or "",
        "version": policy.version,
        "updated_at": policy.updated_at,
    }


def validate_policy_values(values: dict) -> None:
    start_in = values["in_window_start"]
    end_in = values["in_window_end"]
    start_out = values["out_window_start"]
    end_out = values["out_window_end"]
    if not (start_in < end_in <= start_out < end_out):
        raise ValueError(
            "windows must be same-day and ordered: IN start < IN end <= "
            "OUT start < OUT end"
        )
    try:
        ZoneInfo(values["timezone"])
    except ZoneInfoNotFoundError as exc:
        raise ValueError("unknown IANA timezone") from exc
    if values["minimum_work_minutes"] < 0:
        raise ValueError("minimum_work_minutes must be non-negative")
    if not 5 <= values["observation_cooldown_seconds"] <= 3600:
        raise ValueError("observation_cooldown_seconds must be between 5 and 3600")
    camera_ids = values.get("attendance_camera_ids") or []
    serials = values.get("camera_serial_numbers") or {}
    missing = [camera_id for camera_id in camera_ids if not str(serials.get(camera_id, "")).strip()]
    if values.get("enabled") and missing:
        raise ValueError(f"serial number required for attendance cameras: {missing}")


def update_policy(db: Session, policy: AttendancePolicy, values: dict, *, actor_id: int) -> AttendancePolicy:
    validate_policy_values(values)
    for field, value in values.items():
        setattr(policy, field, value)
    policy.updated_by = actor_id
    policy.version = (policy.version or 0) + 1
    db.commit()
    db.refresh(policy)
    return policy


def _in_window(value, start, end) -> bool:
    return start <= value <= end


def _ensure_membership(db: Session, user: User) -> PersonFacility:
    facility = facility_service.get_single_facility(db)
    mapping = (
        db.query(PersonFacility)
        .filter(
            PersonFacility.person_id == user.id,
            PersonFacility.facility_id == facility.id,
        )
        .first()
    )
    if mapping is None:
        mapping = PersonFacility(
            person_id=user.id,
            facility_id=facility.id,
            person_type=user.user_type,
            mrn=user.mrn,
            visit_count=0,
            is_active=True,
        )
        db.add(mapping)
        db.flush()
        mapping_service.record_version(db, mapping, reason="attendance-observation")
    return mapping


def _payload(user: User, serial: str, direction: str, observed_at, server_ip: str) -> tuple[dict, str]:
    event_time = to_ist(observed_at)
    biometric_idx = (
        f"{serial}_{user.id}_{event_time.strftime('%Y-%m-%d')}_"
        f"{event_time.strftime('%H:%M:%S')}"
    )
    return {
        "serialNumber": serial,
        "biometricServerIP": server_ip or "",
        "biometricUID": user.id,
        "biometricUserID": user.id,
        "bioStatus": "0" if direction == VisitType.IN.value else "1",
        "biometricIDX": biometric_idx,
        "punchDate": event_time.strftime("%Y-%m-%d"),
        "punchTime": event_time.strftime("%H:%M:%S"),
    }, biometric_idx


def _queue_punch(
    db: Session,
    *,
    user: User,
    state: AttendanceDailyState,
    policy: AttendancePolicy,
    camera_id: str,
    observed_at: datetime,
    direction: str,
) -> PunchExport:
    serial = str((policy.camera_serial_numbers or {}).get(camera_id, "")).strip()
    if not serial:
        raise ValueError("attendance camera has no Mednet serial number")
    mapping = _ensure_membership(db, user)
    log = PersonTrackingLog(
        person_facility_id=mapping.id,
        person_facility_version=mapping.current_version,
        visit_type=direction,
        camera_id=camera_id,
        event_time=observed_at,
        visit_number=mapping.visit_count,
        source=TrackingSource.CAMERA.value,
    )
    db.add(log)
    db.flush()
    payload, biometric_idx = _payload(
        user, serial, direction, observed_at, policy.biometric_server_ip
    )
    row = PunchExport(
        visit_log_id=log.id,
        facility_id=mapping.facility_id,
        user_id=user.id,
        attendance_date=state.attendance_date,
        direction=direction,
        camera_id=camera_id,
        depends_on_id=state.in_export_id if direction == VisitType.OUT.value else None,
        policy_version=policy.version,
        biometric_idx=biometric_idx,
        bio_status=payload["bioStatus"],
        payload=payload,
        status=ExportStatus.PENDING.value,
    )
    db.add(row)
    db.flush()
    return row


def observe_recognition(
    db: Session,
    *,
    user_id: int,
    camera_id: str,
    confidence: float | None = None,
    observed_at: datetime | None = None,
) -> dict:
    """Record a throttled sighting and possibly enqueue one daily IN/OUT."""
    observed_at = to_ist(observed_at or now_ist())
    policy = get_or_create_policy(db)
    try:
        local_time = observed_at.astimezone(ZoneInfo(policy.timezone))
    except (ValueError, ZoneInfoNotFoundError):
        local_time = to_ist(observed_at)
    attendance_date = local_time.date()
    cooldown = max(5, int(policy.observation_cooldown_seconds or 60))
    bucket = int(local_time.timestamp()) // cooldown
    observation_key = f"{user_id}:{camera_id}:{bucket}"
    observation_id = db.execute(
        insert(RecognitionObservation)
        .values(
            observation_key=observation_key,
            user_id=user_id,
            camera_id=camera_id,
            observed_at=observed_at,
            attendance_date=attendance_date,
            confidence=confidence,
            outcome="LOG_ONLY",
            reason="ordinary_recognition",
            policy_version=policy.version,
        )
        .on_conflict_do_nothing(index_elements=["observation_key"])
        .returning(RecognitionObservation.id)
    ).scalar()

    user = db.get(User, user_id)
    outcome, reason = "LOG_ONLY", "ordinary_recognition"
    queued = None
    if user is None:
        outcome, reason = "IGNORED", "user_not_registered"
    elif not user.is_active:
        outcome, reason = "IGNORED", "user_inactive"
    elif not policy.enabled:
        outcome, reason = "LOG_ONLY", "attendance_disabled"
    elif user.user_type not in set(policy.eligible_user_types or []):
        outcome, reason = "LOG_ONLY", "user_type_not_eligible"
    elif camera_id not in set(policy.attendance_camera_ids or []):
        outcome, reason = "LOG_ONLY", "camera_not_enabled"
    else:
        db.execute(
            insert(AttendanceDailyState)
            .values(
                user_id=user.id,
                attendance_date=attendance_date,
                first_seen_at=observed_at,
                last_seen_at=observed_at,
                policy_version=policy.version,
            )
            .on_conflict_do_update(
                constraint="uq_attendance_user_date",
                set_={"last_seen_at": observed_at, "updated_at": now_ist()},
            )
        )
        state = (
            db.query(AttendanceDailyState)
            .filter(
                AttendanceDailyState.user_id == user.id,
                AttendanceDailyState.attendance_date == attendance_date,
            )
            .with_for_update()
            .one()
        )
        current_clock = local_time.time().replace(tzinfo=None)
        if _in_window(current_clock, policy.in_window_start, policy.in_window_end):
            if state.in_decided_at is not None:
                outcome, reason = "LOG_ONLY", "in_already_decided"
            elif policy.shadow_mode:
                state.in_decided_at = observed_at
                state.in_camera_id = camera_id
                state.in_shadow = True
                outcome, reason = "SHADOW_IN", "shadow_mode"
            else:
                queued = _queue_punch(
                    db, user=user, state=state, policy=policy,
                    camera_id=camera_id, observed_at=observed_at,
                    direction=VisitType.IN.value,
                )
                state.in_decided_at = observed_at
                state.in_camera_id = camera_id
                state.in_export_id = queued.id
                outcome, reason = "QUEUED_IN", "first_detection_in_window"
        elif _in_window(current_clock, policy.out_window_start, policy.out_window_end):
            if state.in_decided_at is None:
                outcome, reason = "LOG_ONLY", "out_requires_in"
            elif state.in_shadow:
                outcome, reason = "LOG_ONLY", "in_was_shadow_decision"
            elif state.out_decided_at is not None:
                outcome, reason = "LOG_ONLY", "out_already_decided"
            else:
                worked_minutes = (observed_at - state.in_decided_at).total_seconds() / 60
                if worked_minutes < policy.minimum_work_minutes:
                    outcome, reason = "LOG_ONLY", "minimum_work_duration_not_met"
                elif policy.shadow_mode:
                    state.out_decided_at = observed_at
                    state.out_camera_id = camera_id
                    state.out_shadow = True
                    outcome, reason = "SHADOW_OUT", "shadow_mode"
                else:
                    queued = _queue_punch(
                        db, user=user, state=state, policy=policy,
                        camera_id=camera_id, observed_at=observed_at,
                        direction=VisitType.OUT.value,
                    )
                    state.out_decided_at = observed_at
                    state.out_camera_id = camera_id
                    state.out_export_id = queued.id
                    outcome, reason = "QUEUED_OUT", "first_detection_out_window"
        else:
            outcome, reason = "LOG_ONLY", "outside_punch_windows"

    if observation_id is not None:
        observation = db.get(RecognitionObservation, observation_id)
        observation.outcome = outcome
        observation.reason = reason
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.info(
            "duplicate attendance decision suppressed user=%s date=%s",
            user_id,
            attendance_date,
        )
        return {"outcome": "LOG_ONLY", "reason": "concurrent_duplicate"}
    if queued is not None:
        from backend.app.services import export_worker

        export_worker.wake()
    return {
        "outcome": outcome,
        "reason": reason,
        "export_id": queued.id if queued is not None else None,
        "attendance_date": attendance_date.isoformat(),
    }
