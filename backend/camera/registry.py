"""
registry.py — Single owner of the per-camera worker lifecycle.

Lives in the FastAPI process. The REST CRUD router calls into this module;
camera workers are spawned, stopped, and replaced here. The PostgreSQL roster
is owned by :mod:`backend.camera.store`; the registry also keeps the shared
runtime role map synchronized after every mutation.

Concurrency model
-----------------
* All public coroutines hold ``_mutation_lock`` (an ``asyncio.Lock``)
  for the duration of any state change. This serialises CRUD calls so
  two HTTP requests can't race spawn/stop on the same camera_id.
* Blocking work (process spawn, ``Process.join``, ``cv2.VideoCapture``
  probe) is offloaded to ``asyncio.to_thread`` so the event loop stays
  responsive.
* Shared dicts (``stream_queues``, ``stream_versions``, ``viewer_counts``)
  are keyed by camera_id. We mutate them in a strict order:
  - **Add:** allocate dicts → spawn worker.
  - **Remove:** stop worker → free dicts.
  This keeps the stream endpoint's lookups race-free: while a worker is
  alive, its keys exist; once it's gone, they're gone too.

What the registry does NOT touch
--------------------------------
* AI worker pool and EventProcessor lifecycle — they stay put while camera
  workers are added or removed.
* FAISS and identity/session state — they do not depend on roster mutations.
* The frame pool (SharedMemory) — sized at boot, not per-camera.
"""

from __future__ import annotations

import asyncio
import logging
import time
from multiprocessing import Queue
from typing import Any, Optional

import cv2

from backend.camera import store as camera_store
from backend.camera.worker import CameraWorker

logger = logging.getLogger(__name__)


# Probe timeouts for the /test endpoint. RTSP handshakes can be slow
# over a flaky link, but blocking the API for >5s is rude.
_PROBE_OPEN_TIMEOUT_MS = 5000
_PROBE_READ_TIMEOUT_MS = 5000
_PROBE_WALL_CLOCK_S = 8.0

# How long to wait for a worker process to exit gracefully before
# escalating to ``terminate()`` and then ``kill()``.
_STOP_JOIN_TIMEOUT_S = 3.0
_STOP_KILL_TIMEOUT_S = 1.5


