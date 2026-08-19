"""
frontdesk_admin_service.py — CRUD for departments, doctors, rooms.

Used by the admin UI. Soft-deletes via ``is_active = False`` instead of
hard delete, so historic ``opd_visits`` rows that reference these rows
keep their FK valid (and their snapshot name/room columns stay readable
even if the row is deactivated).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.app.db.frontdesk_models import Department, Room
from backend.app.db.models import User, UserType


class AdminError(Exception):
    pass


class NotFoundError(AdminError):
    pass


class ConflictError(AdminError):
    pass


# ── Department ───────────────────────────────────────────────────────────
def list_departments(db: Session, *, include_inactive: bool = False) -> List[Department]:
    q = db.query(Department)
    if not include_inactive:
        q = q.filter(Department.is_active.is_(True))
    return q.order_by(Department.name.asc()).all()


def create_department(
    db: Session,
    *,
    code: str,
    name: str,
    token_prefix: str,
    default_room_id: Optional[int] = None,
) -> Department:
    code = code.strip().upper()
    if db.query(Department).filter(Department.code == code).first():
        raise ConflictError(f"department code {code!r} already exists")
    dept = Department(
        code=code,
        name=name.strip(),
        token_prefix=token_prefix.strip().upper(),
        default_room_id=default_room_id,
    )
    db.add(dept)
    db.commit()
    db.refresh(dept)
    return dept


def update_department(db: Session, dept_id: int, updates: Dict[str, Any]) -> Department:
    dept = db.query(Department).filter(Department.id == dept_id).first()
    if dept is None:
        raise NotFoundError(f"department {dept_id} not found")
    EDITABLE = {"name", "token_prefix", "default_room_id", "is_active"}
    for k, v in updates.items():
        if k in EDITABLE and v is not None:
            setattr(dept, k, v)
    db.commit()
    db.refresh(dept)
    return dept


def deactivate_department(db: Session, dept_id: int) -> Department:
    return update_department(db, dept_id, {"is_active": False})


# ── Room ─────────────────────────────────────────────────────────────────
def list_rooms(db: Session, *, include_inactive: bool = False) -> List[Room]:
    q = db.query(Room)
    if not include_inactive:
        q = q.filter(Room.is_active.is_(True))
    return q.order_by(Room.name.asc()).all()


def create_room(
    db: Session, *, name: str, floor: Optional[str] = None, description: Optional[str] = None
) -> Room:
    name = name.strip()
    if db.query(Room).filter(Room.name == name).first():
        raise ConflictError(f"room {name!r} already exists")
    room = Room(name=name, floor=floor, description=description)
    db.add(room)
    db.commit()
    db.refresh(room)
    return room


def update_room(db: Session, room_id: int, updates: Dict[str, Any]) -> Room:
    room = db.query(Room).filter(Room.id == room_id).first()
    if room is None:
        raise NotFoundError(f"room {room_id} not found")
    EDITABLE = {"name", "floor", "description", "is_active"}
    for k, v in updates.items():
        if k in EDITABLE and v is not None:
            setattr(room, k, v)
    db.commit()
    db.refresh(room)
    return room


def deactivate_room(db: Session, room_id: int) -> Room:
    return update_room(db, room_id, {"is_active": False})


# ── Doctor ───────────────────────────────────────────────────────────────
# Doctors live as ``User`` rows with ``user_type='DOCTOR'`` since the
# Phase 1b unification. OPD assignments are carried in
# ``users.opd_department_id`` and ``users.opd_room_id``; the public
# API surface (request payloads, response dicts) keeps the historical
# ``department_id`` / ``room_id`` field names — translation happens in
# the service layer below so callers do not need to change.
_DOCTOR_FIELD_MAP = {
    "department_id": "opd_department_id",
    "room_id": "opd_room_id",
}


def _to_doctor_column(api_field: str) -> str:
    return _DOCTOR_FIELD_MAP.get(api_field, api_field)


def list_doctors(
    db: Session,
    *,
    department_id: Optional[int] = None,
    include_inactive: bool = False,
) -> List[User]:
    q = db.query(User).filter(User.user_type == UserType.DOCTOR.value)
    if not include_inactive:
        q = q.filter(User.is_active.is_(True))
    if department_id is not None:
        q = q.filter(User.opd_department_id == department_id)
    return q.order_by(User.name.asc()).all()


def create_doctor(
    db: Session,
    *,
    name: str,
    department_id: int,
    room_id: Optional[int] = None,
    specialty: Optional[str] = None,
) -> User:
    if db.query(Department).filter(Department.id == department_id).first() is None:
        raise NotFoundError(f"department {department_id} not found")
    doc = User(
        user_type=UserType.DOCTOR.value,
        name=name.strip(),
        opd_department_id=department_id,
        opd_room_id=room_id,
        specialty=specialty,
        is_active=True,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def update_doctor(db: Session, doctor_id: int, updates: Dict[str, Any]) -> User:
    doc = (
        db.query(User)
        .filter(
            User.id == doctor_id,
            User.user_type == UserType.DOCTOR.value,
        )
        .first()
    )
    if doc is None:
        raise NotFoundError(f"doctor {doctor_id} not found")
    EDITABLE = {"name", "department_id", "room_id", "specialty", "is_active"}
    for k, v in updates.items():
        if k in EDITABLE and v is not None:
            setattr(doc, _to_doctor_column(k), v)
    db.commit()
    db.refresh(doc)
    return doc


def deactivate_doctor(db: Session, doctor_id: int) -> User:
    return update_doctor(db, doctor_id, {"is_active": False})


# ── Serialization ────────────────────────────────────────────────────────
def dept_to_dict(d: Department) -> Dict[str, Any]:
    return {
        "id": d.id,
        "code": d.code,
        "name": d.name,
        "token_prefix": d.token_prefix,
        "default_room_id": d.default_room_id,
        "is_active": d.is_active,
    }


def room_to_dict(r: Room) -> Dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "floor": r.floor,
        "description": r.description,
        "is_active": r.is_active,
    }


def doctor_to_dict(d: User) -> Dict[str, Any]:
    """Serialise a doctor (``User`` with ``user_type='DOCTOR'``) using
    the legacy field names expected by the frontend."""
    return {
        "id": d.id,
        "name": d.name,
        "department_id": d.opd_department_id,
        "room_id": d.opd_room_id,
        "specialty": d.specialty,
        "is_active": d.is_active,
    }
