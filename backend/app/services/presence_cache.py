"""
presence_cache.py — in-process cache of the global cross-camera presence
snapshot emitted by EventProcessor.

GLOBAL_PRESENCE lives in the EventProcessor child process, so FastAPI
routes can't read it directly. The processor pushes a PRESENCE event
through the WS queue; the event bridge unpacks it here; routes read from
this cache.
"""

import asyncio
import time


_snapshot: dict[str, dict] = {}
_lock = asyncio.Lock()

STALE_AFTER = 10.0  # seconds — drop entries older than this on read


async def set_presence(snapshot: dict):
    """Replace the cached snapshot. `snapshot` is {user_id: {last_seen, cameras}}."""
    async with _lock:
        _snapshot.clear()
        _snapshot.update(snapshot)


def get_presence() -> dict:
    """Return a copy of the fresh presence snapshot, stale entries dropped."""
    now = time.time()
    return {
        uid: rec
        for uid, rec in _snapshot.items()
        if now - rec.get("last_seen", 0) <= STALE_AFTER
    }