class CameraRegistry:
    """Owns the live mapping camera_id → worker process + its shared queue."""

    def __init__(self) -> None:
        self._workers: dict[str, Any] = {}  # camera_id → multiprocessing.Process

        # Shared resources (filled by ``init`` from the manager). The
        # registry mutates these dicts in place so the rest of the app
        # (stream endpoint, worker producers) sees changes immediately.
        self._frame_queue = None
        self._ws_queue = None
        self._stream_queues: dict[str, Queue] = {}
        self._stream_versions = None  # Manager().dict()
        self._viewer_counts = None    # Manager().dict()
        self._metrics_dict = None     # Manager().dict()
        self._camera_roles = None     # Manager().dict(): camera_id -> role
        self._frame_pool_handle = None

        self._mutation_lock = asyncio.Lock()
        self._initialised = False

    # ── Lifecycle wiring ────────────────────────────────────────────

    def init(
        self,
        *,
        frame_queue,
        ws_queue,
        stream_queues: dict,
        stream_versions,
        viewer_counts,
        metrics_dict,
        camera_roles=None,
        frame_pool_handle=None,
    ) -> None:
        """Bind shared resources. Called once from ``start_camera_system``."""
        self._frame_queue = frame_queue
        self._ws_queue = ws_queue
        self._stream_queues = stream_queues
        self._stream_versions = stream_versions
        self._viewer_counts = viewer_counts
        self._metrics_dict = metrics_dict
        self._camera_roles = camera_roles
        self._frame_pool_handle = frame_pool_handle
        self._initialised = True

    def is_initialised(self) -> bool:
        return self._initialised

    # ── Sync read paths (called from sync route handlers too) ───────

    def list_cameras(self) -> list[dict]:
        """Return all cameras from the store, annotated with runtime status.

        Status values:
          * ``running``  — worker process exists and is alive.
          * ``stopped``  — camera is ``active=False`` in the store.
          * ``missing``  — should be running but the worker died/never spawned.
        """
        out: list[dict] = []
        for cam in camera_store.load():
            cam_id = cam["camera_id"]
            worker = self._workers.get(cam_id)
            if not cam.get("active", True):
                status = "stopped"
            elif worker is not None and worker.is_alive():
                status = "running"
            else:
                status = "missing"
            out.append({**cam, "status": status})
        return out

    def get_camera(self, camera_id: str) -> Optional[dict]:
        for cam in self.list_cameras():
            if cam["camera_id"] == camera_id:
                return cam
        return None

    # ── Mutations ───────────────────────────────────────────────────

    async def add_camera(self, spec: dict) -> dict:
        """Persist + spawn (if active). Returns the full annotated record."""
        async with self._mutation_lock:
            full = await asyncio.to_thread(camera_store.add, spec)
            if self._camera_roles is not None:
                self._camera_roles[full["camera_id"]] = full.get("role", "inside")
            if full.get("active", True):
                await asyncio.to_thread(self._spawn_worker_sync, full)
            return self._annotate(full)

    async def update_camera(self, camera_id: str, patch: dict) -> dict:
        """Apply patch + restart the worker if a runtime-relevant field changed.

        Restart-triggering fields: ``source``, ``type``, ``active``.
        Others (``name``, ``floor``, ``role``) are metadata-only. ``role``
        is read from the manager-backed live map, so the EventProcessor sees
        it immediately without restarting the capture worker. ``floor`` is
        currently informational on the dashboard.
        Forcing a restart on cosmetic edits would needlessly blip the
        live stream.
        """
        async with self._mutation_lock:
            before = await asyncio.to_thread(camera_store.get, camera_id)
            if before is None:
                raise KeyError(camera_id)

            updated = await asyncio.to_thread(camera_store.update, camera_id, patch)
            if self._camera_roles is not None:
                self._camera_roles[camera_id] = updated.get("role", "inside")

            restart_triggers = ("source", "type", "active")
            needs_restart = any(
                k in patch and patch[k] is not None and before.get(k) != updated.get(k)
                for k in restart_triggers
            )

            if needs_restart:
                await asyncio.to_thread(self._stop_worker_sync, camera_id)
                if updated.get("active", True):
                    await asyncio.to_thread(self._spawn_worker_sync, updated)

            return self._annotate(updated)

    async def remove_camera(self, camera_id: str) -> dict:
        """Stop the worker, drop shared dict slots, then delete from store."""
        async with self._mutation_lock:
            await asyncio.to_thread(self._stop_worker_sync, camera_id)
            removed = await asyncio.to_thread(camera_store.remove, camera_id)
            if self._camera_roles is not None:
                self._camera_roles.pop(camera_id, None)
            self._drop_camera_slots(camera_id)
            return removed

    async def restart_camera(self, camera_id: str) -> dict:
        """Bounce the worker. No-op for inactive cameras."""
        async with self._mutation_lock:
            cam = await asyncio.to_thread(camera_store.get, camera_id)
            if cam is None:
                raise KeyError(camera_id)

            await asyncio.to_thread(self._stop_worker_sync, camera_id)
            if cam.get("active", True):
                await asyncio.to_thread(self._spawn_worker_sync, cam)

            return self._annotate(cam)

    # ── Boot + shutdown (called by manager during app lifespan) ─────

    def start_all_from_store(self) -> list[Any]:
        """Spawn workers for every active camera in the store.

        Synchronous because it runs during FastAPI lifespan startup,
        before the loop is serving requests. Returns the list of
        spawned worker processes for the caller to track for shutdown.
        """
        spawned = []
        for cam in camera_store.load():
            if not cam.get("active", True):
                continue
            try:
                worker = self._spawn_worker_sync(cam)
                spawned.append(worker)
            except Exception as exc:
                # One bad camera must not block the rest of the boot.
                logger.error("failed to spawn worker for %s: %s",
                             cam.get("camera_id"), exc)
        logger.info("CameraRegistry started %d workers", len(spawned))
        return spawned

    def stop_all(self) -> None:
        """Stop every running worker. Safe to call from shutdown hook."""
        for cam_id in list(self._workers.keys()):
            try:
                self._stop_worker_sync(cam_id)
            except Exception as exc:
                logger.warning("error stopping worker %s: %s", cam_id, exc)

    # ── Probe (used by /cameras/test) ───────────────────────────────

    @staticmethod
    async def probe_rtsp(source: str) -> dict:
        """Open ``source`` in a short-lived VideoCapture, grab one frame.

        Returns a dict matching :class:`CameraTestResult`. Always
        returns — never raises — so the route handler can pass the
        result through directly.
        """
        return await asyncio.to_thread(_probe_rtsp_sync, source)

    # ── Internal sync helpers (called via to_thread) ────────────────

    def _spawn_worker_sync(self, cam: dict):
        """Allocate per-camera shared resources, then start the process.

        Idempotent on the worker dict: if a stale entry exists for this
        camera_id (e.g. respawn after crash), it's replaced.
        """
        if not self._initialised:
            raise RuntimeError("CameraRegistry.init() was not called")

        cam_id = cam["camera_id"]

        # Don't double-spawn if a live process already exists.
        existing = self._workers.get(cam_id)
        if existing is not None and existing.is_alive():
            logger.info("worker for %s already alive; skipping spawn", cam_id)
            return existing

        # Allocate (or reuse) the per-camera stream queue. Reusing is
        # safe because the previous worker is gone and the stream
        # endpoint only reads — a stale frame at the head will be
        # superseded by the next put.
        if cam_id not in self._stream_queues:
            self._stream_queues[cam_id] = Queue(maxsize=1)

        if self._stream_versions is not None:
            # Bump version so any in-flight MJPEG generator notices
            # the swap and resets its idle timer.
            self._stream_versions[cam_id] = self._stream_versions.get(cam_id, 0) + 1

        if self._viewer_counts is not None and cam_id not in self._viewer_counts:
            self._viewer_counts[cam_id] = 0

        worker = CameraWorker(
            cam,
            self._frame_queue,
            self._stream_queues[cam_id],
            self._ws_queue,
            self._stream_versions,
            self._metrics_dict,
            frame_pool_handle=self._frame_pool_handle,
            viewer_counts=self._viewer_counts,
        )
        worker.start()
        self._workers[cam_id] = worker
        logger.info("worker spawned id=%s pid=%s", cam_id, worker.pid)

        # Notify the dashboard that this camera_id has a fresh stream.
        # The new worker process can't emit CAMERA_RECONNECTED on its own
        # — from its perspective this is a first connection, not a
        # reconnect — so the registry, which knows the spawn is a swap,
        # publishes the event itself. The frontend keys its `<img>`
        # remount on this version bump; without it, the old MJPEG that
        # died at IDLE_TIMEOUT stays "Camera Offline" until manual
        # refresh. Best-effort: a full ws_queue must not block a spawn.
        if self._ws_queue is not None and self._stream_versions is not None:
            try:
                self._ws_queue.put_nowait({
                    "type": "CAMERA_RECONNECTED",
                    "camera_id": cam_id,
                    "version": self._stream_versions.get(cam_id, 0),
                })
            except Exception as exc:
                logger.debug("ws notify after spawn failed cam=%s: %s",
                             cam_id, exc)

        return worker

    def _stop_worker_sync(self, camera_id: str) -> None:
        """Stop the worker (terminate → join → kill) and free shared slots.

        Also cancels any in-flight MJPEG response so the client gets a
        clean disconnect instead of a hung stream.
        """
        # Cancel any active MJPEG generator BEFORE we yank the queue —
        # otherwise the generator may try to read from a closed Queue
        # during shutdown and raise.
        try:
            from backend.app.api.routes import stream as stream_route
            stream_route.cancel_active_stream(camera_id)
        except Exception as exc:
            logger.debug("cancel_active_stream(%s) failed: %s", camera_id, exc)

        worker = self._workers.pop(camera_id, None)
        if worker is not None:
            try:
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=_STOP_JOIN_TIMEOUT_S)
                if worker.is_alive():
                    worker.kill()
                    worker.join(timeout=_STOP_KILL_TIMEOUT_S)
            except Exception as exc:
                logger.warning("worker stop error id=%s: %s", camera_id, exc)
            finally:
                # Process.close() releases OS handles; ignore if already closed.
                try:
                    worker.close()
                except Exception:
                    pass
            logger.info("worker stopped id=%s", camera_id)

        # Drain the stream queue so a future spawn doesn't serve a stale
        # frame as its first byte. Drop the queue from the dict only on
        # `remove_camera` (signalled by absence from store), not on
        # restart — keeping it preserves the ``get_stream_queue`` contract.
        q = self._stream_queues.get(camera_id)
        if q is not None:
            try:
                while not q.empty():
                    q.get_nowait()
            except Exception:
                pass

    def _drop_camera_slots(self, camera_id: str) -> None:
        """Remove shared runtime state after a camera is deleted."""
        self._stream_queues.pop(camera_id, None)
        for shared in (self._viewer_counts, self._stream_versions, self._metrics_dict):
            if shared is not None:
                try:
                    del shared[camera_id]
                except KeyError:
                    pass

    def _annotate(self, cam: dict) -> dict:
        """Add the runtime ``status`` field to a store record."""
        worker = self._workers.get(cam["camera_id"])
        if not cam.get("active", True):
            status = "stopped"
        elif worker is not None and worker.is_alive():
            status = "running"
        else:
            status = "missing"
        return {**cam, "status": status}


