"""
frontdesk.py — HTTP endpoints for the OPD front-desk workflow.

Routes
------
POST /frontdesk/scan                Scan a webcam frame, resolve to patient
POST /frontdesk/visits              Create a visit + allocate token
GET  /frontdesk/visits/today        Today's queue (filter by dept/status)
GET  /frontdesk/visits/{id}         Single visit
PATCH /frontdesk/visits/{id}        Update status
POST /frontdesk/visits/{id}/reprint Re-fetch slip payload + mark printed
GET  /frontdesk/patients/{id}/visits History of visits for a patient
"""

from __future__ import annotations

import logging
from typing import Optional

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.db.models import Patient
from backend.app.db.postgres import get_db
from backend.app.services import frontdesk_service
from backend.app.services.face_service import recognize_faces
from backend.app.services.frontdesk_service import (
    FrontDeskError,
    InvalidVisitPayloadError,
    PatientNotFoundError,
)

logger = logging.getLogger("backend.frontdesk.routes")

router = APIRouter(prefix="/frontdesk", tags=["frontdesk"])


# ── Schemas ──────────────────────────────────────────────────────────────
class CreateVisitIn(BaseModel):
    patient_id: int
    visit_type: str  # GENERAL | SPECIALIST | DOCTOR
    department_id: int
    doctor_id: Optional[int] = None
    room_id: Optional[int] = None
    note: Optional[str] = None
    source: Optional[str] = "FACE_SCAN"
    created_by: Optional[str] = None


class UpdateVisitIn(BaseModel):
    status: str  # WAITING | IN_CONSULT | DONE | CANCELLED


# ── Helpers ──────────────────────────────────────────────────────────────
def _decode_image(data: bytes) -> np.ndarray:
    arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="Could not decode image")
    return img


def _patient_summary(p: Patient) -> dict:
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
        # User-model expansion fields — let the front desk discriminate
        # between patient (eligible for OPD token) and other user types.
        "user_type": getattr(p, "user_type", "PATIENT") or "PATIENT",
        "role": getattr(p, "role", None),
        "specialty": getattr(p, "specialty", None),
        "staff_department": getattr(p, "staff_department", None),
        "opd_department_id": getattr(p, "opd_department_id", None),
        "opd_room_id": getattr(p, "opd_room_id", None),
        "purpose": getattr(p, "purpose", None),
        "note": getattr(p, "note", None),
    }


# ── /scan ────────────────────────────────────────────────────────────────
def _bbox_area(bbox) -> int:
    """Pixel area of a [x1,y1,x2,y2] bbox. Used to rank "closest to camera"
    — in a hospital queue the person standing at the counter is the
    largest face in the frame."""
    if not bbox or len(bbox) < 4:
        return 0
    return max(0, int(bbox[2]) - int(bbox[0])) * max(0, int(bbox[3]) - int(bbox[1]))


