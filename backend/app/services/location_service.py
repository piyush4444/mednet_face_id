"""
location_service.py — CRUD for locations (LOCATION_MASTER).

Locations are the named places inside a facility (floor / corridor /
room / gate / ward), hierarchical via ``parent_location_id`` — a room
sits under a corridor under a floor. Cameras and tracking-log rows will
reference these; the OPD ``rooms`` table is a separate, older concept
left untouched for now.

Invariants enforced here (not just in the UI):
- a location's parent must belong to the **same facility**;
- the parent chain must stay acyclic (no location can be its own
  ancestor);
- ``location_type`` is one of :class:`LocationType`.

Soft-delete via ``is_active = False`` so historic tracking logs keep a
valid FK.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.app.db.facility_models import Facility, Location, LocationType


class LocationError(Exception):
    pass


class NotFoundError(LocationError):
    pass


class ValidationError(LocationError):
    pass


_VALID_TYPES = {t.value for t in LocationType}


def _require_facility(db: Session, facility_id: int) -> Facility:
    fac = db.query(Facility).filter(Facility.id == facility_id).first()
    if fac is None:
        raise NotFoundError(f"facility {facility_id} not found")
    return fac


def _validate_parent(
    db: Session, facility_id: int, parent_id: Optional[int], self_id: Optional[int] = None
) -> None:
    """Parent must exist, be in the same facility, and not create a cycle."""
    if parent_id is None:
        return
    if parent_id == self_id:
        raise ValidationError("a location cannot be its own parent")
    parent = db.query(Location).filter(Location.id == parent_id).first()
    if parent is None:
        raise NotFoundError(f"parent location {parent_id} not found")
    if parent.facility_id != facility_id:
        raise ValidationError("parent location belongs to a different facility")
    # Walk up from the proposed parent; if we reach self_id, it's a cycle.
    seen = set()
    node = parent
    while node is not None:
        if node.id in seen:  # defensive — existing data cycle
            break
        seen.add(node.id)
        if self_id is not None and node.id == self_id:
            raise ValidationError("parent assignment would create a cycle")
        node = (
            db.query(Location).filter(Location.id == node.parent_location_id).first()
            if node.parent_location_id
            else None
        )


def list_locations(
    db: Session, *, facility_id: Optional[int] = None, include_inactive: bool = False
) -> List[Location]:
    q = db.query(Location)
    if facility_id is not None:
        q = q.filter(Location.facility_id == facility_id)
    if not include_inactive:
        q = q.filter(Location.is_active.is_(True))
    return q.order_by(Location.name.asc()).all()


def create_location(
    db: Session,
    *,
    facility_id: int,
    name: str,
    location_type: str = LocationType.OTHER.value,
    parent_location_id: Optional[int] = None,
    description: Optional[str] = None,
) -> Location:
    _require_facility(db, facility_id)
    name = (name or "").strip()
    if not name:
        raise ValidationError("name is required")
    location_type = (location_type or LocationType.OTHER.value).strip().upper()
    if location_type not in _VALID_TYPES:
        raise ValidationError(
            f"invalid location_type {location_type!r}; allowed: {sorted(_VALID_TYPES)}"
        )
    _validate_parent(db, facility_id, parent_location_id)

    loc = Location(
        facility_id=facility_id,
        name=name,
        location_type=location_type,
        parent_location_id=parent_location_id,
        description=description,
    )
    db.add(loc)
    db.commit()
    db.refresh(loc)
    return loc


def update_location(db: Session, location_id: int, updates: Dict[str, Any]) -> Location:
    loc = db.query(Location).filter(Location.id == location_id).first()
    if loc is None:
        raise NotFoundError(f"location {location_id} not found")

    if "location_type" in updates and updates["location_type"] is not None:
        lt = str(updates["location_type"]).strip().upper()
        if lt not in _VALID_TYPES:
            raise ValidationError(f"invalid location_type {lt!r}")
        updates["location_type"] = lt

    if "parent_location_id" in updates:
        _validate_parent(
            db, loc.facility_id, updates["parent_location_id"], self_id=loc.id
        )

    EDITABLE = {"name", "location_type", "parent_location_id", "description", "is_active"}
    for k, v in updates.items():
        if k not in EDITABLE:
            continue
        # parent_location_id may legitimately be set to None (detach);
        # every other field ignores None (partial patch).
        if v is None and k != "parent_location_id":
            continue
        setattr(loc, k, v)
    db.commit()
    db.refresh(loc)
    return loc


def deactivate_location(db: Session, location_id: int) -> Location:
    return update_location(db, location_id, {"is_active": False})


def location_to_dict(loc: Location) -> Dict[str, Any]:
    return {
        "id": loc.id,
        "facility_id": loc.facility_id,
        "parent_location_id": loc.parent_location_id,
        "name": loc.name,
        "location_type": loc.location_type,
        "description": loc.description,
        "is_active": loc.is_active,
    }
