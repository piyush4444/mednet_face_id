"""
detection_cache.py — in-process cache of the most recent face detections
per camera, with a staleness timestamp so the overlay expires when a
face leaves the frame instead of persisting forever.

Populated by the WS event bridge; read by the MJPEG stream route.
"""

import asyncio
import time


_latest: dict[str, dict] = {}
_lock = asyncio.Lock()

STALE_AFTER = 0.7  # seconds


async def set_detections(camera_id: str, faces: list):
    # Always overwrite — never merge. The tracker already handles
    # persistence across frames; this cache just mirrors the latest
    # AI result.
    async with _lock:
        _latest[camera_id] = {"faces": faces, "ts": time.time()}


def get_detections(camera_id: str) -> list:
    """Return fresh faces for this camera, or [] if no recent data."""
    data = _latest.get(camera_id)
    if not data:
        return []
    if time.time() - data["ts"] > STALE_AFTER:
        return []
    return data["faces"]
