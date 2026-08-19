"""
mapping_service.py — manage person × facility mappings (PERSON_VISIT_MAPPING).

This is where the centralized-identity model is administered. The
person-to-facility row remains an internal lifecycle record for the singleton
deployment and stores operational person type, facility-issued MRN, and the
running visit counter.

Reads need ``users.read``; writes need ``users.write`` (guarded at the
router). MRN is unique per facility (partial index); duplicate (person,
facility) is rejected.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.db.facility_models import (
    Facility,
    PersonFacility,
    PersonFacilityVersion,
    PersonVisitMapping,
    VisitorSubtype,
)
from backend.app.db.models import User, UserType
from backend.app.utils.time_ist import now_ist

_VALID_TYPES = {t.value for t in UserType}
_VALID_SUBTYPES = {s.value for s in VisitorSubtype}

# Attributes whose change opens a new membership version (SCD Type 2).
_VERSIONED_ATTRS = ("person_type", "visitor_subtype", "mrn", "is_active")


def record_version(
    db: Session,
    mapping: PersonFacility,
    *,
    changed_by: Optional[int] = None,
    reason: Optional[str] = None,
) -> PersonFacilityVersion:
    """Write the SCD-2 version row for the mapping's *current* values.

    Closes any open (``is_current``) version and opens the next one, bumping
    ``mapping.current_version``. Snapshots ``person_type`` / ``visitor_subtype``
    / ``mrn`` / ``is_active`` as they stand on ``mapping`` now, so call this
    *after* applying the change and within the same transaction; the caller
    commits. Idempotent enough to be the single writer of version rows —
    ``person_visit_log`` records the resulting ``version_number``.
    """
    now = now_ist()
    open_rows = (
        db.query(PersonFacilityVersion)
        .filter(
            PersonFacilityVersion.person_facility_id == mapping.id,
            PersonFacilityVersion.is_current.is_(True),
        )
        .all()
    )
    for row in open_rows:
        row.is_current = False
        row.valid_to = now

    max_num = (
        db.query(func.max(PersonFacilityVersion.version_number))
        .filter(PersonFacilityVersion.person_facility_id == mapping.id)
        .scalar()
    ) or 0
    next_num = max_num + 1

    ver = PersonFacilityVersion(
        person_facility_id=mapping.id,
        version_number=next_num,
        person_type=mapping.person_type,
        visitor_subtype=mapping.visitor_subtype,
        mrn=mapping.mrn,
        is_active=mapping.is_active,
        valid_from=now,
        valid_to=None,
        is_current=True,
        change_reason=reason,
        changed_by=changed_by,
    )
    db.add(ver)
    mapping.current_version = next_num
    db.flush()
    return ver


class MappingError(Exception):
    pass


class NotFoundError(MappingError):
    pass


class ConflictError(MappingError):
    pass


class ValidationError(MappingError):
    pass


def _row_to_dict(m: PersonVisitMapping, person: User, fac: Optional[Facility]) -> Dict[str, Any]:
    return {
        "id": m.id,
        "person_id": m.person_id,
        "person_name": person.name if person else None,
        "facility_id": m.facility_id,
        "facility_name": fac.display_name if fac else None,
        "person_type": m.person_type,
        "visitor_subtype": m.visitor_subtype,
        "mrn": m.mrn,
        "visit_count": m.visit_count,
        "last_visit_at": m.last_visit_at,
        "is_active": m.is_active,
    }


def list_mappings(
    db: Session, *, facility_id: Optional[int] = None,
    person_type: Optional[str] = None, person_id: Optional[int] = None,
    include_inactive: bool = True, limit: int = 200,
) -> List[Dict[str, Any]]:
    q = (
        db.query(PersonVisitMapping, User, Facility)
        .join(User, PersonVisitMapping.person_id == User.id)
        .outerjoin(Facility, PersonVisitMapping.facility_id == Facility.id)
    )
    if facility_id is not None:
        q = q.filter(PersonVisitMapping.facility_id == facility_id)
    if person_type:
        q = q.filter(PersonVisitMapping.person_type == person_type.upper())
    if person_id is not None:
        q = q.filter(PersonVisitMapping.person_id == person_id)
    if not include_inactive:
        q = q.filter(PersonVisitMapping.is_active.is_(True))
    rows = q.order_by(PersonVisitMapping.updated_at.desc().nullslast()).limit(min(limit, 500)).all()
    return [_row_to_dict(m, u, f) for m, u, f in rows]


def _validate_type(person_type: str, visitor_subtype: Optional[str]) -> None:
    if person_type.upper() not in _VALID_TYPES:
        raise ValidationError(f"person_type must be one of {sorted(_VALID_TYPES)}")
    if visitor_subtype and visitor_subtype.upper() not in _VALID_SUBTYPES:
        raise ValidationError(f"visitor_subtype must be one of {sorted(_VALID_SUBTYPES)}")


def create_mapping(
    db: Session, *, person_id: int, facility_id: int, person_type: str,
    visitor_subtype: Optional[str] = None, mrn: Optional[str] = None,
) -> Dict[str, Any]:
    person = db.query(User).filter(User.id == person_id).first()
    if person is None:
        raise NotFoundError(f"person {person_id} not found")
    fac = db.query(Facility).filter(Facility.id == facility_id).first()
    if fac is None:
        raise NotFoundError(f"facility {facility_id} not found")
    _validate_type(person_type, visitor_subtype)
    existing = (
        db.query(PersonVisitMapping)
        .filter(PersonVisitMapping.person_id == person_id,
                PersonVisitMapping.facility_id == facility_id)
        .first()
    )
    if existing is not None:
        raise ConflictError("this person is already mapped to that facility")
    m = PersonVisitMapping(
        person_id=person_id, facility_id=facility_id,
        person_type=person_type.upper(),
        visitor_subtype=(visitor_subtype.upper() if visitor_subtype else None),
        mrn=(mrn or None), visit_count=0,
    )
    db.add(m)
    try:
        db.flush()                       # assign m.id before the version row
        record_version(db, m, reason="created")
        db.commit()
    except Exception as exc:
        db.rollback()
        raise ConflictError(f"could not create mapping (duplicate MRN?): {exc}")
    db.refresh(m)
    return _row_to_dict(m, person, fac)


def update_mapping(db: Session, mapping_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
    m = db.query(PersonVisitMapping).filter(PersonVisitMapping.id == mapping_id).first()
    if m is None:
        raise NotFoundError(f"mapping {mapping_id} not found")
    before = {a: getattr(m, a) for a in _VERSIONED_ATTRS}
    if updates.get("person_type"):
        _validate_type(updates["person_type"], updates.get("visitor_subtype"))
        m.person_type = updates["person_type"].upper()
    if "visitor_subtype" in updates:
        sub = updates["visitor_subtype"]
        if sub and sub.upper() not in _VALID_SUBTYPES:
            raise ValidationError("invalid visitor_subtype")
        m.visitor_subtype = sub.upper() if sub else None
    if "mrn" in updates:
        m.mrn = updates["mrn"] or None
    if "is_active" in updates and updates["is_active"] is not None:
        m.is_active = bool(updates["is_active"])
    # Open a new version only when a versioned attribute actually changed.
    changed = any(getattr(m, a) != before[a] for a in _VERSIONED_ATTRS)
    try:
        if changed:
            record_version(
                db, m, changed_by=updates.get("changed_by"),
                reason=updates.get("change_reason"),
            )
        db.commit()
    except Exception as exc:
        db.rollback()
        raise ConflictError(f"update failed (duplicate MRN?): {exc}")
    db.refresh(m)
    person = db.query(User).filter(User.id == m.person_id).first()
    fac = db.query(Facility).filter(Facility.id == m.facility_id).first()
    return _row_to_dict(m, person, fac)


def deactivate_mapping(db: Session, mapping_id: int) -> Dict[str, Any]:
    return update_mapping(db, mapping_id, {"is_active": False})
