"""
kiosk.py — endpoints for the entry-gate kiosk (separate-URL frontend).

Routes
------
POST /kiosk/scan       One scan-loop cycle: recognize the largest face in
                       the frame and record a punch (IN/OUT per kiosk mode).
POST /kiosk/prereg     Submit a patient pre-registration (queued PENDING
                       for the client-HIS push).
GET  /kiosk/mrn/new    Random unique MRN (the form's generate button).

Like the rest of the API there is no request auth yet — the
feat/auth-rbac merge must gate these (kiosk device principal). Input
hardening (size caps, magic-byte sniffing, enum validation) is done
here so the service layer only ever sees clean data.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.db.models import User
from backend.app.db.postgres import get_db
from backend.app.services import kiosk_service
from backend.app.services.face_service import recognize_faces
from backend.app.services.facility_service import (
    ConflictError as FacilityConflict,
    NotFoundError as FacilityNotFound,
    get_single_facility,
)
from backend.app.services.kiosk_service import KioskError
from backend.app.services.media_service import resolve_media_url

logger = logging.getLogger("backend.kiosk.routes")

router = APIRouter(prefix="/kiosk", tags=["kiosk"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB per frame

# Magic-byte allowlist — bytes decide, not filenames (see register.py).
_IMAGE_SIGNATURES: tuple[bytes, ...] = (
    b"\xff\xd8\xff",       # JPEG
    b"\x89PNG\r\n\x1a\n",  # PNG
)

_VALID_MODES = {"IN", "OUT", "AUTO"}
_VALID_TOKEN_TYPES = {"General", "Cash"}


def _facility_or_http(db: Session):
    try:
        return get_single_facility(db)
    except FacilityNotFound as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except FacilityConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))


def _decode_frame(data: bytes) -> np.ndarray:
    if not data:
        raise HTTPException(status_code=400, detail="Empty image")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Image too large")
    if not any(data.startswith(sig) for sig in _IMAGE_SIGNATURES):
        raise HTTPException(
            status_code=400, detail="Unsupported format (only JPEG/PNG allowed)"
        )
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="Could not decode image")
    return img


def _bbox_area(bbox) -> int:
    if not bbox or len(bbox) < 4:
        return 0
    return max(0, int(bbox[2]) - int(bbox[0])) * max(0, int(bbox[3]) - int(bbox[1]))


def _user_summary(u: User, punch: dict) -> dict:
    """The subset the kiosk greeting + pre-reg form need — deliberately
    NOT the full registry row (the kiosk is a public screen)."""
    return {
        "id": u.id,
        "name": u.name,
        "prefix": u.prefix,
        "first_name": u.first_name,
        "middle_name": u.middle_name,
        "last_name": u.last_name,
        "gender": u.gender,
        "dob": u.dob.isoformat() if u.dob else None,
        "contact_number": u.contact_number,
        "whatsapp_number": u.whatsapp_number,
        "email": u.email,
        "address": u.address,
        "city": u.city,
        "state": u.state,
        "country": u.country,
        "pin_code": u.pin_code,
        "national_id_type": u.national_id_type,
        "national_id": u.national_id,
        "person_type": punch.get("person_type"),
        "mrn": punch.get("mrn"),
        "photo_url": resolve_media_url(u.photo_url),
    }


# ── /scan ────────────────────────────────────────────────────────────────
@router.post("/scan")
async def kiosk_scan(
    image: UploadFile = File(...),
    facility_id: int = Form(...),
    mode: str = Form(...),
    kiosk_serial: Optional[str] = Form(None),
    camera_label: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """One kiosk scan cycle: recognize → punch → greeting data.

    The largest face in the frame (closest to the kiosk) is the subject.
    Response::

        {
          "matched": bool,
          "user": {...} | null,
          "punch": {
            "visit_type": "IN"|"OUT",
            "event_time": iso,
            "visit_number": int,
            "duplicate": bool
          } | null,
          "facility_name": str
        }
    """
    mode = (mode or "AUTO").strip().upper()
    if mode not in _VALID_MODES:
        raise HTTPException(status_code=400, detail="mode must be IN, OUT or AUTO")
    facility = _facility_or_http(db)
    if facility_id != facility.id:
        raise HTTPException(status_code=409, detail="kiosk facility configuration is stale")

    img = _decode_frame(await image.read())

    results = recognize_faces(img, db)
    # recognize_faces marks a hit with a resolved ``user_id``; misses
    # carry identity="Unknown" and no user_id.
    identified = [
        r for r in results
        if r.get("user_id") is not None and r.get("identity") != "Unknown"
    ]
    if not identified:
        # faces_detected lets the kiosk distinguish an empty frame
        # (keep scanning silently) from an unknown face (show the
        # "please visit the front desk" message).
        return {"matched": False, "faces_detected": len(results),
                "user": None, "punch": None, "facility_name": facility.display_name}

    primary = max(identified, key=lambda r: _bbox_area(r.get("bbox")))
    user = db.query(User).filter(User.id == primary["user_id"]).first()
    if user is None or not user.is_active:
        return {"matched": False, "faces_detected": len(results),
                "user": None, "punch": None, "facility_name": facility.display_name}

    serial = (kiosk_serial or "").strip() or None
    punch = kiosk_service.record_punch(
        db,
        user=user,
        facility=facility,
        mode=mode,
        kiosk_serial=serial,
        camera_id=(camera_label or "").strip() or None,
    )

    # Best-effort heartbeat for a registered kiosk (central config model).
    if serial:
        try:
            from backend.app.services import kiosk_device_service
            kiosk_device_service.touch_last_seen(db, serial)
        except Exception:
            pass

    return {
        "matched": True,
        "faces_detected": len(results),
        "user": _user_summary(user, punch),
        "punch": {
            "visit_type": punch["visit_type"],
            "event_time": punch["event_time"],
            "visit_number": punch["visit_number"],
            "duplicate": punch["duplicate"],
        },
        "facility_name": facility.display_name,
    }


# ── /prereg ──────────────────────────────────────────────────────────────
class PreRegIn(BaseModel):
    user_id: int
    facility_id: int
    token_type: str = "General"

    prefix: Optional[str] = None
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    gender: Optional[str] = None
    dob: Optional[str] = None  # YYYY-MM-DD
    contact_number: Optional[str] = None
    whatsapp_number: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    pin_code: Optional[str] = None
    national_id_type: Optional[str] = None
    national_id: Optional[str] = None
    next_of_kin_relation: Optional[str] = None
    next_of_kin_name: Optional[str] = None
    next_of_kin_contact: Optional[str] = None
    mrn: Optional[str] = Field(None, max_length=20)


@router.post("/prereg")
def kiosk_prereg(payload: PreRegIn, db: Session = Depends(get_db)):
    """Submit a pre-registration for an identified patient.

    Corrected demographics are written back to the registry; the
    client-shaped payload is stored PENDING for the HIS push.
    """
    if payload.token_type not in _VALID_TOKEN_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"token_type must be one of {sorted(_VALID_TOKEN_TYPES)}",
        )
    facility = _facility_or_http(db)
    if payload.facility_id != facility.id:
        raise HTTPException(status_code=409, detail="kiosk facility configuration is stale")
    user = db.query(User).filter(User.id == payload.user_id).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=404, detail="user not found")

    form = payload.model_dump(exclude={"user_id", "facility_id", "token_type"})
    if form.get("dob"):
        try:
            form["dob"] = datetime.strptime(form["dob"], "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid dob format. Expected YYYY-MM-DD."
            )

    try:
        entry = kiosk_service.submit_pre_registration(
            db, user=user, facility=facility, form=form,
            token_type=payload.token_type,
        )
    except KioskError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"success": True, "pre_registration": kiosk_service.prereg_to_dict(entry)}


# ── /mrn/new ─────────────────────────────────────────────────────────────
@router.get("/mrn/new")
def new_mrn(db: Session = Depends(get_db)):
    """Random unique MRN for the form's generate button."""
    try:
        facility = _facility_or_http(db)
        return {"mrn": kiosk_service.generate_unique_mrn(db, facility.id)}
    except KioskError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── /config (device pulls its central config by serial) ──────────────────
