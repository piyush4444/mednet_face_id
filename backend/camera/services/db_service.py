"""
db_service.py — Low-level DB operations for patient tracking.

Only DB queries + commits live here. No business logic, no decisions
about when to call what. Callers are responsible for lifecycle.
"""

from backend.app.db.models import Patient, PatientSession
from backend.app.utils.time_ist import now_ist


def create_or_get_active_session(db, user_id, camera_id, floor):
    """Upsert a single INSIDE session for this patient. Returns the session."""
    now = now_ist()
    try:
        session = (
            db.query(PatientSession)
            .filter(
                PatientSession.patient_id == user_id,
                PatientSession.status == "INSIDE",
            )
            .order_by(PatientSession.entry_time.desc())
            .first()
        )

        if session is None:
            session = PatientSession(
                patient_id=user_id,
                status="INSIDE",
                current_floor=floor,
                current_camera=camera_id,
                entry_time=now,
                last_seen=now,
            )
            db.add(session)
        else:
            session.current_floor = floor
            session.current_camera = camera_id
            session.last_seen = now

        db.commit()
        print(f"[DB ENTRY] session_id={session.id} patient_id={user_id}")
        return session
    except Exception as exc:
        db.rollback()
        print(f"[DB ENTRY ERROR] patient_id={user_id} {exc}")
        return None


def update_patient_presence(db, user_id, camera_id, floor=None, status=None):
    """Touch Patient + active session last_seen / current_camera."""
    now = now_ist()
    try:
        patient = db.query(Patient).filter(Patient.id == user_id).first()
        if patient:
            patient.last_seen_at = now
            if camera_id is not None:
                patient.current_camera_id = camera_id
            if floor is not None:
                patient.current_floor = floor
            if status is not None:
                patient.current_status = status

        session = (
            db.query(PatientSession)
            .filter(
                PatientSession.patient_id == user_id,
                PatientSession.status == "INSIDE",
            )
            .order_by(PatientSession.entry_time.desc())
            .first()
        )
        if session:
            session.last_seen = now
            if camera_id is not None:
                session.current_camera = camera_id
            if floor is not None:
                session.current_floor = floor

        db.commit()
        # Heartbeat write — fires per-detection-per-camera. The success
        # path is uninteresting (and identical run-to-run); only the
        # error path is worth surfacing so a Postgres outage isn't silent.
    except Exception as exc:
        db.rollback()
        print(f"[DB UPDATE ERROR] patient_id={user_id} {exc}")


def close_session(db, user_id):
    """Close the most recent INSIDE session and mark patient OUT."""
    now = now_ist()
    try:
        session = (
            db.query(PatientSession)
            .filter(
                PatientSession.patient_id == user_id,
                PatientSession.status == "INSIDE",
            )
            .order_by(PatientSession.entry_time.desc())
            .first()
        )
        if session:
            session.status = "OUT"
            session.exit_time = now
            session.last_seen = now

        patient = db.query(Patient).filter(Patient.id == user_id).first()
        if patient:
            patient.current_status = "OUT"
            patient.current_camera_id = None

        db.commit()
        print(f"[DB EXIT] session closed patient_id={user_id}")
    except Exception as exc:
        db.rollback()
        print(f"[DB EXIT ERROR] patient_id={user_id} {exc}")
