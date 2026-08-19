"""
history.py — Session history endpoints.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.db.postgres import get_db
from backend.app.db.models import Patient, PatientSession

router = APIRouter(prefix="/history", tags=["history"])


@router.get("/date/{target_date}")
def get_sessions_by_date(target_date: date, db: Session = Depends(get_db)):
    try:
        sessions = (
            db.query(PatientSession)
            .join(Patient)
            .filter(func.date(PatientSession.entry_time) == target_date)
            .all()
        )

        result = []
        for s in sessions:
            duration = None
            if s.exit_time:
                duration = (s.exit_time - s.entry_time).total_seconds()

            result.append({
                "patient_id": s.patient_id,
                "name": s.patient.name if s.patient else None,
                "user_type": getattr(s.patient, "user_type", "PATIENT") if s.patient else "PATIENT",
                "entry": s.entry_time,
                "exit": s.exit_time,
                "duration": duration,
                "floor": s.current_floor,
                "camera_id": s.current_camera,
            })

        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/patient/{patient_id}")
def get_patient_history(patient_id: int, db: Session = Depends(get_db)):
    try:
        sessions = (
            db.query(PatientSession)
            .filter(PatientSession.patient_id == patient_id)
            .order_by(PatientSession.entry_time.desc())
            .all()
        )

        return [
            {
                "entry": s.entry_time,
                "exit": s.exit_time,
                "floor": s.current_floor,
                "camera_id": s.current_camera,
            }
            for s in sessions
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
