"""
stream.py — MJPEG live stream per camera with bbox + label overlays.

    GET /api/v1/stream/{camera_id}             multipart/x-mixed-replace
    GET /api/v1/stream/{camera_id}/snapshot    image/jpeg (single frame, no overlay)

Architecture — fan-out broadcaster
----------------------------------
The camera worker pushes raw BGR frames into a per-camera
``multiprocessing.Queue`` with ``maxsize=1`` (oldest dropped on overflow).
Earlier versions of this module ran a *single-consumer guard*: at most
one MJPEG generator per camera at a time, with new connections
cancelling the previous one. That worked for one operator at one
terminal but broke as soon as two dashboards watched the same camera
— the loser saw a frozen feed.

We now run one ``StreamBroadcaster`` task per camera. It owns the
exclusive read of the source queue, draws overlays once, JPEG-encodes
once, then fans the same multipart bytes out to N per-viewer
``asyncio.Queue`` subscribers. Slow viewers drop the oldest pending
frame ("latest wins") rather than blocking the broadcaster or
their siblings.

Lifecycle:
    * First subscribe() spins up the broadcaster task and calls
      ``viewer_inc`` so the camera worker starts pushing frames.
    * Last unsubscribe() cancels the task and calls ``viewer_dec``,
      letting the worker skip the ~1.5 MB per-capture pickle.
    * ``cancel_active_stream(camera_id)`` — called by the camera
      registry when a worker is being stopped/restarted — terminates
      the broadcaster cleanly; each subscriber's generator unblocks
      via a zero-length sentinel chunk and returns.

The snapshot endpoint reads the broadcaster's cached raw frame and
encodes a *clean* (overlay-free) JPEG on demand, so the Front Desk
recognise pipeline never receives a picture with rectangles drawn over
the face.
"""

import asyncio
import logging
import os
import queue as _queue
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import cv2
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse

from backend.camera.manager import (
    get_stream_queue,
    get_stream_version,
    viewer_inc,
    viewer_dec,
)
from backend.app.services.detection_cache import get_detections


logger = logging.getLogger(__name__)

router = APIRouter(tags=["stream"])

BOUNDARY = "frame"
POLL_TIMEOUT = 0.2
IDLE_TIMEOUT = 15.0
JPEG_QUALITY = 70
TARGET_FPS = 8
MIN_FRAME_INTERVAL = 1.0 / TARGET_FPS

# Per-subscriber asyncio.Queue capacity. Two slots lets a brief consumer
# stall absorb one frame of jitter; beyond that we drop-oldest so a slow
# client cannot accumulate stale frames or back-pressure the broadcaster.
SUBSCRIBER_QUEUE_SIZE = 2

# Zero-length chunk used to wake a blocked subscriber when the
# broadcaster shuts down. Subscribers treat an empty bytes object as
# end-of-stream.
_EOS_SENTINEL: bytes = b""

# Dedicated pool for overlay + JPEG encoding. Sized so a handful of
# concurrent MJPEG viewers never saturate the box: OpenCV releases the
# GIL inside imencode, so threads actually parallelize. We cap at 8 so
# a runaway client count can't starve the rest of the process.
_ENCODE_WORKERS = min(8, (os.cpu_count() or 4))
_encode_executor = ThreadPoolExecutor(
    max_workers=_ENCODE_WORKERS,
    thread_name_prefix="mjpeg-encode",
)


def _poll(q, timeout=POLL_TIMEOUT):
    try:
        return q.get(timeout=timeout)
    except _queue.Empty:
        return None


def _draw_overlays(frame, faces):
    for face in faces:
        bbox = face.get("bbox")
        if not bbox or len(bbox) != 4:
            continue

        x1, y1, x2, y2 = (int(v) for v in bbox)
        identity = face.get("identity") or "Unknown"
        conf = float(face.get("identity_confidence", 0.0) or 0.0)

        is_known = identity and identity != "Unknown"
        color = (0, 255, 0) if is_known else (0, 0, 255)

        label = (
            f"{identity} ({int(conf * 100)}%)"
            if is_known else "Unknown"
        )

        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            frame, label, (x1, max(y1 - 10, 15)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2,
        )

    return frame


