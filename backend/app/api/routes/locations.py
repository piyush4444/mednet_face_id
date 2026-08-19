"""
locations.py — CRUD endpoints for locations (LOCATION_MASTER).

Mounted under /api/v1/locations. Locations are the named, hierarchical
places inside a facility (floor / corridor / room / gate / ward) that
cameras and tracking logs reference. Managed from Settings → Locations.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.db.postgres import get_db
from backend.app.services import location_service as svc
from backend.app.services.facility_service import (
    ConflictError as FacilityConflict,
    NotFoundError as FacilityNotFound,
    get_single_facility,
)
from backend.app.services.location_service import NotFoundError, ValidationError

router = APIRouter(prefix="/locations", tags=["locations"])


class LocationIn(BaseModel):
    name: str
    location_type: str = "OTHER"
    parent_location_id: Optional[int] = None
    description: Optional[str] = None


class LocationPatch(BaseModel):
    name: Optional[str] = None
    location_type: Optional[str] = None
    parent_location_id: Optional[int] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


@router.get("")
def list_locations(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
):
    try:
        facility = get_single_facility(db)
    except FacilityNotFound as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except FacilityConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    rows = svc.list_locations(db, facility_id=facility.id, include_inactive=include_inactive)
    return {"locations": [svc.location_to_dict(r) for r in rows]}


@router.post("")
def create_location(payload: LocationIn, db: Session = Depends(get_db)):
    try:
        facility = get_single_facility(db)
        loc = svc.create_location(db, facility_id=facility.id, **payload.model_dump())
    except FacilityNotFound as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except FacilityConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return svc.location_to_dict(loc)


@router.patch("/{location_id}")
def patch_location(location_id: int, payload: LocationPatch, db: Session = Depends(get_db)):
    try:
        loc = svc.update_location(
            db, location_id, payload.model_dump(exclude_unset=True)
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return svc.location_to_dict(loc)


@router.delete("/{location_id}")
def delete_location(location_id: int, db: Session = Depends(get_db)):
    try:
        loc = svc.deactivate_location(db, location_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return svc.location_to_dict(loc)
