from sqlalchemy.orm import Session
from backend.app.db.models import PatientSession
from backend.app.utils.time_ist import now_ist


def create_session(
    db: Session,
    patient_id: int,
    camera_id: str,
    floor: str | None = None,
):
    session = PatientSession(
        patient_id=patient_id,
        status="INSIDE",
        current_floor=floor,
        current_camera=camera_id,
        entry_time=now_ist(),
        last_seen=now_ist(),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_active_session(db: Session, patient_id: int):
    return (
        db.query(PatientSession)
        .filter(
            PatientSession.patient_id == patient_id,
            PatientSession.status == "INSIDE",
        )
        .first()
    )


def update_session_activity(
    db: Session,
    session: PatientSession,
    camera_id: str,
    floor: str | None = None,
):
    session.last_seen = now_ist()
    if camera_id is not None:
        session.current_camera = camera_id
    if floor is not None:
        session.current_floor = floor
    db.commit()


def close_session(db: Session, session: PatientSession, camera_id: str):
    session.exit_time = now_ist()
    session.current_camera = camera_id
    session.status = "OUT"
    session.last_seen = now_ist()
    db.commit()