def _encode_frame(frame, faces, quality: int) -> Optional[bytes]:
    """Draw overlays (if any) + JPEG encode. Returns bytes or None.

    Pure-sync; runs inside the dedicated encode thread pool so the
    asyncio event loop is never blocked on CPU work. Errors are
    swallowed at WARNING — a single corrupt frame must not kill the
    broadcaster.
    """
    try:
        if faces:
            _draw_overlays(frame, faces)
        ok, buf = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality],
        )
        if not ok:
            return None
        return buf.tobytes()
    except Exception as exc:  # noqa: BLE001 — any cv2 glitch must not crash the stream
        logger.warning("mjpeg encode failed: %s", exc)
        return None


def shutdown_encode_pool(wait: bool = False) -> None:
    """Release encode threads. Called from the FastAPI lifespan shutdown
    so uvicorn doesn't hang waiting on idle threads."""
    try:
        _encode_executor.shutdown(wait=wait, cancel_futures=True)
    except TypeError:
        # Python < 3.9 has no cancel_futures kwarg.
        _encode_executor.shutdown(wait=wait)


def _build_multipart(jpeg: bytes) -> bytes:
    return (
        b"--" + BOUNDARY.encode() + b"\r\n"
        b"Content-Type: image/jpeg\r\n"
        b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
        + jpeg + b"\r\n"
    )


# ── Per-camera broadcaster ──────────────────────────────────────────────


