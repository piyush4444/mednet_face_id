"""
mappings.py — shelved CRUD for person × facility mappings.

This router is intentionally not mounted in ``app.api.router``. Keep it with
the service implementation for the future Facility Roles workstream.

When re-enabled, mount under /api/v1/mappings guarded by ``users.write`` (managing a
person's per-facility role/MRN is a user-administration action). See
mapping_service for the model.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.db.postgres import get_db
from backend.app.services import mapping_service as svc
from backend.app.services.mapping_service import (
    ConflictError,
    NotFoundError,
    ValidationError,
)

router = APIRouter(prefix="/mappings", tags=["mappings"])


class MappingIn(BaseModel):
    person_id: int
    facility_id: int
    person_type: str
    visitor_subtype: Optional[str] = None
    mrn: Optional[str] = Field(default=None, max_length=20)


class MappingPatch(BaseModel):
    person_type: Optional[str] = None
    visitor_subtype: Optional[str] = None
    mrn: Optional[str] = Field(default=None, max_length=20)
    is_active: Optional[bool] = None


@router.get("")
def list_mappings(
    facility_id: Optional[int] = None,
    person_type: Optional[str] = None,
    person_id: Optional[int] = None,
    include_inactive: bool = True,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    return {"mappings": svc.list_mappings(
        db, facility_id=facility_id, person_type=person_type,
        person_id=person_id, include_inactive=include_inactive, limit=limit)}


@router.post("")
def create_mapping(payload: MappingIn, db: Session = Depends(get_db)):
    try:
        return svc.create_mapping(db, **payload.model_dump())
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/{mapping_id}")
def patch_mapping(mapping_id: int, payload: MappingPatch, db: Session = Depends(get_db)):
    try:
        return svc.update_mapping(db, mapping_id, payload.model_dump(exclude_unset=True))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{mapping_id}")
def delete_mapping(mapping_id: int, db: Session = Depends(get_db)):
    try:
        return svc.deactivate_mapping(db, mapping_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
