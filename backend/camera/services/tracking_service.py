"""
tracking_service.py — Business logic for patient tracking lifecycle.

The event processor drives camera_state and calls these handlers when a
user enters, is re-seen, or leaves a camera. This layer decides *what*
should happen in the DB; db_service executes *how*.
"""

from backend.camera.services import db_service


def handle_entry(db, user_id, camera_id, floor):
    """First sighting of this patient on this camera."""
    db_service.create_or_get_active_session(db, user_id, camera_id, floor)
    db_service.update_patient_presence(
        db, user_id, camera_id, floor=floor, status="IN",
    )


def handle_update(db, user_id, camera_id, floor):
    """Continued presence — refresh last_seen and current location."""
    db_service.update_patient_presence(
        db, user_id, camera_id, floor=floor,
    )


def handle_exit(db, user_id, camera_id, floor):
    """Patient no longer visible on this camera beyond the exit timeout."""
    db_service.close_session(db, user_id)
