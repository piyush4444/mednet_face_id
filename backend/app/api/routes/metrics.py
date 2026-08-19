"""
metrics.py — Operational metrics endpoint.

Exposes a snapshot of the shared cross-process metrics dict maintained
by the camera pipeline. Two formats:

    GET /api/v1/metrics           → JSON (default; easy from a dashboard)
    GET /api/v1/metrics?format=prom → Prometheus text format

This is intentionally minimal. A real Prometheus client integration
(prometheus-client exposition) can come later — today we just need
visibility into FPS, queue depth, inference latency, drop rate.
"""

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from backend.camera.manager import get_metrics_dict


router = APIRouter(tags=["metrics"])


def _snapshot() -> dict:
    d = get_metrics_dict()
    if d is None:
        return {}
    # Manager-dict proxies are slow to iterate — copy once.
    try:
        return dict(d)
    except Exception:
        return {}


def _to_prom(snap: dict) -> str:
    """
    Render the flat metric dict as Prometheus text. Key names already look
    like `camera.cam_1.frame_queue_drops_per_sec` — we convert dots to
    underscores and drop anything non-numeric.
    """
    lines: list[str] = []
    for key, value in sorted(snap.items()):
        if not isinstance(value, (int, float)):
            continue
        safe = key.replace(".", "_").replace("-", "_")
        lines.append(f"{safe} {value}")
    return "\n".join(lines) + "\n"


@router.get("/metrics")
def metrics(format: str = Query("json", pattern="^(json|prom)$")):
    snap = _snapshot()
    if format == "prom":
        return PlainTextResponse(_to_prom(snap), media_type="text/plain; version=0.0.4")
    return snap