# ── Module-level singleton ──────────────────────────────────────────────

_registry: Optional[CameraRegistry] = None


def get_registry() -> CameraRegistry:
    """Return the process-wide registry, creating it on first use."""
    global _registry
    if _registry is None:
        _registry = CameraRegistry()
    return _registry


# ── Probe implementation (sync, runs on a worker thread) ────────────────

def _probe_rtsp_sync(source: str) -> dict:
    t0 = time.perf_counter()
    cap = None
    try:
        params = [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, _PROBE_OPEN_TIMEOUT_MS,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, _PROBE_READ_TIMEOUT_MS,
        ]
        try:
            cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG, params)
        except TypeError:
            cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, _PROBE_OPEN_TIMEOUT_MS)

        if cap is None or not cap.isOpened():
            return {
                "ok": False,
                "elapsed_ms": int((time.perf_counter() - t0) * 1000),
                "error": "could not open RTSP source (timeout or auth/url failure)",
            }

        # Some RTSP cameras need a few empty grabs before the first decoded
        # frame appears. Bound the total wait to PROBE_WALL_CLOCK_S so
        # the caller never waits forever.
        deadline = t0 + _PROBE_WALL_CLOCK_S
        frame = None
        while time.perf_counter() < deadline:
            ok, frame = cap.read()
            if ok and frame is not None:
                break

        elapsed = int((time.perf_counter() - t0) * 1000)
        if frame is None:
            return {
                "ok": False,
                "elapsed_ms": elapsed,
                "error": "opened the stream but couldn't decode a frame in time",
            }

        h, w = frame.shape[:2]
        return {"ok": True, "width": int(w), "height": int(h), "elapsed_ms": elapsed}

    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
