"""
facilities.py — CRUD endpoints for facilities (FACILITY_MASTER).

Mounted under /api/v1/facilities. Facilities are the hospitals / sites
this deployment serves; each row carries the client-HIS identifiers
(facilityGuid, companyID, queue setup) used by the outbound
integrations. Managed from Settings → Facilities in the admin UI.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.db.postgres import get_db
from backend.app.services import facility_service as svc
from backend.app.services.facility_service import ConflictError, NotFoundError

router = APIRouter(prefix="/facilities", tags=["facilities"])


# ── Schemas ──────────────────────────────────────────────────────────────
class FacilityIn(BaseModel):
    display_name: str
    code: str
    facility_guid: str
    regn_number: int
    contact_number: str
    primary_contact_person: Optional[str] = None
    address: Optional[str] = None
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pin_code: Optional[str] = None
    client_company_id: Optional[int] = None
    integration_config: Optional[Dict[str, Any]] = None


class FacilityPatch(BaseModel):
    code: Optional[str] = None
    display_name: Optional[str] = None
    facility_guid: Optional[str] = None
    regn_number: Optional[int] = None
    contact_number: Optional[str] = None
    primary_contact_person: Optional[str] = None
    address: Optional[str] = None
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pin_code: Optional[str] = None
    client_company_id: Optional[int] = None
    integration_config: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


# ── Routes ───────────────────────────────────────────────────────────────
@router.get("")
def list_facilities(include_inactive: bool = False, db: Session = Depends(get_db)):
    rows = svc.list_facilities(db, include_inactive=include_inactive)
    return {"facilities": [svc.facility_to_dict(f) for f in rows]}


@router.get("/{facility_id}")
def get_facility(facility_id: int, db: Session = Depends(get_db)):
    try:
        fac = svc.get_facility(db, facility_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return svc.facility_to_dict(fac)


@router.post("")
def create_facility(payload: FacilityIn, db: Session = Depends(get_db)):
    try:
        fac = svc.create_facility(db, **payload.model_dump())
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return svc.facility_to_dict(fac)


@router.patch("/{facility_id}")
def patch_facility(facility_id: int, payload: FacilityPatch, db: Session = Depends(get_db)):
    try:
        fac = svc.update_facility(db, facility_id, payload.model_dump(exclude_unset=True))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return svc.facility_to_dict(fac)


@router.delete("/{facility_id}")
def delete_facility(facility_id: int, db: Session = Depends(get_db)):
    try:
        fac = svc.deactivate_facility(db, facility_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return svc.facility_to_dict(fac)
