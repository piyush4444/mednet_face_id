"""
kiosk_device_service.py — central kiosk registry + kiosk activity/log reads.

Two concerns for the admin "Kiosk" section:

- **Devices**: register/edit kiosks (serial, facility, mode, camera source),
  provision their minimal device login, and serve each device its config by
  serial so the physical kiosk needs no on-device setup.
- **Logs**: read-only views over the kiosk's own data — activity (who was
  punched, IN/OUT), pre-registrations (token + status), and attendance
  exports (outbound punch deliveries) — with person/facility names joined.

Device CRUD + logs are admin-tier (``kiosk.manage``); the config-by-serial
read is device-tier (``kiosk.operate``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.facility_models import (
    Facility,
    KioskDevice,
    PersonTrackingLog,
    PersonVisitMapping,
    PreRegistrationLog,
    PunchExport,
    TrackingSource,
)
from backend.app.db.models import User
from backend.app.services import auth_service
from backend.app.utils.time_ist import now_ist

_VALID_MODES = {"IN", "OUT", "AUTO"}
_VALID_SOURCES = {"webcam", "system"}


class KioskDeviceError(Exception):
    pass


class NotFoundError(KioskDeviceError):
    pass


class ConflictError(KioskDeviceError):
    pass


class ValidationError(KioskDeviceError):
    pass


# ── Device CRUD ──────────────────────────────────────────────────────────
def list_devices(db: Session, *, include_inactive: bool = True) -> List[KioskDevice]:
    q = db.query(KioskDevice)
    if not include_inactive:
        q = q.filter(KioskDevice.is_active.is_(True))
    return q.order_by(KioskDevice.name.asc()).all()


def get_device(db: Session, device_id: int) -> KioskDevice:
    d = db.query(KioskDevice).filter(KioskDevice.id == device_id).first()
    if d is None:
        raise NotFoundError(f"kiosk device {device_id} not found")
    return d


def get_device_by_serial(db: Session, serial: str) -> Optional[KioskDevice]:
    return db.query(KioskDevice).filter(KioskDevice.serial == serial.strip()).first()


def _validate(mode: str, source_type: str) -> None:
    if mode.upper() not in _VALID_MODES:
        raise ValidationError(f"mode must be one of {sorted(_VALID_MODES)}")
    if source_type not in _VALID_SOURCES:
        raise ValidationError(f"source_type must be one of {sorted(_VALID_SOURCES)}")


def create_device(
    db: Session,
    *,
    serial: str,
    name: str,
    facility_id: Optional[int] = None,
    mode: str = "AUTO",
    source_type: str = "webcam",
    camera_id: Optional[str] = None,
    dup_window: Optional[int] = None,
) -> KioskDevice:
    serial = (serial or "").strip()
    if not serial:
        raise ValidationError("serial is required")
    if not (name or "").strip():
        raise ValidationError("name is required")
    _validate(mode, source_type)
    if get_device_by_serial(db, serial) is not None:
        raise ConflictError(f"kiosk serial {serial!r} already registered")
    dev = KioskDevice(
        serial=serial,
        name=name.strip(),
        facility_id=facility_id,
        mode=mode.upper(),
        source_type=source_type,
        camera_id=(camera_id or None),
        dup_window=dup_window,
    )
    db.add(dev)
    db.commit()
    db.refresh(dev)
    return dev


def update_device(db: Session, device_id: int, updates: Dict[str, Any]) -> KioskDevice:
    dev = get_device(db, device_id)
    if "mode" in updates and updates["mode"]:
        m = str(updates["mode"]).upper()
        if m not in _VALID_MODES:
            raise ValidationError("invalid mode")
        updates["mode"] = m
    if "source_type" in updates and updates["source_type"]:
        if updates["source_type"] not in _VALID_SOURCES:
            raise ValidationError("invalid source_type")
    EDITABLE = {
        "name", "facility_id", "mode", "source_type", "camera_id",
        "dup_window", "is_active",
    }
    for k, v in updates.items():
        if k in EDITABLE:
            setattr(dev, k, v)
    db.commit()
    db.refresh(dev)
    return dev


def deactivate_device(db: Session, device_id: int) -> KioskDevice:
    return update_device(db, device_id, {"is_active": False})


def touch_last_seen(db: Session, serial: str) -> None:
    """Best-effort heartbeat — called on each kiosk scan."""
    dev = get_device_by_serial(db, serial)
    if dev is not None:
        dev.last_seen_at = now_ist()
        db.commit()


def provision_account(db: Session, device_id: int, *, username: str, password: str):
    """Create a minimal kiosk device login and link it to the device."""
    dev = get_device(db, device_id)
    try:
        acct = auth_service.create_kiosk_account(db, username=username, password=password)
    except ValueError as exc:
        raise ConflictError(str(exc))
    dev.account_id = acct.id
    db.commit()
    return acct


# ── Config the physical device pulls by serial ───────────────────────────
def device_config(db: Session, serial: str) -> Optional[Dict[str, Any]]:
    dev = get_device_by_serial(db, serial)
    if dev is None or not dev.is_active:
        return None
    fac = (
        db.query(Facility).filter(Facility.id == dev.facility_id).first()
        if dev.facility_id else None
    )
    return {
        "serial": dev.serial,
        "name": dev.name,
        "facility_id": dev.facility_id,
        "facility_name": fac.display_name if fac else None,
        "mode": dev.mode,
        "source_type": dev.source_type,
        "camera_id": dev.camera_id,
        "dup_window": dev.dup_window or settings.KIOSK_DUPLICATE_WINDOW,
    }


def device_to_dict(dev: KioskDevice) -> Dict[str, Any]:
    return {
        "id": dev.id,
        "serial": dev.serial,
        "name": dev.name,
        "facility_id": dev.facility_id,
        "facility_name": dev.facility.display_name if dev.facility else None,
        "mode": dev.mode,
        "source_type": dev.source_type,
        "camera_id": dev.camera_id,
        "dup_window": dev.dup_window,
        "account_id": dev.account_id,
        "is_active": dev.is_active,
        "last_seen_at": dev.last_seen_at,
        "created_at": dev.created_at,
    }


# ── Logs (read-only) ─────────────────────────────────────────────────────
def list_activity(
    db: Session, *, facility_id: Optional[int] = None,
    visit_type: Optional[str] = None, limit: int = 100,
) -> List[Dict[str, Any]]:
    """Kiosk punch events, newest first, with person + facility names."""
    q = (
        db.query(PersonTrackingLog, PersonVisitMapping, User, Facility)
        .join(PersonVisitMapping, PersonTrackingLog.person_facility_id == PersonVisitMapping.id)
        .join(User, PersonVisitMapping.person_id == User.id)
        .outerjoin(Facility, PersonVisitMapping.facility_id == Facility.id)
        .filter(PersonTrackingLog.source == TrackingSource.KIOSK.value)
    )
    if facility_id is not None:
        q = q.filter(PersonVisitMapping.facility_id == facility_id)
    if visit_type:
        q = q.filter(PersonTrackingLog.visit_type == visit_type.upper())
    rows = q.order_by(PersonTrackingLog.event_time.desc()).limit(min(limit, 500)).all()
    return [
        {
            "id": log.id,
            "person_name": user.name,
            "person_type": mapping.person_type,
            "mrn": mapping.mrn,
            "visit_type": log.visit_type,
            "facility_name": fac.display_name if fac else None,
            "camera_id": log.camera_id,
            "visit_number": log.visit_number,
            "event_time": log.event_time,
        }
        for log, mapping, user, fac in rows
    ]


def list_preregs(
    db: Session, *, facility_id: Optional[int] = None,
    status: Optional[str] = None, limit: int = 100,
) -> List[Dict[str, Any]]:
    q = (
        db.query(PreRegistrationLog, User, Facility)
        .outerjoin(PersonVisitMapping, PreRegistrationLog.person_facility_id == PersonVisitMapping.id)
        .outerjoin(User, PersonVisitMapping.person_id == User.id)
        .outerjoin(Facility, PreRegistrationLog.facility_id == Facility.id)
    )
    if facility_id is not None:
        q = q.filter(PreRegistrationLog.facility_id == facility_id)
    if status:
        q = q.filter(PreRegistrationLog.status == status.upper())
    rows = q.order_by(PreRegistrationLog.created_at.desc()).limit(min(limit, 500)).all()
    return [
        {
            "id": pre.id,
            "person_name": user.name if user else None,
            "token_no": pre.token_no,
            "pre_regn_id": pre.pre_regn_id,
            "status": pre.status,
            "facility_name": fac.display_name if fac else None,
            "last_error": pre.last_error,
            "created_at": pre.created_at,
        }
        for pre, user, fac in rows
    ]


def list_exports(
    db: Session, *, status: Optional[str] = None, limit: int = 100,
) -> List[Dict[str, Any]]:
    q = (
        db.query(PunchExport, User)
        .outerjoin(PersonTrackingLog, PunchExport.visit_log_id == PersonTrackingLog.id)
        .outerjoin(PersonVisitMapping, PersonTrackingLog.person_facility_id == PersonVisitMapping.id)
        .outerjoin(User, PersonVisitMapping.person_id == User.id)
    )
    if status:
        q = q.filter(PunchExport.status == status.upper())
    rows = q.order_by(PunchExport.created_at.desc()).limit(min(limit, 500)).all()
    return [
        {
            "id": exp.id,
            "person_name": user.name if user else None,
            "biometric_idx": exp.biometric_idx,
            "status": exp.status,
            "attempts": exp.attempts,
            "last_error": exp.last_error,
            "sent_at": exp.sent_at,
            "created_at": exp.created_at,
        }
        for exp, user in rows
    ]
