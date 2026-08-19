"""PostgreSQL-backed persistent camera roster.

The original ``load/get/add/update/remove`` API and dictionary shape are
preserved so the registry, workers, tracking routes, and frontend do not need
to know that persistence moved from JSON to ``camera_master``.

The old JSON file is read only by :func:`seed_from_legacy_json`. A durable
``data_migrations`` marker makes that import truly one-time; the file is never
written and is not a fallback source after the database cutover.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import func

from backend.app.db.facility_models import (
    Camera,
    CameraType,
    DataMigration,
    Facility,
    Location,
)
from backend.app.db.postgres import SessionLocal

logger = logging.getLogger(__name__)

_LEGACY_STORE_PATH = Path(__file__).resolve().parents[2] / "database" / "cameras.json"
_LEGACY_IMPORT_KEY = "camera_roster_json_to_postgres_v1"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_camera_id() -> str:
    return f"cam_{uuid.uuid4().hex[:8]}"


def _event_type_for_role(role: str) -> str:
    return {
        "entry": CameraType.IN.value,
        "exit": CameraType.OUT.value,
        "inside": CameraType.TRACKER_IN.value,
    }.get(role, CameraType.TRACKER_IN.value)


def _serialize(row: Camera) -> dict:
    source: str | int = row.stream_url
    if row.source_type == "usb":
        try:
            source = int(row.stream_url)
        except (TypeError, ValueError):
            pass
    return {
        "camera_id": row.code,
        "name": row.name,
        "type": row.source_type,
        "source": source,
        "floor": row.floor,
        "role": row.role,
        "active": row.is_active,
        "facility_id": row.facility_id,
        "location_id": row.location_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at or row.created_at,
    }


def _default_facility_id(db) -> int:
    from backend.app.services.facility_service import get_single_facility

    return get_single_facility(db).id


def _resolve_placement(
    db, spec: dict, *, current: Camera | None = None
) -> tuple[int, int | None]:
    facility_id = _default_facility_id(db)

    location_id = spec.get("location_id")
    if location_id is None and current is not None:
        location_id = current.location_id
    if location_id is not None:
        location = db.get(Location, location_id)
        if location is None:
            raise ValueError(f"location_id {location_id} does not exist")
        if location.facility_id != facility_id:
            raise ValueError("location must belong to the selected facility")
    return facility_id, location_id


def load() -> list[dict]:
    """Return the full database roster in stable camera-dictionary form."""
    with SessionLocal() as db:
        rows = db.query(Camera).order_by(Camera.id).all()
        return [_serialize(row) for row in rows]


def get(camera_id: str) -> Optional[dict]:
    with SessionLocal() as db:
        row = db.query(Camera).filter(Camera.code == camera_id).first()
        return _serialize(row) if row is not None else None


def add(spec: dict) -> dict:
    """Insert a camera and return its runtime representation."""
    with SessionLocal() as db:
        name = spec["name"].strip()
        duplicate = db.query(Camera.id).filter(func.lower(Camera.name) == name.lower()).first()
        if duplicate is not None:
            raise ValueError(f"Camera with name '{name}' already exists")

        facility_id, location_id = _resolve_placement(db, spec)
        now = _utcnow()
        row = Camera(
            code=_new_camera_id(),
            name=name,
            source_type=spec["type"],
            stream_url=str(spec["source"]),
            floor=spec["floor"],
            role=spec["role"],
            camera_type=_event_type_for_role(spec["role"]),
            is_active=bool(spec.get("active", True)),
            facility_id=facility_id,
            location_id=location_id,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("camera added id=%s name=%s", row.code, row.name)
        return _serialize(row)


def update(camera_id: str, patch: dict) -> dict:
    """Apply a partial update and return the resulting camera record."""
    with SessionLocal() as db:
        row = db.query(Camera).filter(Camera.code == camera_id).first()
        if row is None:
            raise KeyError(camera_id)

        if "name" in patch and patch["name"] is not None:
            name = patch["name"].strip()
            duplicate = (
                db.query(Camera.id)
                .filter(func.lower(Camera.name) == name.lower(), Camera.id != row.id)
                .first()
            )
            if duplicate is not None:
                raise ValueError(f"Camera with name '{name}' already exists")

        placement_requested = "location_id" in patch
        placement_changed = False
        if placement_requested:
            facility_id, location_id = _resolve_placement(db, patch, current=row)
            if row.facility_id != facility_id or row.location_id != location_id:
                row.facility_id = facility_id
                row.location_id = location_id
                placement_changed = True

        field_map = {
            "name": "name",
            "type": "source_type",
            "source": "stream_url",
            "floor": "floor",
            "role": "role",
            "active": "is_active",
        }
        changed = placement_changed
        for public_name, column_name in field_map.items():
            value = patch.get(public_name)
            if value is None:
                continue
            if public_name == "name":
                value = value.strip()
            if public_name == "source":
                value = str(value)
            if getattr(row, column_name) != value:
                setattr(row, column_name, value)
                changed = True

        if "role" in patch and patch["role"] is not None:
            event_type = _event_type_for_role(patch["role"])
            if row.camera_type != event_type:
                row.camera_type = event_type
                changed = True
        if changed:
            row.updated_at = _utcnow()
            db.commit()
            db.refresh(row)
            logger.info("camera updated id=%s keys=%s", camera_id, sorted(patch))
        return _serialize(row)


def remove(camera_id: str) -> dict:
    with SessionLocal() as db:
        row = db.query(Camera).filter(Camera.code == camera_id).first()
        if row is None:
            raise KeyError(camera_id)
        result = _serialize(row)
        db.delete(row)
        db.commit()
        logger.info("camera removed id=%s name=%s", camera_id, result["name"])
        return result


def seed_from_legacy_json() -> int:
    """Transactionally import JSON once and persist a durable marker."""
    with SessionLocal() as db:
        if db.get(DataMigration, _LEGACY_IMPORT_KEY) is not None:
            return 0
        if db.query(Camera.id).first() is not None:
            db.add(DataMigration(key=_LEGACY_IMPORT_KEY))
            db.commit()
            return 0
        if not _LEGACY_STORE_PATH.exists():
            db.add(DataMigration(key=_LEGACY_IMPORT_KEY))
            db.commit()
            return 0

        with _LEGACY_STORE_PATH.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
        cameras = document.get("cameras") if isinstance(document, dict) else None
        if not isinstance(cameras, list):
            raise ValueError(f"legacy camera store at {_LEGACY_STORE_PATH} is malformed")

        default_facility_id = _default_facility_id(db)
        for item in cameras:
            facility_id = item.get("facility_id") or default_facility_id
            location_id = item.get("location_id")
            _resolve_placement(db, {"facility_id": facility_id, "location_id": location_id})
            created_at = _parse_legacy_datetime(item.get("created_at"))
            updated_at = _parse_legacy_datetime(item.get("updated_at")) or created_at
            role = item.get("role", "inside")
            db.add(Camera(
                code=item["camera_id"],
                name=item["name"].strip(),
                source_type=item.get("type", "rtsp"),
                stream_url=str(item["source"]),
                floor=item.get("floor", "unknown"),
                role=role,
                camera_type=_event_type_for_role(role),
                is_active=bool(item.get("active", True)),
                facility_id=facility_id,
                location_id=location_id,
                created_at=created_at or _utcnow(),
                updated_at=updated_at or _utcnow(),
            ))
        db.add(DataMigration(key=_LEGACY_IMPORT_KEY))
        db.commit()
        logger.info("imported %d camera(s) from legacy JSON store", len(cameras))
        return len(cameras)


def _parse_legacy_datetime(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def legacy_store_path() -> Path:
    """Return the read-only migration source path for diagnostics."""
    return _LEGACY_STORE_PATH