class StreamBroadcaster:
    """Single reader → N asyncio-queue subscribers, encode-once fan-out.

    Thread-safety: all subscribe/unsubscribe/cancel calls run on the
    FastAPI event loop. The only cross-thread caller is the camera
    registry (sync code) invoking :func:`cancel_active_stream`, which
    uses ``task.cancel`` — itself thread-safe.
    """

    __slots__ = (
        "camera_id",
        "source_queue",
        "_subscribers",
        "_task",
        "_latest_raw",
        "_closed",
    )

    def __init__(self, camera_id: str, source_queue) -> None:
        self.camera_id = camera_id
        self.source_queue = source_queue
        self._subscribers: set[asyncio.Queue] = set()
        self._task: Optional[asyncio.Task] = None
        self._latest_raw = None
        self._closed = False

    # ── Subscriber management ──

    def subscribe(self) -> asyncio.Queue:
        """Register a viewer. Returns an asyncio.Queue that the caller
        awaits for multipart chunks. The first subscribe() also boots
        the reader task and bumps the producer-side viewer counter.
        """
        if self._closed:
            raise RuntimeError(
                f"broadcaster for {self.camera_id} is closed"
            )

        q: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        first_viewer = not self._subscribers
        self._subscribers.add(q)

        if first_viewer:
            # Tell the camera worker someone is watching, then start
            # the reader. Order matters — the worker must see the
            # incremented counter before our first poll returns, so
            # we don't burn the IDLE_TIMEOUT before frames arrive.
            viewer_inc(self.camera_id)
            self._task = asyncio.create_task(
                self._run(), name=f"mjpeg-bcast-{self.camera_id}"
            )
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Deregister a viewer. The last unsubscribe winds the
        broadcaster down (cancel task + viewer_dec) so the camera
        worker can stop pushing frames."""
        was_subscribed = q in self._subscribers
        self._subscribers.discard(q)
        if not self._subscribers and not self._closed:
            # Last viewer left — graceful shutdown.
            self._shutdown(call_viewer_dec=was_subscribed)

    def cancel(self) -> bool:
        """Force shutdown. Used by the camera registry when stopping
        or restarting a worker. Idempotent.

        Wakes every subscriber with the EOS sentinel so their
        generators exit cleanly instead of waiting on a dead queue.
        Returns True if a live broadcaster was cancelled.
        """
        if self._closed:
            return False
        had_viewers = bool(self._subscribers)
        # Wake subscribers first (sentinel) so they don't race the
        # task cancel and finally-block sentinel below.
        for sub_q in list(self._subscribers):
            self._safe_put(sub_q, _EOS_SENTINEL)
        self._shutdown(call_viewer_dec=had_viewers)
        return True

    def _shutdown(self, *, call_viewer_dec: bool) -> None:
        if self._closed:
            return
        self._closed = True
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
        if call_viewer_dec:
            try:
                viewer_dec(self.camera_id)
            except Exception:  # noqa: BLE001 — never let teardown crash
                pass
        # Remove self from the global registry if we still own the slot.
        if _broadcasters.get(self.camera_id) is self:
            _broadcasters.pop(self.camera_id, None)

    # ── Frame access for snapshot ──

    async def wait_for_raw_frame(self, timeout: float):
        """Block up to ``timeout`` seconds for the next raw BGR frame.

        Used by the snapshot endpoint. If the broadcaster already has
        a cached frame, returns immediately. Returns ``None`` on
        timeout.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        # Fast path — cached frame already available.
        if self._latest_raw is not None:
            return self._latest_raw
        while loop.time() < deadline:
            if self._latest_raw is not None:
                return self._latest_raw
            if self._closed:
                return None
            await asyncio.sleep(0.05)
        return self._latest_raw

    # ── Internals ──

    @staticmethod
    def _safe_put(q: asyncio.Queue, item) -> None:
        """Drop-oldest put. Latest frame wins for slow subscribers;
        the broadcaster is never blocked by a stuck client."""
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            # Lost a race with another put — accept the drop rather
            # than awaiting (which would block the fan-out loop).
            pass

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        last_emit = 0.0
        idle_start: Optional[float] = None
        stream_version = get_stream_version(self.camera_id)

        try:
            while True:
                # Exit if everyone left while we were blocked on poll.
                if not self._subscribers:
                    break

                # Detect worker restart. The registry preserves the
                # ``stream_queue`` object identity on restart and only
                # drains it — but bumps the stream_version. Reset the
                # idle clock so we don't bail during the brief gap.
                current_version = get_stream_version(self.camera_id)
                if current_version != stream_version:
                    logger.info(
                        "stream version update cam=%s %d -> %d",
                        self.camera_id, stream_version, current_version,
                    )
                    stream_version = current_version
                    idle_start = None
                    await asyncio.sleep(0.5)
                    continue

                frame = await loop.run_in_executor(
                    None, _poll, self.source_queue, POLL_TIMEOUT,
                )

                if frame is None:
                    idle_start = idle_start or loop.time()
                    if loop.time() - idle_start > IDLE_TIMEOUT:
                        logger.info(
                            "broadcaster idle cam=%s for %.0fs — exiting",
                            self.camera_id, IDLE_TIMEOUT,
                        )
                        break
                    continue

                idle_start = None

                # Cache the latest raw frame for the snapshot endpoint
                # regardless of whether we emit this frame to viewers.
                self._latest_raw = frame

                # Per-camera FPS cap. We still updated _latest_raw above,
                # so snapshots stay responsive even on a throttled stream.
                now = loop.time()
                if now - last_emit < MIN_FRAME_INTERVAL:
                    continue
                last_emit = now

                faces = get_detections(self.camera_id)

                t0 = time.perf_counter()
                jpeg = await loop.run_in_executor(
                    _encode_executor,
                    _encode_frame, frame, faces, JPEG_QUALITY,
                )
                encode_ms = (time.perf_counter() - t0) * 1000.0

                if jpeg is None:
                    continue
                if encode_ms > 50.0:
                    logger.debug(
                        "slow mjpeg encode cam=%s %.1f ms",
                        self.camera_id, encode_ms,
                    )

                chunk = _build_multipart(jpeg)

                # Fan-out — drop-oldest on full subscriber queues.
                for sub_q in list(self._subscribers):
                    self._safe_put(sub_q, chunk)
        except asyncio.CancelledError:
            # Normal teardown path.
            pass
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "broadcaster crashed cam=%s: %s", self.camera_id, exc
            )
        finally:
            # Wake any remaining subscribers (e.g. after IDLE_TIMEOUT
            # break) so their generators exit instead of hanging on
            # an empty queue forever.
            for sub_q in list(self._subscribers):
                self._safe_put(sub_q, _EOS_SENTINEL)


_broadcasters: dict[str, StreamBroadcaster] = {}


