"""
pipeline_service.py — Heavy-model singleton for the backend.

Why this module exists
----------------------
The face-recognition pipeline needs three expensive, long-lived resources:

    1. A face **detector**               (OpenCV Haar / YOLO / InsightFace)
    2. A face **recognizer**             (InsightFace ArcFace / ONNX)
    3. A **FAISS index** of embeddings   (persistent on disk)

Loading any of these costs hundreds of milliseconds to several seconds —
downloading model weights, allocating ONNX sessions, reading the FAISS
file, etc. A production backend must therefore load them **exactly once
per process** and then share the handles across every request.

This module owns those handles. Everything else in the backend (routes,
services, background jobs) goes through the accessor functions below so
there is a single source of truth for the loaded models.

Threading model
---------------
Uvicorn's default worker runs a single event loop; each HTTP request is
dispatched onto a shared thread pool when it needs to do blocking work
(the face pipeline is all blocking). That means multiple threads may
call ``get_detector()`` / ``get_recognizer()`` / ``get_face_db()``
concurrently.

    * Initialization is guarded by ``_init_lock`` — double-checked so the
      fast path (already loaded) is lock-free.
    * ONNX Runtime ``InferenceSession.Run`` is documented thread-safe,
      so the recognizer can be called concurrently without a lock.
    * FAISS ``IndexFlatIP.search`` is thread-safe (pure read), but
      ``add`` / ``reset`` are not. ``face_db_write_lock`` is exposed so
      callers serialize mutations (``register`` + ``save``).
    * OpenCV Haar cascades are read-only after load, so detection is
      safe to call concurrently.

Multi-worker deployments (``uvicorn --workers N``) give every worker its
own process, and therefore its own copy of the models. That is the
recommended way to scale CPU-bound inference.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

logger = logging.getLogger("backend.pipeline_service")


# ── Module-level singletons (lazy) ───────────────────────────────────────
_detector: Optional[Any] = None
_recognizer: Optional[Any] = None
_face_db: Optional[Any] = None

# Guards one-time initialization. Double-checked locking → fast path is
# a plain attribute read with no synchronization overhead.
_init_lock = threading.Lock()

# Exposed so services can serialize FAISS mutations. Reads do NOT need
# this lock; only ``face_db.register`` / ``face_db.save`` do.
face_db_write_lock = threading.Lock()


# ── Debounced async FAISS save ───────────────────────────────────────────
# FAISS save() serialises the full index to disk — cheap (~20ms for
# thousands of embeddings) but still blocks the API thread when called
# synchronously from a register endpoint. We coalesce rapid registrations
# into one save: each `schedule_save()` call resets a 1-second timer, so
# a burst of N registrations (e.g. bulk import) produces ONE save at the
# end, not N.
#
# AI workers pick up the new index via mtime watcher in pipeline_runner,
# so the 1-second debounce just delays disk durability — the in-memory
# FAISS index is already updated the moment `register()` returns.
_save_timer: Optional[threading.Timer] = None
_save_dirty: bool = False
_save_timer_lock = threading.Lock()
_SAVE_DEBOUNCE_S = 1.0


def _do_save() -> None:
    """Run a single save if the index is marked dirty. Called by the timer."""
    global _save_dirty
    with _save_timer_lock:
        if not _save_dirty:
            return
        _save_dirty = False

    try:
        with face_db_write_lock:
            get_face_db().save()
        logger.info("[FAISS] debounced save complete")
    except Exception as exc:  # noqa: BLE001
        logger.error("[FAISS] save failed: %s", exc)


def schedule_save(delay: float = _SAVE_DEBOUNCE_S) -> None:
    """
    Mark the FAISS index dirty and schedule a debounced disk save.

    Safe to call under or outside ``face_db_write_lock``. Cancels any
    pending save and arms a fresh one — a burst of registrations within
    `delay` seconds produces exactly one disk write at the end.
    """
    global _save_timer, _save_dirty
    with _save_timer_lock:
        _save_dirty = True
        if _save_timer is not None:
            _save_timer.cancel()
        t = threading.Timer(delay, _do_save)
        t.daemon = True
        _save_timer = t
        t.start()


def flush_save() -> None:
    """
    Force an immediate save, bypassing the debounce. Call on graceful
    shutdown so in-memory embeddings don't get lost in the debounce window.
    """
    global _save_timer
    with _save_timer_lock:
        if _save_timer is not None:
            _save_timer.cancel()
            _save_timer = None
    _do_save()


# ── Initialization ───────────────────────────────────────────────────────
def init_pipeline() -> None:
    """
    Eagerly load detector, recognizer, and FAISS database.

    Intended to be called once during application startup
    (``app.main.lifespan``). Safe to call multiple times — subsequent
    calls are no-ops.

    Raises:
        RuntimeError: If any model fails to load. The backend should
        refuse to start in that case; a face-recognition API without a
        working pipeline is worse than an explicit boot failure.
    """
    global _detector, _recognizer, _face_db

    with _init_lock:
        if _detector is None:
            logger.info("Loading face detector...")
            from backend.engine.pipeline.detection import _load_detector
            _detector = _load_detector()
            if _detector is None:
                raise RuntimeError("Face detector failed to load")
            logger.info("Face detector ready")

        if _recognizer is None:
            logger.info("Loading ArcFace recognizer...")
            from backend.engine.pipeline.embedding import _load_recognizer
            _recognizer = _load_recognizer()
            logger.info("ArcFace recognizer ready")

        if _face_db is None:
            logger.info("Loading FAISS face database...")
            # Re-export path — keeps ``app`` as the single import entry
            # point even though the real class lives in ``engine``.
            from backend.app.db.face_db import FaceDatabase
            db = FaceDatabase()
            db.load()   # No-op if files don't exist — starts empty.
            _face_db = db
            logger.info("FAISS database ready (%d embeddings)", db.size)


# ── Accessors ────────────────────────────────────────────────────────────
# Each accessor lazily calls ``init_pipeline`` if the models aren't
# loaded yet. In practice startup has already done that, so the common
# path is a single attribute read.
def get_detector() -> Any:
    """Return the loaded face detector (lazy-init if needed)."""
    if _detector is None:
        init_pipeline()
    return _detector


def get_recognizer() -> Any:
    """Return the loaded ArcFace recognizer (lazy-init if needed)."""
    if _recognizer is None:
        init_pipeline()
    return _recognizer


def get_face_db() -> Any:
    """Return the loaded FAISS face database (lazy-init if needed)."""
    if _face_db is None:
        init_pipeline()
    return _face_db


# ── Reconciliation ───────────────────────────────────────────────────────
def reconcile_face_db_with_postgres() -> int:
    """
    Drop FAISS identities that have no matching row in the ``users`` table.

    The FAISS index and Postgres can drift if rows are deleted directly in
    the database, if a previous run crashed mid-write, or if the index
    file outlives a fresh database. Without reconciliation those orphan
    keys cause an endless stream of "FAISS hit ... but no Postgres row"
    warnings on every recognize call.

    Runs once at startup. Returns the number of identities removed.
    """
    db = get_face_db()
    if db.is_empty:
        return 0

    from backend.app.db.postgres import SessionLocal
    from backend.app.db.models import Patient

    session = SessionLocal()
    try:
        live_ids = {str(uid) for (uid,) in session.query(Patient.id).all()}
    finally:
        session.close()

    orphans = [name for name in db.list_identities() if name not in live_ids]
    if not orphans:
        return 0

    removed_total = 0
    with face_db_write_lock:
        for key in orphans:
            removed_total += db.remove_identity(key)
        if removed_total:
            db.save()

    logger.warning(
        "Reconciled FAISS ↔ Postgres: removed %d orphan identities (%s)",
        len(orphans), ", ".join(orphans),
    )
    return len(orphans)


# ── Test hook ────────────────────────────────────────────────────────────
def _reset_for_tests() -> None:
    """
    Drop all cached singletons. Test-only helper — do not call in prod.
    """
    global _detector, _recognizer, _face_db
    with _init_lock:
        _detector = None
        _recognizer = None
        _face_db = None
