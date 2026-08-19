"""
kiosk_admin.py — the admin "Kiosk" section: device registry + logs.

Mounted under /api/v1/kiosk-admin and guarded by ``kiosk.manage`` (admin
tier — these logs carry patient PII). Distinct from the device-facing
/kiosk/* routes (kiosk.operate). Retry actions for failed pre-regs /
exports live on the existing /integrations/* endpoints.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.db.postgres import get_db
from backend.app.services import kiosk_device_service as svc
from backend.app.services.kiosk_device_service import (
    ConflictError,
    NotFoundError,
    ValidationError,
)

router = APIRouter(prefix="/kiosk-admin", tags=["kiosk-admin"])


# ── Schemas ──────────────────────────────────────────────────────────────
class DeviceIn(BaseModel):
    serial: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    mode: str = "AUTO"
    source_type: str = "webcam"
    camera_id: Optional[str] = None
    dup_window: Optional[int] = None


class DevicePatch(BaseModel):
    name: Optional[str] = None
    mode: Optional[str] = None
    source_type: Optional[str] = None
    camera_id: Optional[str] = None
    dup_window: Optional[int] = None
    is_active: Optional[bool] = None


class AccountIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=200)


# ── Devices ──────────────────────────────────────────────────────────────
@router.get("/devices")
def list_devices(db: Session = Depends(get_db)):
    return {"devices": [svc.device_to_dict(d) for d in svc.list_devices(db)]}


@router.post("/devices")
def create_device(payload: DeviceIn, db: Session = Depends(get_db)):
    try:
        dev = svc.create_device(db, **payload.model_dump())
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return svc.device_to_dict(dev)


@router.patch("/devices/{device_id}")
def patch_device(device_id: int, payload: DevicePatch, db: Session = Depends(get_db)):
    try:
        dev = svc.update_device(db, device_id, payload.model_dump(exclude_unset=True))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return svc.device_to_dict(dev)


@router.delete("/devices/{device_id}")
def delete_device(device_id: int, db: Session = Depends(get_db)):
    try:
        dev = svc.deactivate_device(db, device_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return svc.device_to_dict(dev)


@router.post("/devices/{device_id}/account")
def provision_account(device_id: int, payload: AccountIn, db: Session = Depends(get_db)):
    """Create + link a minimal kiosk device login for this kiosk."""
    try:
        acct = svc.provision_account(
            db, device_id, username=payload.username, password=payload.password
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"account_id": acct.id, "username": acct.username}


# ── Logs ─────────────────────────────────────────────────────────────────
@router.get("/activity")
def activity(
    visit_type: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    return {"activity": svc.list_activity(
        db, visit_type=visit_type, limit=limit)}


@router.get("/preregs")
def preregs(
    status: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    return {"preregs": svc.list_preregs(
        db, status=status, limit=limit)}


@router.get("/exports")
def exports(status: Optional[str] = None, limit: int = 100, db: Session = Depends(get_db)):
    return {"exports": svc.list_exports(db, status=status, limit=limit)}