def _get_or_create_broadcaster(camera_id: str) -> Optional[StreamBroadcaster]:
    """Return the active broadcaster for ``camera_id``, creating one
    if missing or if the underlying ``stream_queue`` was replaced
    (e.g. camera removed-and-re-added with the same id).

    Returns ``None`` if the camera has no registered stream_queue —
    the route handler should respond 404 in that case.
    """
    src = get_stream_queue(camera_id)
    if src is None:
        return None

    existing = _broadcasters.get(camera_id)
    if (
        existing is not None
        and not existing._closed
        and existing.source_queue is src
    ):
        return existing

    bcast = StreamBroadcaster(camera_id, src)
    _broadcasters[camera_id] = bcast
    return bcast


def cancel_active_stream(camera_id: str) -> bool:
    """Force any active MJPEG broadcaster for ``camera_id`` to stop.

    Called by :mod:`backend.camera.registry` when a worker is being
    stopped or restarted — without this the broadcaster would keep
    polling a queue whose producer was just terminated, holding the
    consumer slot and forcing the next request to wait for the idle
    timeout.

    Safe to call from sync code: ``Task.cancel`` is thread-safe, and
    the dict mutation here is the only loop-side state touched. The
    broadcaster's own ``_run`` finally-block wakes subscribers with a
    sentinel so their MJPEG generators exit cleanly.

    Returns ``True`` if a live broadcaster was cancelled, ``False`` if
    none was running.
    """
    bcast = _broadcasters.pop(camera_id, None)
    if bcast is None:
        return False
    try:
        return bcast.cancel()
    except Exception as exc:  # noqa: BLE001 — defensive; never let teardown crash
        logger.warning(
            "cancel_active_stream(%s) error: %s", camera_id, exc
        )
        return False


# ── Routes ──────────────────────────────────────────────────────────────


@router.get("/stream/{camera_id}/snapshot")
async def snapshot(camera_id: str):
    """Single-frame JPEG capture from a live system camera.

    Used by the Front Desk page so the operator can scan a patient
    using a configured RTSP/IP camera instead of the operator's USB
    webcam. We *briefly* subscribe to the broadcaster so the camera
    worker starts pushing frames (via viewer_inc), then read the
    broadcaster's cached raw BGR frame and JPEG-encode it without
    overlays — the bare frame is what the recognise pipeline needs.

    Returns 503 if no frame arrives within ~1 s (camera idle/paused),
    404 if the camera is unknown.
    """
    bcast = _get_or_create_broadcaster(camera_id)
    if bcast is None:
        raise HTTPException(
            status_code=404,
            detail=f"Camera '{camera_id}' is not active or unknown",
        )

    sub_q = bcast.subscribe()
    try:
        frame = await bcast.wait_for_raw_frame(1.0)
        if frame is None:
            raise HTTPException(status_code=503, detail="no frame available")

        loop = asyncio.get_running_loop()
        jpeg = await loop.run_in_executor(
            _encode_executor, _encode_frame, frame, None, JPEG_QUALITY,
        )
        if jpeg is None:
            raise HTTPException(status_code=500, detail="encode failed")
        return Response(content=jpeg, media_type="image/jpeg")
    finally:
        bcast.unsubscribe(sub_q)


@router.get("/stream/{camera_id}")
async def stream_camera(camera_id: str):
    """Multipart MJPEG live stream with bbox + identity overlays.

    Any number of clients can subscribe to the same camera_id
    concurrently. Each viewer gets its own asyncio.Queue subscribing
    to a single shared encode loop; a slow viewer drops frames
    locally without affecting siblings.
    """
    bcast = _get_or_create_broadcaster(camera_id)
    if bcast is None:
        raise HTTPException(
            status_code=404,
            detail=f"Camera '{camera_id}' is not active or unknown",
        )

    sub_q = bcast.subscribe()

    async def _generator():
        try:
            while True:
                chunk = await sub_q.get()
                if not chunk:
                    # EOS sentinel — broadcaster shutting down or cancelled.
                    break
                yield chunk
        except asyncio.CancelledError:
            # Client disconnected — normal teardown path.
            pass
        finally:
            bcast.unsubscribe(sub_q)

    return StreamingResponse(
        _generator(),
        media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
    )
