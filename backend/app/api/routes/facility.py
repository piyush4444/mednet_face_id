"""Read-only endpoint for the deployment's single facility."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.db.postgres import get_db
from backend.app.services import facility_service as svc
from backend.app.services.facility_service import ConflictError, NotFoundError

router = APIRouter(prefix="/facility", tags=["facility"])


@router.get("")
def get_facility(db: Session = Depends(get_db)):
    try:
        return svc.facility_to_dict(svc.get_single_facility(db))
    except NotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
