"""
cameras.py — Admin CRUD for the camera roster.

Routes
------
* ``GET    /cameras``               list all cameras (with runtime status)
* ``GET    /cameras/{id}``          single camera
* ``POST   /cameras``                create + spawn worker
* ``PATCH  /cameras/{id}``          partial update; restarts worker only
                                    when source/type/active actually change
* ``DELETE /cameras/{id}``          stop worker + remove from store
* ``POST   /cameras/{id}/restart``  bounce worker (source unchanged)
* ``POST   /cameras/test``          probe an RTSP URL without persisting

All mutations go through the :class:`CameraRegistry` singleton, which
serialises them under an ``asyncio.Lock`` so two concurrent admin
requests can't race on the same camera_id.

Notes
-----
* USB cameras are not creatable from this API (Pydantic-level rejection).
  Legacy USB rows imported into PostgreSQL keep running and can be
  updated/deleted from the UI.
* No authentication is wired in yet — the entire admin surface assumes
  the FastAPI service sits behind whatever access control the deployment
  enforces (reverse proxy, VPN, etc). When you add auth, gate this
  router with a dependency rather than a per-route check.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.app.core.deps import get_optional_account
from backend.app.db.attendance_models import AttendancePolicy
from backend.app.db.postgres import get_db
from backend.app.schemas.cameras import (
    CameraCreate,
    CameraRead,
    CameraTestRequest,
    CameraTestResult,
    CameraUpdate,
)
from backend.app.services import audit_service
from backend.camera.registry import get_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cameras", tags=["cameras"])


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _ensure_ready():
    """Reject mutations before the camera system has booted.

    The registry is wired during FastAPI lifespan startup. If a request
    arrives before that completes (or if ``START_CAMERA_SYSTEM`` is
    unset), every CRUD path would no-op silently — surface a clear 503
    instead so the caller sees what's wrong.
    """
    reg = get_registry()
    if not reg.is_initialised():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Camera system is not running. Start the backend with "
                "START_CAMERA_SYSTEM=1 to use the admin API."
            ),
        )
    return reg


@router.get("", response_model=list[CameraRead])
def list_cameras():
    """All cameras with runtime status. Safe to poll from the dashboard."""
    return get_registry().list_cameras()


@router.get("/{camera_id}", response_model=CameraRead)
def get_camera(camera_id: str):
    cam = get_registry().get_camera(camera_id)
    if cam is None:
        raise HTTPException(status_code=404, detail=f"camera '{camera_id}' not found")
    return cam


@router.post("", response_model=CameraRead, status_code=status.HTTP_201_CREATED)
async def create_camera(
    payload: CameraCreate,
    request: Request,
    account=Depends(get_optional_account),
    db: Session = Depends(get_db),
):
    reg = _ensure_ready()
    try:
        cam = await reg.add_camera(payload.model_dump())
    except ValueError as exc:
        # Duplicate names conflict with an existing resource; placement
        # validation errors are malformed requests.
        error_status = 409 if str(exc).startswith("Camera with name") else 400
        raise HTTPException(status_code=error_status, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("create_camera failed")
        raise HTTPException(status_code=500, detail=f"create failed: {exc}") from exc
    # Source is already scheme-restricted to rtsp(s):// by the schema, which
    # blocks the http/file/gopher SSRF classes; record the change either way.
    audit_service.record(
        db, action="camera.create", actor=account, ip=_client_ip(request),
        target_type="camera", target_id=getattr(cam, "camera_id", None)
        or (cam.get("camera_id") if isinstance(cam, dict) else None),
        detail={"name": payload.name, "source": payload.source},
    )
    return cam


@router.patch("/{camera_id}", response_model=CameraRead)
async def update_camera(
    camera_id: str,
    payload: CameraUpdate,
    request: Request,
    account=Depends(get_optional_account),
    db: Session = Depends(get_db),
):
    reg = _ensure_ready()
    # exclude_none so partial PATCHes don't accidentally null fields.
    patch = payload.model_dump(exclude_none=True)
    if not patch:
        # Empty PATCH is a no-op; return current state rather than 400 so
        # idempotent retries from the UI succeed.
        cam = reg.get_camera(camera_id)
        if cam is None:
            raise HTTPException(status_code=404, detail=f"camera '{camera_id}' not found")
        return cam
    try:
        cam = await reg.update_camera(camera_id, patch)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"camera '{camera_id}' not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("update_camera failed id=%s", camera_id)
        raise HTTPException(status_code=500, detail=f"update failed: {exc}") from exc
    audit_service.record(
        db, action="camera.update", actor=account, ip=_client_ip(request),
        target_type="camera", target_id=camera_id, detail=patch,
    )
    return cam


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_camera(
    camera_id: str,
    request: Request,
    account=Depends(get_optional_account),
    db: Session = Depends(get_db),
):
    # Deletion must remain available when capture workers are intentionally
    # disabled (for example, API-only maintenance mode). ``remove_camera``
    # safely no-ops its runtime cleanup when the registry was not initialised
    # and still deletes the PostgreSQL roster row.
    reg = get_registry()
    try:
        await reg.remove_camera(camera_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"camera '{camera_id}' not found")
    except Exception as exc:
        logger.exception("delete_camera failed id=%s", camera_id)
        raise HTTPException(status_code=500, detail=f"delete failed: {exc}") from exc

    # Avoid leaving an unusable attendance policy after a roster cleanup.
    policy = db.get(AttendancePolicy, 1)
    if policy is not None:
        selected = list(policy.attendance_camera_ids or [])
        serials = dict(policy.camera_serial_numbers or {})
        if camera_id in selected or camera_id in serials:
            policy.attendance_camera_ids = [
                value for value in selected if value != camera_id
            ]
            serials.pop(camera_id, None)
            policy.camera_serial_numbers = serials
            policy.version = (policy.version or 0) + 1
            db.commit()
    audit_service.record(
        db, action="camera.delete", actor=account, ip=_client_ip(request),
        target_type="camera", target_id=camera_id,
    )
    return None


@router.post("/{camera_id}/restart", response_model=CameraRead)
async def restart_camera(camera_id: str):
    reg = _ensure_ready()
    try:
        return await reg.restart_camera(camera_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"camera '{camera_id}' not found")
    except Exception as exc:
        logger.exception("restart_camera failed id=%s", camera_id)
        raise HTTPException(status_code=500, detail=f"restart failed: {exc}") from exc


@router.post("/test", response_model=CameraTestResult)
async def test_camera_source(payload: CameraTestRequest):
    """Probe an RTSP URL without persisting it.

    Always returns 200 with a structured ``CameraTestResult`` — even on
    failure — so the frontend can render either ``ok: true`` (with
    width/height) or ``ok: false`` (with the underlying error string)
    in a single code path.
    """
    return await get_registry().probe_rtsp(payload.source)
