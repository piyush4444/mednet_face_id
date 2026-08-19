"""
kiosk_service.py — business logic for the entry-gate kiosk.

The kiosk runs a continuous scan loop: a face is recognized, a punch is
recorded, a greeting is shown, and the screen returns to scanning. This
module owns everything behind that cycle:

- ``record_punch``      : mapping lookup/creation, duplicate-punch window,
                          visit counter, tracking-log row, and (for
                          EMPLOYEE/DOCTOR) the outbound punch-export row.
- ``submit_pre_registration`` : persists a client-shaped pre-registration
                          payload as PENDING; the actual HTTP push to the
                          client HIS is the export phase's job.
- ``generate_unique_mrn``: backs the form's "generate MRN" button.

Design notes
------------
- Punches are logged for EVERY user type (tracking history); the export
  queue is populated only for EMPLOYEE/DOCTOR mappings — that is the
  client attendance contract (hospital staff punch; patients/visitors
  don't).
- ``biometric_idx`` is ``{serial}_{uid}_{date}_{time}`` (the client's
  unique transaction id). The duplicate-punch window makes same-second
  collisions unreachable in practice; a unique-constraint race is still
  caught and treated as a duplicate.
- The client HIS credentials are env vars; deployment identifiers come from
  the singleton facility row. Nothing secret is ever written into payloads
  stored in the DB.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.facility_models import (
    ExportStatus,
    Facility,
    PersonTrackingLog,
    PersonVisitMapping,
    PreRegistrationLog,
    PunchExport,
    TrackingSource,
    VisitType,
)
from backend.app.db.models import User, UserType
from backend.app.services import client_api
from backend.app.services.face_service import generate_mrn
from backend.app.services.media_service import resolve_media_url
from backend.app.utils.time_ist import now_ist

logger = logging.getLogger("backend.kiosk")


class KioskError(Exception):
    pass


class FacilityNotFoundError(KioskError):
    pass


class UserNotFoundError(KioskError):
    pass


# Person types whose punches are exported to the client attendance API.
_EXPORTED_TYPES = {UserType.EMPLOYEE.value, UserType.DOCTOR.value}


# ── Mapping ──────────────────────────────────────────────────────────────
def get_or_create_mapping(
    db: Session, user: User, facility_id: int
) -> PersonVisitMapping:
    """One row per (person, facility); created lazily on first sighting.

    A new mapping inherits the user's global ``user_type`` and MRN as its
    starting operational values for the singleton facility.
    """
    mapping = (
        db.query(PersonVisitMapping)
        .filter(
            PersonVisitMapping.person_id == user.id,
            PersonVisitMapping.facility_id == facility_id,
        )
        .first()
    )
    if mapping is not None:
        return mapping

    mapping = PersonVisitMapping(
        person_id=user.id,
        facility_id=facility_id,
        person_type=user.user_type or UserType.PATIENT.value,
        mrn=user.mrn,
        visit_count=0,
    )
    db.add(mapping)
    try:
        db.flush()  # assign mapping.id before the initial version row
        from backend.app.services.mapping_service import record_version
        record_version(db, mapping, reason="first sighting")
        db.commit()
    except Exception:
        # Concurrent first-sighting from two kiosks — the other one won.
        db.rollback()
        mapping = (
            db.query(PersonVisitMapping)
            .filter(
                PersonVisitMapping.person_id == user.id,
                PersonVisitMapping.facility_id == facility_id,
            )
            .first()
        )
        if mapping is None:
            raise
        return mapping
    db.refresh(mapping)
    return mapping


# ── Punch ────────────────────────────────────────────────────────────────
def _recent_same_punch(
    db: Session, mapping_id: int, visit_type: str, window_seconds: int
) -> Optional[PersonTrackingLog]:
    """Latest identical punch inside the duplicate window, if any."""
    cutoff = now_ist() - timedelta(seconds=window_seconds)
    return (
        db.query(PersonTrackingLog)
        .filter(
            PersonTrackingLog.person_facility_id == mapping_id,
            PersonTrackingLog.visit_type == visit_type,
            PersonTrackingLog.event_time >= cutoff,
        )
        .order_by(PersonTrackingLog.event_time.desc())
        .first()
    )


def _auto_direction(db: Session, mapping_id: int) -> str:
    """Resolve AUTO mode → IN/OUT from the last IN/OUT punch for a mapping.

    Currently inside (last punch was IN) → this is an OUT; otherwise IN.
    Tracker sightings are ignored — only gate/kiosk punches flip presence.
    """
    last = (
        db.query(PersonTrackingLog)
        .filter(
            PersonTrackingLog.person_facility_id == mapping_id,
            PersonTrackingLog.visit_type.in_(
                [VisitType.IN.value, VisitType.OUT.value]
            ),
        )
        .order_by(PersonTrackingLog.event_time.desc())
        .first()
    )
    if last is not None and last.visit_type == VisitType.IN.value:
        return VisitType.OUT.value
    return VisitType.IN.value


def _build_punch_payload(
    user_id: int, serial: str, visit_type: str, event_time
) -> tuple[Dict[str, Any], str]:
    """Client attendance payload for ONE punch (their device-emulation
    contract). ``bioStatus``: "0" = IN, "1" = OUT."""
    biometric_idx = (
        f"{serial}_{user_id}_"
        f"{event_time.strftime('%Y-%m-%d')}_{event_time.strftime('%H:%M:%S')}"
    )
    payload = {
        "serialNumber": serial,
        "biometricServerIP": "",
        "biometricUID": user_id,
        "biometricUserID": user_id,
        "bioStatus": "0" if visit_type == VisitType.IN.value else "1",
        "biometricIDX": biometric_idx,
        "punchDate": event_time.strftime("%Y-%m-%d"),
        "punchTime": event_time.strftime("%H:%M:%S"),
    }
    return payload, biometric_idx


def record_punch(
    db: Session,
    *,
    user: User,
    facility: Facility,
    mode: str,
    kiosk_serial: Optional[str] = None,
    camera_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Record one kiosk punch and return what the greeting screen needs.

    ``mode`` is IN, OUT, or AUTO. AUTO (the default for a two-way gate)
    resolves the direction from the person's own last punch at this
    facility: currently inside → OUT, otherwise → IN. So one kiosk handles
    both directions with no operator setting.

    Inside the duplicate window (``settings.KIOSK_DUPLICATE_WINDOW``) the
    existing punch is returned with ``duplicate=True`` and nothing is
    written — a person lingering in front of the camera punches once.
    """
    mapping = get_or_create_mapping(db, user, facility.id)

    mode = (mode or "AUTO").upper()
    if mode == "IN":
        visit_type = VisitType.IN.value
    elif mode == "OUT":
        visit_type = VisitType.OUT.value
    else:  # AUTO — flip based on the last IN/OUT punch for this mapping.
        visit_type = _auto_direction(db, mapping.id)

    recent = _recent_same_punch(
        db, mapping.id, visit_type, settings.KIOSK_DUPLICATE_WINDOW
    )
    if recent is not None:
        return {
            "duplicate": True,
            "visit_type": visit_type,
            "event_time": recent.event_time,
            "visit_number": recent.visit_number,
            "person_type": mapping.person_type,
            "mrn": mapping.mrn,
        }

    event_time = now_ist()
    if visit_type == VisitType.IN.value:
        mapping.visit_count = (mapping.visit_count or 0) + 1
        mapping.last_visit_at = event_time

    log = PersonTrackingLog(
        person_facility_id=mapping.id,
        # Stamp the membership version in effect right now, so this event
        # always replays against the role/MRN that were true at punch time.
        person_facility_version=mapping.current_version,
        visit_type=visit_type,
        camera_id=camera_id,
        event_time=event_time,
        visit_number=mapping.visit_count,
        source=TrackingSource.KIOSK.value,
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    # Attendance export — staff only, one row per punch, idempotent on
    # the client side via biometric_idx.
    if mapping.person_type in _EXPORTED_TYPES:
        serial = (kiosk_serial or camera_id or f"IRIS-{facility.code}").strip()
        payload, biometric_idx = _build_punch_payload(
            user.id, serial, visit_type, event_time
        )
        export = PunchExport(
            visit_log_id=log.id,
            facility_id=mapping.facility_id,
            biometric_idx=biometric_idx,
            bio_status=payload["bioStatus"],
            payload=payload,
            status=ExportStatus.PENDING.value,
        )
        db.add(export)
        try:
            db.commit()
        except Exception:
            # biometric_idx race (same person+serial+second) — the punch
            # itself is already committed; treat the export as a dup.
            db.rollback()
            logger.warning(
                "Punch export dedup hit for %s — skipped", biometric_idx
            )

    return {
        "duplicate": False,
        "visit_type": visit_type,
        "event_time": log.event_time,
        "visit_number": log.visit_number,
        "person_type": mapping.person_type,
        "mrn": mapping.mrn,
    }


# ── MRN ──────────────────────────────────────────────────────────────────
def generate_unique_mrn(db: Session, facility_id: Optional[int] = None) -> str:
    """Random MRN, unique against both the legacy global ``users.mrn``
    and facility-membership MRNs. Backs the form's generate button."""
    for _ in range(8):
        mrn = generate_mrn()
        clash = db.query(User).filter(User.mrn == mrn).first()
        if clash is None:
            q = db.query(PersonVisitMapping).filter(PersonVisitMapping.mrn == mrn)
            if facility_id is not None:
                q = q.filter(PersonVisitMapping.facility_id == facility_id)
            clash = q.first()
        if clash is None:
            return mrn
    raise KioskError("Failed to generate a unique MRN")


# ── Pre-registration ─────────────────────────────────────────────────────
# Registry fields the kiosk form may write back onto the user row.
_PREREG_USER_FIELDS = {
    "prefix", "first_name", "middle_name", "last_name", "gender", "dob",
    "contact_number", "whatsapp_number", "email", "address", "city",
    "state", "country", "pin_code", "national_id_type", "national_id",
    "next_of_kin_relation", "next_of_kin_name", "next_of_kin_contact",
}


def submit_pre_registration(
    db: Session,
    *,
    user: User,
    facility: Facility,
    form: Dict[str, Any],
    token_type: str = "General",
) -> PreRegistrationLog:
    """Persist a pre-registration as PENDING in the client's payload shape.

    Also writes any corrected demographics back onto the registry row and
    the facility-issued MRN onto the membership. The HTTP push to the client
    HIS happens in the export phase (no-op until CLIENT_PREREG_API_URL is
    configured).
    """
    mapping = get_or_create_mapping(db, user, facility.id)

    # ── Registry write-back (kiosk corrections are authoritative) ────
    for field in _PREREG_USER_FIELDS:
        if field in form and form[field] not in (None, ""):
            setattr(user, field, form[field])
    parts = [user.first_name, user.middle_name, user.last_name]
    joined = " ".join(p for p in parts if p)
    if joined:
        user.name = joined

    mrn = (form.get("mrn") or "").strip()
    if mrn:
        mapping.mrn = mrn

    # ── Client-shaped request payload ─────────────────────────────────
    integration = facility.integration_config or {}
    photo_url = ""
    if settings.MEDIA_BASE_URL and user.photo_url:
        # Only absolute URLs are useful to the client HIS; the local
        # /media mount is not reachable from their side.
        photo_url = resolve_media_url(user.photo_url) or ""
    request_payload = {
        "prefix": user.prefix or "",
        "firstName": user.first_name or "",
        "middleName": user.middle_name or "",
        "lastName": user.last_name or "",
        "gender": user.gender or "",
        "dateOfBirth": user.dob.isoformat() if user.dob else "",
        "address": user.address or "",
        "phoneNo": user.contact_number or "",
        "mrn": mapping.mrn or "",
        "txnDate": "",
        "billingCounter": "",
        "whatsappNumber": user.whatsapp_number or "",
        "emailID": user.email or "",
        "nextOfKinRelation": user.next_of_kin_relation or "",
        "nextOfKinName": user.next_of_kin_name or "",
        "nextOfKinContact": user.next_of_kin_contact or "",
        "city": user.city or "",
        "state": user.state or "",
        "country": user.country or "",
        "pinCode": user.pin_code or "",
        "patientProfilePhotoURL": photo_url,
        "type": "OP",
        "companyID": facility.client_company_id,
        "facilityGuid": facility.facility_guid or "",
        "patientNationalIDType": user.national_id_type or "",
        "nationalID": user.national_id or "",
        "patientTokenType": token_type,
        "status": "WAITING",
        "queueSetupID": integration.get("queueSetupID"),
    }

    # If the integration is live, claim the row as SENDING up front so
    # the background worker can never race this inline push; otherwise
    # leave it PENDING for the worker to pick up once configured.
    enabled = client_api.prereg_enabled()
    entry = PreRegistrationLog(
        person_facility_id=mapping.id,
        facility_id=facility.id,
        status=ExportStatus.SENDING.value if enabled else ExportStatus.PENDING.value,
        queue_setup_id=integration.get("queueSetupID"),
        request_payload=request_payload,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    if enabled:
        # Inline push so the kiosk can show the token immediately. On
        # failure the row is left FAILED with a retry time — the export
        # worker takes over. Never raises into the request.
        result = client_api.push_prereg(request_payload)
        if result.ok:
            entry.status = ExportStatus.SENT.value
            entry.attempts = 1
            entry.response_payload = result.data
            entry.pre_regn_id = result.pre_regn_id
            entry.token_no = result.token_no
            if result.queue_setup_id is not None:
                entry.queue_setup_id = result.queue_setup_id
            data = (result.data or {}).get("data") if isinstance(result.data, dict) else None
            his_mrn = (data or {}).get("mrn") if isinstance(data, dict) else None
            if his_mrn and not mapping.mrn:
                mapping.mrn = str(his_mrn)
        else:
            from datetime import timedelta
            entry.status = ExportStatus.FAILED.value
            entry.attempts = 1
            entry.last_error = (result.error or "unknown error")[:1000]
            entry.next_retry_at = now_ist() + timedelta(
                seconds=settings.EXPORT_BACKOFF_BASE
            )
            logger.warning(
                "Inline pre-reg push failed (prereg_log=%d): %s",
                entry.id, result.error,
            )
        db.commit()
        db.refresh(entry)

    logger.info(
        "Pre-registration %s: user=%d facility=%d prereg_log=%d token=%s",
        "sent" if entry.status == ExportStatus.SENT.value else "queued",
        user.id, facility.id, entry.id, entry.token_no,
    )
    return entry


def prereg_to_dict(e: PreRegistrationLog) -> Dict[str, Any]:
    return {
        "id": e.id,
        "status": e.status,
        "pre_regn_id": e.pre_regn_id,
        "token_no": e.token_no,
        "queue_setup_id": e.queue_setup_id,
        "created_at": e.created_at,
    }
