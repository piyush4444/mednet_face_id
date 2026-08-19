"""
frontdesk_admin.py — CRUD endpoints for departments, doctors, rooms.

Mounted under /api/v1/frontdesk/admin/*.

There is no built-in auth here; pair with the existing auth middleware
(or reverse-proxy auth) when deploying. The endpoints are intentionally
small and explicit — no clever generic CRUD.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.db.postgres import get_db
from backend.app.services import frontdesk_admin_service as svc
from backend.app.services.frontdesk_admin_service import (
    ConflictError,
    NotFoundError,
)

router = APIRouter(prefix="/frontdesk/admin", tags=["frontdesk-admin"])


# ── Schemas ──────────────────────────────────────────────────────────────
class DepartmentIn(BaseModel):
    code: str
    name: str
    token_prefix: str
    default_room_id: Optional[int] = None


class DepartmentPatch(BaseModel):
    name: Optional[str] = None
    token_prefix: Optional[str] = None
    default_room_id: Optional[int] = None
    is_active: Optional[bool] = None


class RoomIn(BaseModel):
    name: str
    floor: Optional[str] = None
    description: Optional[str] = None


class RoomPatch(BaseModel):
    name: Optional[str] = None
    floor: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class DoctorIn(BaseModel):
    name: str
    department_id: int
    room_id: Optional[int] = None
    specialty: Optional[str] = None


class DoctorPatch(BaseModel):
    name: Optional[str] = None
    department_id: Optional[int] = None
    room_id: Optional[int] = None
    specialty: Optional[str] = None
    is_active: Optional[bool] = None


# ── Departments ──────────────────────────────────────────────────────────
@router.get("/departments")
def list_departments(include_inactive: bool = False, db: Session = Depends(get_db)):
    rows = svc.list_departments(db, include_inactive=include_inactive)
    return {"departments": [svc.dept_to_dict(d) for d in rows]}


@router.post("/departments")
def create_department(payload: DepartmentIn, db: Session = Depends(get_db)):
    try:
        dept = svc.create_department(
            db,
            code=payload.code,
            name=payload.name,
            token_prefix=payload.token_prefix,
            default_room_id=payload.default_room_id,
        )
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return svc.dept_to_dict(dept)


@router.patch("/departments/{dept_id}")
def patch_department(dept_id: int, payload: DepartmentPatch, db: Session = Depends(get_db)):
    try:
        dept = svc.update_department(db, dept_id, payload.model_dump(exclude_unset=True))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return svc.dept_to_dict(dept)


@router.delete("/departments/{dept_id}")
def delete_department(dept_id: int, db: Session = Depends(get_db)):
    try:
        dept = svc.deactivate_department(db, dept_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return svc.dept_to_dict(dept)


# ── Rooms ────────────────────────────────────────────────────────────────
@router.get("/rooms")
def list_rooms(include_inactive: bool = False, db: Session = Depends(get_db)):
    rows = svc.list_rooms(db, include_inactive=include_inactive)
    return {"rooms": [svc.room_to_dict(r) for r in rows]}


@router.post("/rooms")
def create_room(payload: RoomIn, db: Session = Depends(get_db)):
    try:
        room = svc.create_room(
            db, name=payload.name, floor=payload.floor, description=payload.description
        )
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return svc.room_to_dict(room)


@router.patch("/rooms/{room_id}")
def patch_room(room_id: int, payload: RoomPatch, db: Session = Depends(get_db)):
    try:
        room = svc.update_room(db, room_id, payload.model_dump(exclude_unset=True))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return svc.room_to_dict(room)


@router.delete("/rooms/{room_id}")
def delete_room(room_id: int, db: Session = Depends(get_db)):
    try:
        room = svc.deactivate_room(db, room_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return svc.room_to_dict(room)


# ── Doctors ──────────────────────────────────────────────────────────────
@router.get("/doctors")
def list_doctors(
    department_id: Optional[int] = None,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
):
    rows = svc.list_doctors(
        db, department_id=department_id, include_inactive=include_inactive
    )
    return {"doctors": [svc.doctor_to_dict(d) for d in rows]}


@router.post("/doctors")
def create_doctor(payload: DoctorIn, db: Session = Depends(get_db)):
    try:
        doc = svc.create_doctor(
            db,
            name=payload.name,
            department_id=payload.department_id,
            room_id=payload.room_id,
            specialty=payload.specialty,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return svc.doctor_to_dict(doc)


@router.patch("/doctors/{doctor_id}")
def patch_doctor(doctor_id: int, payload: DoctorPatch, db: Session = Depends(get_db)):
    try:
        doc = svc.update_doctor(db, doctor_id, payload.model_dump(exclude_unset=True))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return svc.doctor_to_dict(doc)


@router.delete("/doctors/{doctor_id}")
def delete_doctor(doctor_id: int, db: Session = Depends(get_db)):
    try:
        doc = svc.deactivate_doctor(db, doctor_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return svc.doctor_to_dict(doc)
