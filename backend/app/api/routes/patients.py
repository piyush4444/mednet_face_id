"""
patients.py — Legacy patient-tracking endpoints.

**Deprecated as of the user-model expansion (Phase 8).** Every active
frontend caller has been migrated to ``/users/*`` (which serves all user
types under the unified model). This router is retained as a thin alias
so any external integration that still hits ``/patients/...`` continues
to work; new code MUST use ``/users/*``.

The aliased endpoints are scoped to ``user_type=PATIENT`` semantically —
they happen to return rows of any type today because they share the
same table, but the route name is patient-specific and should not be
relied on for non-patient lookups.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.db.postgres import get_db
from backend.app.services.patient_service import (
    search_patients,
    get_patient_status,
    get_active_patients,
)
from backend.app.services.relation_service import list_relations

router = APIRouter(tags=["patients"], prefix="/patients")


@router.get("/search")
def search(q: str, db: Session = Depends(get_db)):
    results = search_patients(db, q)
    return [
        {
            "id": p.id,
            "mrn": p.mrn,
            "name": p.name,
            "status": p.current_status,
            "floor": p.current_floor,
        }
        for p in results
    ]


@router.get("/active")
def active(db: Session = Depends(get_db)):
    patients = get_active_patients(db)
    return [
        {
            "id": p.id,
            "mrn": p.mrn,
            "name": p.name,
            "floor": p.current_floor,
            "camera_id": p.current_camera_id,
            "last_seen_at": p.last_seen_at,
        }
        for p in patients
    ]


def _serialize_patient(p):
    return {
        "id": p.id,
        "mrn": p.mrn,
        "name": p.name,
        "age": p.age,
        "gender": p.gender,
        "dob": p.dob.isoformat() if p.dob else None,
        "contact_number": p.contact_number,
        "address": p.address,
        "department": p.department,
        "doctor": p.doctor,
        "category": p.category,
        # User-model expansion fields — surfaced so the profile page
        # can render type-aware sections.
        "user_type": getattr(p, "user_type", "PATIENT") or "PATIENT",
        "role": getattr(p, "role", None),
        "specialty": getattr(p, "specialty", None),
        "staff_department": getattr(p, "staff_department", None),
        "opd_department_id": getattr(p, "opd_department_id", None),
        "opd_room_id": getattr(p, "opd_room_id", None),
        "purpose": getattr(p, "purpose", None),
        "note": getattr(p, "note", None),
        "is_active": getattr(p, "is_active", True),
        "status": p.current_status,
        "floor": p.current_floor,
        "camera_id": p.current_camera_id,
        "last_seen_at": p.last_seen_at,
    }


@router.get("/{patient_id}")
def get_patient(patient_id: int, db: Session = Depends(get_db)):
    """Full user profile by ID. The route is still named ``/patients``
    for backwards-compat; under the unified user model it returns any
    user type and embeds their relations."""
    p = get_patient_status(db, patient_id)
    if not p:
        raise HTTPException(status_code=404, detail="User not found")
    body = _serialize_patient(p)
    body["relations"] = list_relations(db, patient_id)
    return body


@router.get("/{patient_id}/status")
def status(patient_id: int, db: Session = Depends(get_db)):
    p = get_patient_status(db, patient_id)

    if not p:
        raise HTTPException(status_code=404, detail="Patient not found")

    return {
        "id": p.id,
        "mrn": p.mrn,
        "name": p.name,
        "status": p.current_status,
        "floor": p.current_floor,
        "camera_id": p.current_camera_id,
        "last_seen_at": p.last_seen_at,
    }
