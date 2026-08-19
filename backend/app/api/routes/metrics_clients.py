"""metrics_clients.py — live counts of WS tabs + MJPEG viewers.

Used by internal monitoring dashboards. Reads the in-memory state owned by
the WS connection manager and the MJPEG broadcaster registry — no DB hit,
no shared-dict round-trip.
"""

from datetime import datetime, timezone

from fastapi import APIRouter

from backend.app.services.ws_manager import manager as ws_manager
from backend.app.api.routes.stream import _broadcasters


router = APIRouter(tags=["metrics"])


@router.get("/metrics/clients")
def clients():
    per_camera = {
        cam_id: len(b._subscribers)
        for cam_id, b in _broadcasters.items()
        if not getattr(b, "_closed", False)
    }
    return {
        "ws_connections": len(ws_manager.active_connections),
        "mjpeg_total_subscribers": sum(per_camera.values()),
        "mjpeg_per_camera": per_camera,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
