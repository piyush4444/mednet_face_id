"""
pipeline_runner.py — Synchronous face pipeline adapter for CameraWorker.

The engine pipeline (backend/engine/pipeline/*) is built as async,
process-based stages wired through multiprocessing.Queues. Each
CameraWorker is already its own process, so we compose the existing
stage helpers synchronously here instead of spinning up another 7
subprocesses per camera.

No pipeline files are modified — we only import from them.
"""

import logging
import os
import time

import cv2
import numpy as np

import backend.config as config
from backend.engine.pipeline.detection import (
    _load_detector,
    _detect_faces,
    _filter_faces,
    _crop_face,
)
from backend.engine.pipeline.alignment import (
    align_with_landmarks,
    align_center_crop,
)
from backend.engine.pipeline.embedding import (
    _load_recognizer,
    extract_embedding,
)
from backend.engine.pipeline.matching import _load_database
from backend.engine.pipeline.zoom_trigger import (
    ZoomCooldown,
    evaluate as evaluate_zoom_trigger,
)

# NOTE: identity smoothing (FaceTracker) used to run here, but lived in
# process-local state and fragmented when NUM_AI_WORKERS > 1. It now
# runs inside EventProcessor (single-process observer of all cameras)
# so tracker state is coherent regardless of AI worker count.

logger = logging.getLogger("pipeline_runner")

_detector = None
_recognizer = None
_database = None
_database_mtime: float = 0.0
_name_cache: dict[str, str] = {}

# Motion-skip state — per camera. Each entry: (last_thumb_gray, last_run_ts).
# thumb is a 64×64 grayscale numpy array used for cheap diffing.
_motion_state: dict[str, tuple[np.ndarray, float]] = {}
_MOTION_THUMB = (64, 64)

# Cache the last pipeline result per camera so that on motion-skip we can
# replay the previous faces instead of emitting [] (which would age out
# trackers for stationary people).
_last_results: dict[str, list] = {}

# Per-camera zoom-trigger cooldown gates (Phase 1 multi-scale detection).
# One ai_worker process serves N cameras, so we keep gate state keyed by
# camera_id rather than as a single module-level counter — otherwise an
# active camera would starve a sparse one's eligibility window.
_zoom_cooldowns: dict[str, ZoomCooldown] = {}
_zoom_frame_counter: dict[str, int] = {}
_zoom_triggers_total: dict[str, int] = {}


def _zoom_gate(camera_id: str) -> ZoomCooldown:
    """Lazy per-camera ZoomCooldown allocator."""
    gate = _zoom_cooldowns.get(camera_id)
    if gate is None:
        gate = ZoomCooldown(
            getattr(config, "ZOOM_TRIGGER_COOLDOWN_FRAMES", 5),
        )
        _zoom_cooldowns[camera_id] = gate
    return gate


def _should_skip_motion(frame, camera_id: str | None) -> bool:
    """
    Return True if the frame is close enough to the last-processed frame
    for this camera that we can skip the full pipeline.

    We force a run every MOTION_SKIP_MAX_INTERVAL_S seconds so trackers
    keep ticking and aged tracks expire on schedule.
    """
    if not getattr(config, "MOTION_SKIP_ENABLED", False):
        return False
    if camera_id is None or frame is None:
        return False

    now = time.time()
    thumb = cv2.resize(frame, _MOTION_THUMB, interpolation=cv2.INTER_AREA)
    thumb_gray = cv2.cvtColor(thumb, cv2.COLOR_BGR2GRAY)

    prev = _motion_state.get(camera_id)
    if prev is None:
        _motion_state[camera_id] = (thumb_gray, now)
        return False  # No baseline yet — run pipeline this time.

    prev_thumb, prev_ts = prev

    # Force a periodic run even in perfectly still scenes.
    if now - prev_ts >= config.MOTION_SKIP_MAX_INTERVAL_S:
        _motion_state[camera_id] = (thumb_gray, now)
        return False

    diff = float(np.abs(thumb_gray.astype(np.int16) - prev_thumb.astype(np.int16)).mean())

    if diff < config.MOTION_SKIP_THRESHOLD:
        # Scene is static — skip. Don't update the baseline so small drift
        # still accumulates relative to the last REAL processing tick.
        return True

    # Motion detected — refresh baseline and run pipeline.
    _motion_state[camera_id] = (thumb_gray, now)
    return False


def _resolve_identity(identity):
    """Map a recognizer identity (user id as string) to the user's
    display name and type. Cached in-process; misses fall back to
    (raw id, "PATIENT") so existing consumers keep their semantics."""
    if not identity or identity == "Unknown":
        return identity, "PATIENT"

    key = str(identity)
    cached = _name_cache.get(key)
    if cached is not None:
        return cached

    name = key
    user_type = "PATIENT"
    try:
        from backend.app.db.postgres import SessionLocal
        from backend.app.db.models import Patient

        db = SessionLocal()
        try:
            user = db.query(Patient).filter(Patient.id == int(key)).first()
            if user is not None:
                if user.name:
                    name = user.name
                user_type = getattr(user, "user_type", None) or "PATIENT"
        finally:
            db.close()
    except Exception as exc:
        logger.debug("Identity lookup failed for %s: %s", key, exc)

    _name_cache[key] = (name, user_type)
    return name, user_type


