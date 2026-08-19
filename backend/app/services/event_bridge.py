"""
event_bridge.py — Async bridge from the camera multiprocessing Queue to
active WebSocket clients.
"""

import asyncio
import queue as _queue

from backend.app.services.ws_manager import manager
from backend.app.services.detection_cache import set_detections
from backend.app.services.presence_cache import set_presence


_ws_queue = None


def set_ws_queue(q):
    global _ws_queue
    _ws_queue = q


def _poll(q, timeout=0.2):
    """Blocking get with short timeout. Returns None on empty — lets the
    event loop resume and honor cancellation between polls."""
    try:
        return q.get(timeout=timeout)
    except _queue.Empty:
        return None


async def start_event_listener():
    loop = asyncio.get_event_loop()

    try:
        while True:
            if _ws_queue is None:
                await asyncio.sleep(1)
                continue

            event = await loop.run_in_executor(None, _poll, _ws_queue, 0.2)
            if event:
                if event.get("type") == "DETECTION":
                    await set_detections(
                        event.get("camera_id"), event.get("faces", []),
                    )
                if event.get("type") == "PRESENCE":
                    await set_presence(event.get("snapshot", {}))
                    # Internal event — don't broadcast to WS clients.
                    continue
                await manager.broadcast(event)
    except asyncio.CancelledError:
        # Clean exit on shutdown.
        pass
