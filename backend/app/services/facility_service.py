"""
facility_service.py — CRUD for facilities (FACILITY_MASTER).

Used by the admin UI (Settings → Facilities). Soft-deletes via
``is_active = False`` so mappings, tracking logs and pre-registration
rows keep their FK valid. The client-HIS identifiers stored here
(``client_facility_guid``, ``client_company_id``, ``integration_config``)
are read by the outbound integration services when building payloads.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.app.db.facility_models import Facility


class FacilityError(Exception):
    pass


class NotFoundError(FacilityError):
    pass


class ConflictError(FacilityError):
    pass


def list_facilities(db: Session, *, include_inactive: bool = False) -> List[Facility]:
    q = db.query(Facility)
    if not include_inactive:
        q = q.filter(Facility.is_active.is_(True))
    return q.order_by(Facility.display_name.asc()).all()


def get_facility(db: Session, facility_id: int) -> Facility:
    fac = db.query(Facility).filter(Facility.id == facility_id).first()
    if fac is None:
        raise NotFoundError(f"facility {facility_id} not found")
    return fac


def create_facility(
    db: Session,
    *,
    display_name: str,
    code: str,
    facility_guid: str,
    regn_number: int,
    contact_number: str,
    primary_contact_person: Optional[str] = None,
    address: Optional[str] = None,
    street: Optional[str] = None,
    city: Optional[str] = None,
    state: Optional[str] = None,
    pin_code: Optional[str] = None,
    client_company_id: Optional[int] = None,
    integration_config: Optional[Dict[str, Any]] = None,
) -> Facility:
    code = code.strip().upper()
    if db.query(Facility).filter(Facility.code == code).first():
        raise ConflictError(f"facility code {code!r} already exists")
    fac = Facility(
        display_name=display_name.strip(),
        code=code,
        facility_guid=facility_guid,
        regn_number=regn_number,
        contact_number=contact_number,
        primary_contact_person=primary_contact_person,
        address=address,
        street=street,
        city=city,
        state=state,
        pin_code=pin_code,
        client_company_id=client_company_id,
        integration_config=integration_config,
    )
    db.add(fac)
    db.commit()
    db.refresh(fac)
    return fac


def update_facility(db: Session, facility_id: int, updates: Dict[str, Any]) -> Facility:
    fac = db.query(Facility).filter(Facility.id == facility_id).first()
    if fac is None:
        raise NotFoundError(f"facility {facility_id} not found")
    EDITABLE = {
        "code", "display_name", "facility_guid", "regn_number",
        "contact_number", "primary_contact_person", "address", "street",
        "city", "state", "pin_code",
        "client_company_id", "integration_config", "is_active",
    }
    for k, v in updates.items():
        if k not in EDITABLE or v is None:
            continue
        if k == "code":
            v = v.strip().upper()
            clash = (
                db.query(Facility)
                .filter(Facility.code == v, Facility.id != facility_id)
                .first()
            )
            if clash:
                raise ConflictError(f"facility code {v!r} already exists")
        setattr(fac, k, v)
    db.commit()
    db.refresh(fac)
    return fac


def deactivate_facility(db: Session, facility_id: int) -> Facility:
    return update_facility(db, facility_id, {"is_active": False})


def facility_to_dict(f: Facility) -> Dict[str, Any]:
    return {
        "id": f.id,
        "code": f.code,
        "display_name": f.display_name,
        "facility_guid": f.facility_guid,
        "regn_number": f.regn_number,
        "contact_number": f.contact_number,
        "primary_contact_person": f.primary_contact_person,
        "address": f.address,
        "street": f.street,
        "city": f.city,
        "state": f.state,
        "pin_code": f.pin_code,
        "client_company_id": f.client_company_id,
        "integration_config": f.integration_config,
        "is_active": f.is_active,
    }
