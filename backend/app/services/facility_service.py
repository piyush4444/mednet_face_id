"""Read-only lookup for the deployment's singleton facility."""

from typing import Any, Dict

from sqlalchemy.orm import Session

from backend.app.db.facility_models import Facility


class FacilityError(Exception):
    pass


class NotFoundError(FacilityError):
    pass


class ConflictError(FacilityError):
    pass


def get_single_facility(db: Session) -> Facility:
    rows = (
        db.query(Facility)
        .filter(Facility.is_active.is_(True))
        .order_by(Facility.id)
        .limit(2)
        .all()
    )
    if not rows:
        raise NotFoundError(
            "facility is not initialized; run python -m backend.scripts.seed_initial"
        )
    if len(rows) > 1:
        raise ConflictError(
            "multiple active facilities found; this deployment supports exactly one"
        )
    return rows[0]


def facility_to_dict(facility: Facility) -> Dict[str, Any]:
    return {
        "id": facility.id,
        "code": facility.code,
        "display_name": facility.display_name,
        "facility_guid": facility.facility_guid,
        "regn_number": facility.regn_number,
        "contact_number": facility.contact_number,
        "primary_contact_person": facility.primary_contact_person,
        "address": facility.address,
        "street": facility.street,
        "city": facility.city,
        "state": facility.state,
        "pin_code": facility.pin_code,
        "client_company_id": facility.client_company_id,
        "integration_config": facility.integration_config,
        "is_active": facility.is_active,
    }
