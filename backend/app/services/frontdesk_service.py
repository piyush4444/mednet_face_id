"""
frontdesk_service.py — business logic for the front-desk OPD workflow.

Responsibilities
----------------
- Scan a webcam frame and resolve it to a patient (wraps recognize_faces).
- Create an ``OPDVisit`` row, allocate a token, resolve a room, snapshot
  human-readable names so history stays readable after renames.
- List today's queue (for the live front-desk panel).
- Update visit status (WAITING -> IN_CONSULT -> DONE).

Design
------
The face recognition layer already returns ``user_id`` from /recognize.
This service consumes that id; the scan endpoint itself is a thin wrapper
that just calls ``recognize_faces`` and reformats the response.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_
from sqlalchemy.orm import Session

from backend.app.db.frontdesk_models import (
    Department,
    OPDVisit,
    Room,
    VisitStatusHistory,
)
from backend.app.db.models import Patient, User, UserType
from backend.app.services.token_service import allocate_token
from backend.app.utils.time_ist import IST, now_ist

logger = logging.getLogger("backend.frontdesk")


VISIT_TYPES = {"GENERAL", "SPECIALIST", "DOCTOR"}
VISIT_STATUSES = {"WAITING", "IN_CONSULT", "DONE", "CANCELLED"}

# Reissue (instead of creating a new visit) if the same patient is
# scanned again within this window.
DUPLICATE_SCAN_WINDOW_SECONDS = 30


class FrontDeskError(Exception):
    """Base class for front-desk service errors."""


class PatientNotFoundError(FrontDeskError):
    pass


class InvalidVisitPayloadError(FrontDeskError):
    pass


# ── Room resolution ──────────────────────────────────────────────────────
def _resolve_room(
    db: Session,
    department: Department,
    doctor: Optional[User],
    explicit_room_id: Optional[int],
) -> Optional[Room]:
    """
    Pick the room for a visit. Priority:
        1. explicit_room_id (operator override)
        2. doctor.opd_room_id
        3. department.default_room
    """
    if explicit_room_id is not None:
        room = db.query(Room).filter(Room.id == explicit_room_id).first()
        if room:
            return room

    if doctor and doctor.opd_room_id is not None:
        room = db.query(Room).filter(Room.id == doctor.opd_room_id).first()
        if room:
            return room

    if department.default_room_id is not None:
        room = db.query(Room).filter(Room.id == department.default_room_id).first()
        if room:
            return room

    return None


# ── Recent-duplicate check ───────────────────────────────────────────────
def _recent_visit_for_patient(
    db: Session, patient_id: int, window_seconds: int
) -> Optional[OPDVisit]:
    cutoff = now_ist() - timedelta(seconds=window_seconds)
    return (
        db.query(OPDVisit)
        .filter(
            OPDVisit.patient_id == patient_id,
            OPDVisit.created_at >= cutoff,
            OPDVisit.status.in_(["WAITING", "IN_CONSULT"]),
        )
        .order_by(OPDVisit.created_at.desc())
        .first()
    )


# ── Public: create a visit ───────────────────────────────────────────────
def create_visit(
    db: Session,
    *,
    patient_id: int,
    visit_type: str,
    department_id: int,
    doctor_id: Optional[int] = None,
    room_id: Optional[int] = None,
    note: Optional[str] = None,
    source: str = "FACE_SCAN",
    created_by: Optional[str] = None,
) -> OPDVisit:
    """
    Allocate a token and persist an OPDVisit row.

    Idempotent within DUPLICATE_SCAN_WINDOW_SECONDS for the same patient —
    a repeat scan returns the existing active visit rather than creating
    a new one (operator can still hit "force new" by passing a different
    visit_type/department).
    """
    if visit_type not in VISIT_TYPES:
        raise InvalidVisitPayloadError(f"invalid visit_type: {visit_type!r}")

    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if patient is None:
        raise PatientNotFoundError(f"patient id={patient_id} not found")

    department = db.query(Department).filter(Department.id == department_id).first()
    if department is None or not department.is_active:
        raise InvalidVisitPayloadError(f"department id={department_id} not found or inactive")

    doctor: Optional[User] = None
    if doctor_id is not None:
        doctor = (
            db.query(User)
            .filter(
                User.id == doctor_id,
                User.user_type == UserType.DOCTOR.value,
            )
            .first()
        )
        if doctor is None or not doctor.is_active:
            raise InvalidVisitPayloadError(f"doctor id={doctor_id} not found or inactive")
        if doctor.opd_department_id != department.id:
            raise InvalidVisitPayloadError(
                "doctor does not belong to the chosen department"
            )

    if visit_type == "DOCTOR" and doctor is None:
        raise InvalidVisitPayloadError("DOCTOR visit_type requires doctor_id")

    # Idempotent reuse for accidental double-scans of the same patient.
    existing = _recent_visit_for_patient(
        db, patient.id, DUPLICATE_SCAN_WINDOW_SECONDS
    )
    if existing is not None and existing.department_id == department.id:
        logger.info(
            "Reusing recent visit id=%d for patient_id=%d within %ds window",
            existing.id, patient.id, DUPLICATE_SCAN_WINDOW_SECONDS,
        )
        return existing

    room = _resolve_room(db, department, doctor, room_id)

    token_number = allocate_token(db, department)

    visit = OPDVisit(
        patient_id=patient.id,
        token_number=token_number,
        visit_type=visit_type,
        department_id=department.id,
        doctor_id=doctor.id if doctor else None,
        room_id=room.id if room else None,
        department_name=department.name,
        doctor_name=doctor.name if doctor else None,
        room_name=room.name if room else None,
        note=note,
        status="WAITING",
        source=source,
        created_by=created_by,
    )
    db.add(visit)
    db.commit()
    db.refresh(visit)

    # Log the initial WAITING transition.
    db.add(
        VisitStatusHistory(
            visit_id=visit.id,
            from_status=None,
            to_status="WAITING",
            changed_by=created_by,
        )
    )
    db.commit()

    logger.info(
        "Created OPD visit id=%d token=%s patient_id=%d dept=%s room=%s",
        visit.id, visit.token_number, patient.id,
        department.name, room.name if room else "-",
    )
    return visit


# ── Public: status updates ───────────────────────────────────────────────
def update_visit_status(
    db: Session,
    visit_id: int,
    new_status: str,
    *,
    changed_by: Optional[str] = None,
) -> OPDVisit:
    if new_status not in VISIT_STATUSES:
        raise InvalidVisitPayloadError(f"invalid status: {new_status!r}")
    visit = db.query(OPDVisit).filter(OPDVisit.id == visit_id).first()
    if visit is None:
        raise PatientNotFoundError(f"visit id={visit_id} not found")

    old_status = visit.status
    if old_status == new_status:
        return visit  # no-op, don't pollute the history log

    visit.status = new_status
    db.add(
        VisitStatusHistory(
            visit_id=visit.id,
            from_status=old_status,
            to_status=new_status,
            changed_by=changed_by,
        )
    )
    db.commit()
    db.refresh(visit)
    return visit


def list_visit_history(db: Session, visit_id: int) -> List[VisitStatusHistory]:
    return (
        db.query(VisitStatusHistory)
        .filter(VisitStatusHistory.visit_id == visit_id)
        .order_by(VisitStatusHistory.changed_at.asc())
        .all()
    )


def history_to_dict(h: VisitStatusHistory) -> Dict[str, Any]:
    return {
        "id": h.id,
        "visit_id": h.visit_id,
        "from_status": h.from_status,
        "to_status": h.to_status,
        "changed_at": h.changed_at.isoformat() if h.changed_at else None,
        "changed_by": h.changed_by,
    }


def mark_printed(db: Session, visit_id: int) -> OPDVisit:
    visit = db.query(OPDVisit).filter(OPDVisit.id == visit_id).first()
    if visit is None:
        raise PatientNotFoundError(f"visit id={visit_id} not found")
    visit.printed_at = now_ist()
    db.commit()
    db.refresh(visit)
    return visit


# ── Public: queries ──────────────────────────────────────────────────────
def _today_bounds():
    today = now_ist().date()
    start = datetime.combine(today, time.min, tzinfo=IST)
    end = datetime.combine(today, time.max, tzinfo=IST)
    return start, end


def list_visits_for_date(db: Session, date_iso: str) -> List[OPDVisit]:
    """List every OPDVisit whose created_at falls on ``date_iso`` (YYYY-MM-DD)."""
    from datetime import date as _date

    try:
        d = _date.fromisoformat(date_iso)
    except ValueError as exc:
        raise InvalidVisitPayloadError(f"invalid date: {date_iso!r}") from exc

    start = datetime.combine(d, time.min, tzinfo=IST)
    end = datetime.combine(d, time.max, tzinfo=IST)
    return (
        db.query(OPDVisit)
        .filter(and_(OPDVisit.created_at >= start, OPDVisit.created_at <= end))
        .order_by(OPDVisit.created_at.asc())
        .all()
    )


def list_patients_with_visits_on(
    db: Session, date_iso: str
) -> List[Dict[str, Any]]:
    """
    Distinct patients who had at least one OPD visit on ``date_iso``,
    annotated with visit count + last visit time so the History page can
    list them next to session-based patients.
    """
    visits = list_visits_for_date(db, date_iso)
    by_patient: Dict[int, Dict[str, Any]] = {}
    for v in visits:
        row = by_patient.get(v.patient_id)
        if row is None:
            patient = db.query(Patient).filter(Patient.id == v.patient_id).first()
            row = {
                "patient_id": v.patient_id,
                "name": patient.name if patient else None,
                "mrn": patient.mrn if patient else None,
                "user_type": getattr(patient, "user_type", "PATIENT") if patient else "PATIENT",
                "visits": 0,
                "last_visit_at": None,
            }
            by_patient[v.patient_id] = row
        row["visits"] += 1
        if (
            row["last_visit_at"] is None
            or (v.created_at and v.created_at.isoformat() > row["last_visit_at"])
        ):
            row["last_visit_at"] = v.created_at.isoformat() if v.created_at else None
    return list(by_patient.values())


def list_today_visits(
    db: Session,
    *,
    department_id: Optional[int] = None,
    status: Optional[str] = None,
) -> List[OPDVisit]:
    start, end = _today_bounds()
    q = db.query(OPDVisit).filter(
        and_(OPDVisit.created_at >= start, OPDVisit.created_at <= end)
    )
    if department_id is not None:
        q = q.filter(OPDVisit.department_id == department_id)
    if status is not None:
        if status not in VISIT_STATUSES:
            raise InvalidVisitPayloadError(f"invalid status: {status!r}")
        q = q.filter(OPDVisit.status == status)
    return q.order_by(OPDVisit.created_at.asc()).all()


def search_patients(db: Session, query: str, limit: int = 10) -> List[Patient]:
    """
    Manual fallback when face scan fails.

    Matches in priority order: exact MRN, exact phone, then name LIKE.
    Results are de-duplicated and capped at ``limit``.
    """
    q = (query or "").strip()
    if not q:
        return []

    seen: set[int] = set()
    out: List[Patient] = []

    def _append(rows: List[Patient]):
        for p in rows:
            if p.id in seen:
                continue
            seen.add(p.id)
            out.append(p)
            if len(out) >= limit:
                break

    _append(db.query(Patient).filter(Patient.mrn == q).limit(limit).all())
    if len(out) >= limit:
        return out

    _append(
        db.query(Patient).filter(Patient.contact_number == q).limit(limit).all()
    )
    if len(out) >= limit:
        return out

    like = f"%{q}%"
    _append(
        db.query(Patient)
        .filter(Patient.name.ilike(like))
        .order_by(Patient.name.asc())
        .limit(limit)
        .all()
    )
    return out


def list_patient_visits(
    db: Session, patient_id: int, limit: int = 50
) -> List[OPDVisit]:
    return (
        db.query(OPDVisit)
        .filter(OPDVisit.patient_id == patient_id)
        .order_by(OPDVisit.created_at.desc())
        .limit(limit)
        .all()
    )


# ── Public: serialization ────────────────────────────────────────────────
def visit_to_dict(visit: OPDVisit) -> Dict[str, Any]:
    return {
        "id": visit.id,
        "patient_id": visit.patient_id,
        "token_number": visit.token_number,
        "visit_type": visit.visit_type,
        "department_id": visit.department_id,
        "department_name": visit.department_name,
        "doctor_id": visit.doctor_id,
        "doctor_name": visit.doctor_name,
        "room_id": visit.room_id,
        "room_name": visit.room_name,
        "note": visit.note,
        "status": visit.status,
        "source": visit.source,
        "created_by": visit.created_by,
        "created_at": visit.created_at.isoformat() if visit.created_at else None,
        "updated_at": visit.updated_at.isoformat() if visit.updated_at else None,
        "printed_at": visit.printed_at.isoformat() if visit.printed_at else None,
    }


def slip_payload(visit: OPDVisit, patient: Patient) -> Dict[str, Any]:
    """Payload for the printable slip — denormalized, ready to render."""
    return {
        "visit": visit_to_dict(visit),
        "patient": {
            "id": patient.id,
            "mrn": patient.mrn,
            "name": patient.name,
            "age": patient.age,
            "gender": patient.gender,
            "contact_number": patient.contact_number,
        },
    }