@router.get("/config")
def kiosk_config(serial: str, db: Session = Depends(get_db)):
    """A registered kiosk pulls its server-side config on boot.

    404 when the serial is unknown or the device is deactivated — the
    kiosk then falls back to its on-device setup screen.
    """
    from backend.app.services import kiosk_device_service

    cfg = kiosk_device_service.device_config(db, serial)
    if cfg is None:
        raise HTTPException(status_code=404, detail="kiosk serial not registered")
    return cfg


# ── /setup-options ───────────────────────────────────────────────────────
@router.get("/setup-options")
def setup_options(db: Session = Depends(get_db)):
    """Singleton facility and camera list for the kiosk setup screen.

    Exists so the kiosk device account needs only ``kiosk.operate`` — it
    never touches admin-tier facility or camera read APIs.
    Returns only the few fields the picker needs (no client GUIDs, no
    camera source URLs).
    """
    facility = _facility_or_http(db)
    cameras = []
    try:
        from backend.camera.registry import get_registry

        # registry.list_cameras() returns plain dicts (keys: camera_id,
        # name, role, active, type, source, floor, status).
        for c in get_registry().list_cameras():
            cameras.append({
                "camera_id": c.get("camera_id"),
                "name": c.get("name"),
                "role": c.get("role"),
                "active": c.get("active", False),
            })
    except Exception:
        # Camera system not running (e.g. cloud box) — webcam still works.
        cameras = []
    return {
        "facility": {"id": facility.id, "name": facility.display_name},
        "cameras": cameras,
    }
