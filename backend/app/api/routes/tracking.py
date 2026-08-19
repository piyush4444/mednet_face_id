"""
tracking.py — Real-time patient tracking endpoints (V2).
"""

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.db.postgres import get_db
from backend.app.db.models import Patient
from backend.app.services.patient_service import (
    search_patients,
    get_patients_grouped_by_camera,
)
from backend.app.services.presence_cache import get_presence
from backend.camera import store as camera_store

router = APIRouter(prefix="/tracking", tags=["tracking"])


@router.get("/presence")
def get_global_presence():
    """Cross-camera presence snapshot: {user_id: {last_seen, cameras}}."""
    try:
        return get_presence()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/find")
def find_patient(query: str = Query(...), db: Session = Depends(get_db)):
    results = search_patients(db, query)

    if not results:
        return {
            "found": False,
            "patient": None,
            "message": "Patient not found",
        }

    patient = results[0]
    is_inside = patient.current_status == "IN"

    # Resolve a human-readable camera name from the persistent roster so
    # the search popover can show "Lobby Cam · Floor 2" instead of
    # "cam_3f1a8c92". Falls back to the bare id if the camera was
    # removed since the last detection.
    location = None
    if is_inside and patient.current_camera_id:
        cam = camera_store.get(patient.current_camera_id) or {}
        location = {
            "camera_id": patient.current_camera_id,
            "camera_name": cam.get("name") or patient.current_camera_id,
            "floor": patient.current_floor or cam.get("floor"),
        }

    return {
        "found": is_inside,
        "patient": {
            "id": patient.id,
            "mrn": patient.mrn,
            "name": patient.name,
        },
        "location": location,
        "last_seen_at": patient.last_seen_at,
        "status": patient.current_status,
    }


@router.get("/cameras")
def camera_view(db: Session = Depends(get_db)):
    """Live camera roster joined with current presence.

    Reads from the persistent camera store (not the legacy in-code list)
    so cameras added/removed via the admin API show up immediately on
    the dashboard without a server restart. Inactive cameras are
    omitted — the dashboard only renders things the user expects to be
    streaming.
    """
    camera_map = get_patients_grouped_by_camera(db)

    result = []
    for cam in camera_store.load():
        if not cam.get("active", True):
            continue
        cam_id = cam["camera_id"]
        result.append({
            "camera_id": cam_id,
            # Surface the human-readable name so the dashboard can label
            # tiles with what the operator typed in the admin UI rather
            # than the auto-generated slug. Falls back to camera_id so
            # legacy entries without a name don't render as blank.
            "name": cam.get("name") or cam_id,
            "floor": cam.get("floor"),
            "role": cam.get("role"),
            "patients": camera_map.get(cam_id, []),
        })

    return {"cameras": result}


def _serialize(p: Patient) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "status": "IN",
        "floor": p.current_floor,
        "camera_id": p.current_camera_id,
        "last_seen_at": p.last_seen_at,
    }


@router.get("/current")
def current_patients(db: Session = Depends(get_db)):
    try:
        patients = (
            db.query(Patient)
            .filter(Patient.current_status == "IN")
            .all()
        )
        return [_serialize(p) for p in patients]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"db error: {exc}")


@router.get("/by-camera")
def by_camera(db: Session = Depends(get_db)):
    try:
        patients = (
            db.query(Patient)
            .filter(Patient.current_status == "IN")
            .all()
        )

        by_cam: dict = defaultdict(list)
        for p in patients:
            if p.current_camera_id:
                by_cam[p.current_camera_id].append(_serialize(p))

        cam_floor = {cam["camera_id"]: cam.get("floor") for cam in camera_store.load()}
        cam_ids = set(cam_floor.keys()) | set(by_cam.keys())

        cameras = [
            {
                "camera_id": cam_id,
                "floor": cam_floor.get(cam_id),
                "patients": by_cam.get(cam_id, []),
            }
            for cam_id in cam_ids
        ]
        return {"cameras": cameras}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"db error: {exc}")


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    try:
        total = (
            db.query(func.count(Patient.id))
            .filter(Patient.current_status == "IN")
            .scalar()
        ) or 0

        rows = (
            db.query(Patient.current_floor, func.count(Patient.id))
            .filter(Patient.current_status == "IN")
            .group_by(Patient.current_floor)
            .all()
        )

        floors = {(floor or "unknown"): count for floor, count in rows}

        return {"total_inside": total, "floors": floors}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"db error: {exc}")