def _resolve_name(identity):
    """Back-compat shim: name-only lookup used by callers that don't
    yet care about user_type."""
    name, _ = _resolve_identity(identity)
    return name


def _current_index_mtime() -> float:
    """mtime of the FAISS index file, or 0.0 if the file does not exist yet."""
    try:
        return os.path.getmtime(config.FAISS_INDEX_PATH)
    except OSError:
        return 0.0


def _maybe_reload_database():
    """
    Reload FAISS + name map if the on-disk index is newer than our copy.

    AI workers run as separate processes, so they don't share the
    FastAPI main process's in-memory FaceDatabase. When registration
    writes new embeddings, we notice the mtime change and re-read.
    """
    global _database, _database_mtime, _name_cache

    current_mtime = _current_index_mtime()
    if current_mtime == 0.0 or current_mtime <= _database_mtime:
        return

    try:
        new_db = _load_database()
    except Exception as exc:
        # If the save was mid-write when we tried to read, keep the
        # old DB and try again on the next frame.
        logger.debug("Database reload failed (will retry): %s", exc)
        return

    _database = new_db
    _database_mtime = current_mtime
    _name_cache = {}  # Invalidate — new patient ids may have been added
    logger.info("[RELOAD] FAISS database reloaded (size=%d)", new_db.size)


def _lazy_init():
    global _detector, _recognizer, _database, _database_mtime
    if _detector is None:
        _detector = _load_detector()
    if _recognizer is None:
        try:
            _recognizer = _load_recognizer()
        except Exception as exc:
            logger.error("Recognizer load failed: %s", exc)
            _recognizer = False
    if _database is None:
        _database = _load_database()
        _database_mtime = _current_index_mtime()


def run_pipeline(frame, camera_id: str | None = None):
    """
    Run detection → alignment → embedding → matching synchronously
    on a single BGR frame.

    Returns:
        {"faces": [ {bbox, confidence, identity, identity_confidence}, ... ]}
    """
    _lazy_init()
    _maybe_reload_database()

    if frame is None or _detector is None:
        return {"faces": []}

    # Cheap motion gate — skip detection/embedding on static frames.
    # Replay the last real detections so stationary people don't age out.
    if _should_skip_motion(frame, camera_id):
        return {"faces": list(_last_results.get(camera_id, []))}

    h, w = frame.shape[:2]
    raw = _detect_faces(_detector, frame)
    faces = _filter_faces(raw, h, w)

    # ── Phase-1 zoom trigger ──────────────────────────────────────────────
    # Pure decision; cheap. Result stamped on the return dict so consumers
    # (event_processor, dashboards, future Phase-2 zoom executor) can act
    # without re-evaluating. Per-camera cooldown gates the log line so an
    # idle hallway doesn't spam.
    zoom_decision = evaluate_zoom_trigger(faces)
    if zoom_decision.needs_zoom and camera_id is not None:
        frame_idx = _zoom_frame_counter.get(camera_id, 0) + 1
        _zoom_frame_counter[camera_id] = frame_idx
        if _zoom_gate(camera_id).admit(frame_idx):
            _zoom_triggers_total[camera_id] = (
                _zoom_triggers_total.get(camera_id, 0) + 1
            )
            logger.info(
                "[ZOOM_TRIGGER] cam=%s faces=%d max_size=%d reason=%s "
                "small_regions=%d (total=%d)",
                camera_id, len(faces), zoom_decision.max_face_size_px,
                zoom_decision.reason, len(zoom_decision.small_face_regions),
                _zoom_triggers_total[camera_id],
            )

    target_size = config.ALIGNED_FACE_SIZE
    results = []

    for face in faces:
        bbox = face["bbox"]
        crop = _crop_face(frame, bbox)
        if crop.size == 0:
            continue

        landmarks = face.get("landmarks")
        try:
            if landmarks is not None:
                aligned = align_with_landmarks(crop, landmarks, bbox, target_size)
            else:
                aligned = align_center_crop(crop, target_size)
        except Exception as exc:
            logger.debug("Alignment failed: %s", exc)
            continue

        identity = "Unknown"
        identity_conf = 0.0
        if _recognizer and _database is not None:
            try:
                embedding = extract_embedding(_recognizer, aligned)
                if not _database.is_empty:
                    identity, identity_conf = _database.search(
                        embedding, k=config.FAISS_TOP_K,
                    )
            except Exception as exc:
                logger.debug("Embedding/match failed: %s", exc)

        ident_name, ident_type = _resolve_identity(identity)
        results.append({
            "bbox": bbox,
            "confidence": float(face["confidence"]),
            "identity": ident_name,
            "user_id": identity if identity != "Unknown" else None,
            "user_type": ident_type if identity != "Unknown" else None,
            "identity_confidence": float(identity_conf),
        })

    # Cache raw (un-smoothed) detections for motion-skip replay. Tracker
    # smoothing is applied downstream in EventProcessor.
    if camera_id is not None:
        _last_results[camera_id] = results

    return {
        "faces": results,
        # Phase-1 multi-scale detection metadata. Extra keys, no schema
        # break — existing consumers read result["faces"] only and ignore
        # the rest. Phase-2 zoom executor will consume small_face_regions.
        "needs_zoom": zoom_decision.needs_zoom,
        "zoom_reason": zoom_decision.reason,
        "zoom_max_face_px": zoom_decision.max_face_size_px,
        "small_face_regions": list(zoom_decision.small_face_regions),
    }