@router.post("/scan")
async def scan(
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Scan a webcam frame and resolve to a known user.

    Front-desk operators commonly stand a queue of 3-5 people in the
    camera frame. Picking by recognition confidence often grabs the
    person at the back. We rank instead by bbox area (largest face =
    closest to camera = person at the counter), and surface every
    other identified face in ``candidates`` so the operator can
    one-click swap to a different person without re-scanning.

    Response::

        {
          "matched": bool,
          "patient": {...} | null,      # the auto-picked primary (largest face)
          "confidence": float | null,
          "recent_visits": [...],
          "candidates": [               # every identified face, primary first
            {
              "patient_id": int,
              "name": str,
              "confidence": float,
              "bbox_area": int,         # pixel area, descending
              "is_primary": bool,       # exactly one true (the auto-pick)
              "patient": {...}          # full _patient_summary for one-click prefill
            },
            ...
          ],
          "unrecognised_count": int     # detected but Unknown faces in the frame
        }
    """
    data = await image.read()
    img = _decode_image(data)

    faces = recognize_faces(img, db)
    if not faces:
        return {
            "matched": False,
            "patient": None,
            "confidence": None,
            "candidates": [],
            "unrecognised_count": 0,
        }

    identified = [f for f in faces if f.get("user_id") is not None]
    unrecognised_count = len(faces) - len(identified)

    if not identified:
        return {
            "matched": False,
            "patient": None,
            "confidence": float(max(f["confidence"] for f in faces)),
            "candidates": [],
            "unrecognised_count": unrecognised_count,
        }

    # Rank by bbox area (largest = closest to camera). Stable sort, ties
    # broken by recognition confidence so the more-certain match wins
    # when two faces are roughly equal size.
    identified.sort(
        key=lambda f: (_bbox_area(f.get("bbox")), float(f.get("confidence", 0.0))),
        reverse=True,
    )

    # Batch-fetch every candidate user row in one query — avoids N
    # round-trips when the frame has 5+ people.
    user_ids = [f["user_id"] for f in identified]
    patients_by_id = {
        p.id: p
        for p in db.query(Patient).filter(Patient.id.in_(user_ids)).all()
    }

    candidates = []
    primary_patient = None
    primary_confidence = None
    for idx, f in enumerate(identified):
        p = patients_by_id.get(f["user_id"])
        if p is None:
            # FAISS has the id, Postgres doesn't — skip; the orphan
            # cleanup path in face_service handles the FAISS-side
            # removal on next recognition.
            continue
        is_primary = primary_patient is None  # first surviving row wins
        summary = _patient_summary(p)
        candidates.append({
            "patient_id": p.id,
            "name": p.name,
            "confidence": float(f.get("confidence", 0.0)),
            "bbox_area": _bbox_area(f.get("bbox")),
            "is_primary": is_primary,
            "patient": summary,
        })
        if is_primary:
            primary_patient = p
            primary_confidence = float(f.get("confidence", 0.0))

    if primary_patient is None:
        return {
            "matched": False,
            "patient": None,
            "confidence": None,
            "candidates": [],
            "unrecognised_count": unrecognised_count,
        }

    recent = frontdesk_service.list_patient_visits(
        db, primary_patient.id, limit=5
    )

    return {
        "matched": True,
        "patient": _patient_summary(primary_patient),
        "confidence": primary_confidence,
        "recent_visits": [frontdesk_service.visit_to_dict(v) for v in recent],
        "candidates": candidates,
        "unrecognised_count": unrecognised_count,
    }


# ── /visits ──────────────────────────────────────────────────────────────
@router.post("/visits")
def create_visit_endpoint(payload: CreateVisitIn, db: Session = Depends(get_db)):
    try:
        visit = frontdesk_service.create_visit(
            db,
            patient_id=payload.patient_id,
            visit_type=payload.visit_type,
            department_id=payload.department_id,
            doctor_id=payload.doctor_id,
            room_id=payload.room_id,
            note=payload.note,
            source=payload.source or "FACE_SCAN",
            created_by=payload.created_by,
        )
    except PatientNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidVisitPayloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FrontDeskError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    patient = db.query(Patient).filter(Patient.id == visit.patient_id).first()

    # Best-effort broadcast — never let a WS issue break the request.
    try:
        import asyncio

        from backend.app.services.ws_manager import manager

        asyncio.get_event_loop().create_task(
            manager.broadcast(
                {
                    "type": "frontdesk.visit.created",
                    "visit": frontdesk_service.visit_to_dict(visit),
                }
            )
        )
    except Exception as exc:  # pragma: no cover — best effort
        logger.warning("WS broadcast (visit.created) failed: %s", exc)

    return frontdesk_service.slip_payload(visit, patient)


@router.get("/visits/today")
def list_today(
    department_id: Optional[int] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    try:
        visits = frontdesk_service.list_today_visits(
            db, department_id=department_id, status=status
        )
    except InvalidVisitPayloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"visits": [frontdesk_service.visit_to_dict(v) for v in visits]}


@router.get("/visits/{visit_id}")
def get_visit(visit_id: int, db: Session = Depends(get_db)):
    from backend.app.db.frontdesk_models import OPDVisit

    visit = db.query(OPDVisit).filter(OPDVisit.id == visit_id).first()
    if visit is None:
        raise HTTPException(status_code=404, detail=f"visit {visit_id} not found")
    patient = db.query(Patient).filter(Patient.id == visit.patient_id).first()
    return frontdesk_service.slip_payload(visit, patient)


@router.patch("/visits/{visit_id}")
def patch_visit(visit_id: int, payload: UpdateVisitIn, db: Session = Depends(get_db)):
    try:
        visit = frontdesk_service.update_visit_status(db, visit_id, payload.status)
    except PatientNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidVisitPayloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        import asyncio

        from backend.app.services.ws_manager import manager

        asyncio.get_event_loop().create_task(
            manager.broadcast(
                {
                    "type": "frontdesk.visit.status_changed",
                    "visit": frontdesk_service.visit_to_dict(visit),
                }
            )
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("WS broadcast (visit.status_changed) failed: %s", exc)

    return frontdesk_service.visit_to_dict(visit)


@router.post("/visits/{visit_id}/reprint")
def reprint(visit_id: int, db: Session = Depends(get_db)):
    try:
        visit = frontdesk_service.mark_printed(db, visit_id)
    except PatientNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    patient = db.query(Patient).filter(Patient.id == visit.patient_id).first()
    return frontdesk_service.slip_payload(visit, patient)


@router.get("/visits/by-date/{date_iso}")
def visits_for_date(date_iso: str, db: Session = Depends(get_db)):
    """List every OPD visit for a given date (YYYY-MM-DD)."""
    try:
        visits = frontdesk_service.list_visits_for_date(db, date_iso)
    except InvalidVisitPayloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"visits": [frontdesk_service.visit_to_dict(v) for v in visits]}


@router.get("/patients/by-date/{date_iso}")
def patients_with_visits_on(date_iso: str, db: Session = Depends(get_db)):
    """
    Distinct patients with at least one OPD visit on a given date.
    Used by the History page to surface front-desk patients.
    """
    try:
        rows = frontdesk_service.list_patients_with_visits_on(db, date_iso)
    except InvalidVisitPayloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"patients": rows}


@router.get("/visits/{visit_id}/history")
def visit_history(visit_id: int, db: Session = Depends(get_db)):
    """Full status-transition timeline for a visit."""
    rows = frontdesk_service.list_visit_history(db, visit_id)
    return {"history": [frontdesk_service.history_to_dict(r) for r in rows]}


@router.get("/search")
def manual_search(q: str, limit: int = 10, db: Session = Depends(get_db)):
    """
    Manual fallback for failed face scans. Matches MRN, phone or name.
    Returns the same payload shape as /scan so the frontend can swap
    transparently between scan and search.
    """
    patients = frontdesk_service.search_patients(db, q, limit=limit)
    if not patients:
        return {"matched": False, "patient": None, "candidates": []}

    best = patients[0]
    recent = frontdesk_service.list_patient_visits(db, best.id, limit=5)
    return {
        "matched": True,
        "patient": _patient_summary(best),
        "confidence": None,
        "recent_visits": [frontdesk_service.visit_to_dict(v) for v in recent],
        "candidates": [
            {
                "patient_id": p.id,
                "name": p.name,
                "mrn": p.mrn,
                "contact_number": p.contact_number,
                "user_type": getattr(p, "user_type", "PATIENT") or "PATIENT",
            }
            for p in patients
        ],
    }


@router.get("/patients/{patient_id}/visits")
def patient_visits(patient_id: int, limit: int = 50, db: Session = Depends(get_db)):
    visits = frontdesk_service.list_patient_visits(db, patient_id, limit=limit)
    return {"visits": [frontdesk_service.visit_to_dict(v) for v in visits]}
