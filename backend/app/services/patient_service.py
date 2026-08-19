from sqlalchemy import or_
from sqlalchemy.orm import Session
from backend.app.db.models import Patient
from backend.app.utils.time_ist import now_ist


def update_patient_location(db: Session, patient_id: int, floor: str, camera_id: str):
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        return

    patient.current_status = "IN"
    patient.current_floor = floor
    patient.current_camera_id = camera_id
    patient.last_seen_at = now_ist()

    db.commit()


def mark_patient_exit(db: Session, patient_id: int):
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        return

    patient.current_status = "OUT"
    patient.current_floor = None
    patient.current_camera_id = None
    patient.last_seen_at = now_ist()

    db.commit()


def search_patients(db: Session, query: str):
    return (
        db.query(Patient)
        .filter(
            or_(
                Patient.mrn.ilike(f"%{query}%"),
                Patient.name.ilike(f"%{query}%"),
            )
        )
        .limit(20)
        .all()
    )


def get_patient_status(db: Session, patient_id: int):
    return (
        db.query(Patient)
        .filter(Patient.id == patient_id)
        .first()
    )


def get_active_patients(db: Session):
    return (
        db.query(Patient)
        .filter(Patient.current_status == "IN")
        .order_by(Patient.last_seen_at.desc())
        .all()
    )


def get_patients_grouped_by_camera(db: Session):
    patients = (
        db.query(Patient)
        .filter(Patient.current_status == "IN")
        .all()
    )

    camera_map = {}

    for p in patients:
        cam = p.current_camera_id or "unknown"

        if cam not in camera_map:
            camera_map[cam] = []

        camera_map[cam].append({
            "id": p.id,
            "mrn": p.mrn,
            "name": p.name,
            "last_seen_at": p.last_seen_at,
        })

    return camera_map
