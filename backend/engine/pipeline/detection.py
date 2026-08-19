"""
detection.py — Stage 2: Face Detection

Responsibilities:
    - Receive FramePackets from the capture stage
    - Run face detection on each frame (multiple faces supported)
    - Filter out small faces and low-confidence detections
    - For EACH detected face, create an independent FaceTask with:
        • bounding box [x1, y1, x2, y2]
        • cropped face image (small — NOT full frame)
        • 5-point landmarks (if detector provides them, else None)
        • detection confidence score
    - Push each FaceTask independently into the output queue
    - Send metadata to the aggregator via meta_queue so it knows how
      many FaceTasks to collect before emitting a FrameResult

Data flow:
    ┌───────────┐        ┌───────────────────────────────────────────────────────────┐
    │ Capture Q │──────▶ │ Detection Process                                         │
    │FramePacket│        │   1. Pop FramePacket                                      │
    └───────────┘        │   2. detector.detect(frame) → list of raw faces           │
                         │   3. Filter: confidence ≥ threshold, size ≥ min_size      │
                         │   4. For each face:                                       │
                         │      a. Clamp bbox to frame bounds                         │
                         │      b. Crop face_img from frame (numpy slice + copy)      │
                         │      c. Build FaceTask                                     │
                         │      d. put_nowait → output queue (drop if full)           │
                         │   5. meta_queue.put((frame_id, num_faces, frame))          │
                         └───────────────────────────────────────────────────────────┘
                                │                                    │
                                ▼                                    ▼
                         ┌─────────────┐                    ┌────────────────┐
                         │  Output Q   │                    │   Meta Q       │
                         │  FaceTask×N │                    │(fid, N, frame) │
                         └─────────────┘                    └────────────────┘

Detector abstraction:
    The actual model is hidden behind _load_detector() and _detect_faces().
    To swap in a different model (YOLOv8-face, InsightFace, MediaPipe):
        1. Replace _load_detector() to load your model
        2. Replace _detect_faces() to return the standard dict format:
           [{'bbox': [x1,y1,x2,y2], 'confidence': float, 'landmarks': ndarray|None}]
    The rest of the pipeline is model-agnostic.
"""

import logging
import time
from multiprocessing import Queue, Event
from queue import Empty, Full

import cv2
import numpy as np

from backend.engine.models.face_task import FaceTask, FramePacket
from backend.engine.pipeline.zoom_trigger import (
    ZoomCooldown,
    evaluate as evaluate_zoom_trigger,
)
import backend.config as config

logger = logging.getLogger("detection")

# ── Periodic stats interval ──
_STATS_LOG_INTERVAL = 100


# ═══════════════════════════════════════════════════════════════════════════════
# Detector — load / inference (swap this section for a different model)
# ═══════════════════════════════════════════════════════════════════════════════

def _load_detector():
    """
    Load the face detection model.

    Prefers InsightFace (RetinaFace + 5-point landmarks) for accurate
    detection at off-frontal angles. Falls back to OpenCV Haar cascade
    if InsightFace isn't installed or its model download fails — Haar
    keeps the backend functional in dev environments without network.
    """
    # ── Preferred: InsightFace ────────────────────────────────────────
    try:
        from insightface.app import FaceAnalysis

        app = FaceAnalysis(allowed_modules=["detection"])
        app.prepare(
            ctx_id=config.GPU_DEVICE_ID,
            det_size=config.DETECTION_INPUT_SIZE,
        )
        # Tag the object so _detect_faces can dispatch on type without
        # an isinstance import dance.
        setattr(app, "_is_insightface", True)

        # Log the effective ONNX providers so GPU vs CPU is visible at
        # startup. Mirrors the logging in embedding.py so both stages can
        # be verified from one log scan.
        det_model = app.models.get("detection")
        providers = (
            det_model.session.get_providers()
            if det_model is not None and hasattr(det_model, "session")
            else ["unknown"]
        )
        using_gpu = any("CUDA" in p or "Tensorrt" in p for p in providers)
        logger.info(
            "Loaded InsightFace detector (det_size=%s, ctx_id=%d, providers=%s, GPU=%s)",
            config.DETECTION_INPUT_SIZE, config.GPU_DEVICE_ID, providers, using_gpu,
        )
        return app
    except Exception as exc:
        logger.warning(
            "InsightFace unavailable (%s) — falling back to Haar cascade",
            exc,
        )

    # ── Fallback: Haar cascade ────────────────────────────────────────
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    detector = cv2.CascadeClassifier(cascade_path)

    if detector.empty():
        logger.error("Failed to load Haar cascade from: %s", cascade_path)
        return None

    logger.info("Loaded Haar cascade detector (fallback)")
    return detector


def _detect_faces(detector, frame: np.ndarray):
    """
    Run face detection on a single frame.

    Args:
        detector:  The loaded detection model (Haar cascade for now).
        frame:     BGR image (H, W, 3).

    Returns:
        List of dicts, each with keys:
            'bbox':        [x1, y1, x2, y2] (int, frame coords)
            'confidence':  float (0.0–1.0)
            'landmarks':   np.ndarray (5, 2) or None

    ── How to replace with YOLOv8 / InsightFace ──
    faces = detector.get(frame)
    return [
        {
            'bbox': face.bbox.astype(int).tolist(),
            'confidence': float(face.det_score),
            'landmarks': face.kps,   # (5, 2) ndarray or None
        }
        for face in faces
    ]
    """
    if detector is None:
        return []

    # ── InsightFace path (preferred) ──
    if getattr(detector, "_is_insightface", False):
        try:
            faces = detector.get(frame)
        except Exception as exc:
            logger.error("InsightFace inference failed: %s", exc)
            return []
        results = []
        for face in faces:
            bbox = face.bbox.astype(int).tolist()
            results.append({
                "bbox": [int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])],
                "confidence": float(face.det_score),
                "landmarks": getattr(face, "kps", None),
            })
        return results

    # ── Haar cascade detection (fallback) ──
    # Convert to grayscale (Haar only works on single-channel)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # detectMultiScale returns (x, y, w, h) tuples
    # rejectLevels + levelWeights gives us confidence scores
    rects, reject_levels, weights = detector.detectMultiScale3(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(config.MIN_FACE_SIZE, config.MIN_FACE_SIZE),
        outputRejectLevels=True,
    )

    results = []
    for i, (x, y, w, h) in enumerate(rects):
        # Haar weights aren't true probabilities — normalize roughly
        # Typical range: 0–10+. Map to 0.0–1.0 with a sigmoid-like clamp.
        raw_weight = weights[i] if i < len(weights) else 0.0
        confidence = min(raw_weight / 5.0, 1.0)

        results.append({
            "bbox": [int(x), int(y), int(x + w), int(y + h)],
            "confidence": float(confidence),
            "landmarks": None,  # Haar doesn't provide landmarks
        })

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Filtering — applied AFTER raw detection, BEFORE creating FaceTasks
# ═══════════════════════════════════════════════════════════════════════════════

def _filter_faces(faces: list, frame_h: int, frame_w: int) -> list:
    """
    Filter detected faces by confidence and minimum size.

    Args:
        faces:    List of detection dicts from _detect_faces().
        frame_h:  Frame height (for bbox clamping).
        frame_w:  Frame width (for bbox clamping).

    Returns:
        Filtered list with clamped bounding boxes.
    """
    filtered = []

    for face in faces:
        # ── Confidence filter ──
        if face["confidence"] < config.DETECTION_CONFIDENCE:
            continue

        # ── Clamp bbox to frame bounds ──
        x1, y1, x2, y2 = face["bbox"]
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(frame_w, x2)
        y2 = min(frame_h, y2)

        # ── Size filter (after clamping) ──
        w = x2 - x1
        h = y2 - y1
        if w < config.MIN_FACE_SIZE or h < config.MIN_FACE_SIZE:
            continue

        face["bbox"] = [x1, y1, x2, y2]
        filtered.append(face)

    return filtered


# ═══════════════════════════════════════════════════════════════════════════════
# Cropping — extract face region from the full frame
# ═══════════════════════════════════════════════════════════════════════════════

def _crop_face(frame: np.ndarray, bbox: list) -> np.ndarray:
    """
    Crop the face region from the frame.

    Args:
        frame:  Full BGR image.
        bbox:   [x1, y1, x2, y2] already clamped to frame bounds.

    Returns:
        Cropped face image as a NEW array (not a view).
        Using .copy() to detach from the large frame buffer
        so the full frame can be garbage-collected.
    """
    x1, y1, x2, y2 = bbox
    # Numpy slice creates a VIEW — .copy() gives an independent small array.
    # This is deliberate: we want the ~150×150 crop, not a reference
    # that keeps the entire 1080p frame alive in memory.
    return frame[y1:y2, x1:x2].copy()


# ═══════════════════════════════════════════════════════════════════════════════
# Main process loop
# ═══════════════════════════════════════════════════════════════════════════════

def detection_process(
    camera_id: str,
    input_queue: Queue,
    output_queue: Queue,
    meta_queue: Queue,
    shutdown_event: Event,
):
    """
    Main loop for the face detection stage — ONE instance per camera.

    Runs as an independent multiprocessing.Process.

    Args:
        camera_id:       Identifier for the source camera (e.g. "cam_entry").
                         Used to prefix every log message with "[camera_id]"
                         so the four cameras' interleaved output stays
                         readable.
        input_queue:     Receives FramePackets from capture.
        output_queue:    Sends individual FaceTask objects to alignment.
                         Non-blocking — drops FaceTasks if full.
        meta_queue:      Sends (frame_id, num_faces, frame) to aggregator
                         so it knows how many results to collect for this frame.
        shutdown_event:  multiprocessing.Event — set to stop this process.
    """
    from backend.engine.utils.logger import setup_logger, camera_logger
    setup_logger("detection")
    # Shadows the module-level `logger` inside this function only, so every
    # existing `logger.xxx(...)` call below automatically gets the prefix.
    logger = camera_logger("detection", camera_id)

    # ── Load detector (once — expensive init) ──
    detector = _load_detector()

    # ── Zoom-trigger gate (Phase 1 of multi-scale detection) ──
    # Per-camera throttle so an idle hallway doesn't fire every frame.
    # Disabling the trigger entirely (config.ZOOM_TRIGGER_ENABLED=False)
    # is a fast-path: evaluate() returns a NULL decision, gate is never
    # consulted, no metadata stamped.
    zoom_cooldown = ZoomCooldown(
        getattr(config, "ZOOM_TRIGGER_COOLDOWN_FRAMES", 5),
    )
    zoom_triggers_total = 0

    frames_processed = 0
    total_faces = 0
    faces_dropped = 0

    logger.info("Detection process started")

    while not shutdown_event.is_set():

        # ── Read next FramePacket (blocking with short timeout) ──
        # 10ms timeout: low latency while still yielding CPU when idle.
        # 100ms was too slow — added up to ~3 frames of lag at 30 FPS.
        try:
            packet: FramePacket = input_queue.get(timeout=0.01)
        except Empty:
            continue

        frame = packet.frame
        frame_h, frame_w = frame.shape[:2]

        # ── Detect ──
        t_start = time.perf_counter()
        raw_faces = _detect_faces(detector, frame)
        t_detect = (time.perf_counter() - t_start) * 1000  # ms

        # ── Filter ──
        faces = _filter_faces(raw_faces, frame_h, frame_w)

        # ── Rank and select top N faces (NEVER drop the frame) ──
        # Sort by confidence first, then by face area (larger = closer = more
        # important). This guarantees stable behaviour in crowded scenes:
        # we always process the best faces instead of discarding the entire frame.
        raw_count = len(faces)
        faces = sorted(
            faces,
            key=lambda f: (
                f["confidence"],
                (f["bbox"][2] - f["bbox"][0]) * (f["bbox"][3] - f["bbox"][1]),
            ),
            reverse=True,
        )

        if config.STRICT_FACE_LIMIT:
            # Only cap when count exceeds the limit
            if len(faces) > config.MAX_FACES_PER_FRAME:
                faces = faces[:config.MAX_FACES_PER_FRAME]
        else:
            # Always take top N (safe default)
            faces = faces[:config.MAX_FACES_PER_FRAME]

        num_faces = len(faces)
        frames_processed += 1
        total_faces += num_faces

        if raw_count == 0:
            logger.info("[Detection] frame=%d No faces detected", packet.frame_id)
        elif num_faces == 0:
            logger.warning("[Detection] frame=%d Faces filtered out (Total: %d)", packet.frame_id, raw_count)

        # ── Zoom trigger (Phase 1: flag + log only, no zoom executed) ──
        # Pure decision; cheap (a single max() over bbox sizes). The cooldown
        # gate suppresses repeat logs on idle/sparse cameras so the WARNING
        # band stays signal, not noise. The decision is also stamped on every
        # downstream FaceTask so Phase 2 can act without re-evaluating.
        zoom_decision = evaluate_zoom_trigger(faces)
        if zoom_decision.needs_zoom and zoom_cooldown.admit(packet.frame_id):
            zoom_triggers_total += 1
            logger.info(
                "[ZOOM_TRIGGER] cam=%s frame=%d faces=%d max_size=%d "
                "reason=%s small_regions=%d (total_triggers=%d)",
                camera_id, packet.frame_id, num_faces,
                zoom_decision.max_face_size_px, zoom_decision.reason,
                len(zoom_decision.small_face_regions), zoom_triggers_total,
            )

        # ── Send metadata to aggregator ──
        # The aggregator needs: how many FaceTasks to expect + the original
        # frame for rendering bounding boxes.
        #
        # ⚠️ KNOWN BOTTLENECK (MVP trade-off):
        # Passing the full frame through meta_queue means pickling ~6 MB
        # per frame (1080p BGR). This is the BIGGEST serialization cost
        # in the pipeline. For V2, consider:
        #   - multiprocessing.shared_memory ring buffer
        #   - Memory-mapped file with frame index
        #   - Only pass (frame_id, num_faces), store frames in shared dict
        # For MVP this works because meta_queue is small (maxsize=4) and
        # the aggregator drains it quickly.
        try:
            meta_queue.put_nowait((packet.frame_id, num_faces, frame))
        except Full:
            # Meta queue full — aggregator is backed up.
            # Log but don't block. This frame's results will be orphaned
            # and cleaned up by the aggregator's timeout mechanism.
            logger.warning(
                "Meta queue full — dropping metadata for frame=%d (%d faces)",
                packet.frame_id, num_faces,
            )

        # ── Create and dispatch FaceTasks ──
        dispatched = 0
        for face_id, face in enumerate(faces):
            bbox = face["bbox"]
            face_img = _crop_face(frame, bbox)

            task = FaceTask(
                frame_id=packet.frame_id,
                face_id=face_id,
                bbox=bbox,
                timestamp=packet.timestamp,
                det_score=face["confidence"],
                face_img=face_img,
                landmarks=face.get("landmarks"),
                # original_frame is intentionally NOT set (default None).
                # The full frame goes to the aggregator via meta_queue.
            )

            # Phase-1 zoom-trigger metadata. Cheap to attach (3 keys);
            # consumed by Phase-2 zoom executor and observability dashboards.
            # Stamped even when needs_zoom=False so the downstream contract
            # is uniform (no key-presence checks required).
            task.metadata["needs_zoom"] = zoom_decision.needs_zoom
            task.metadata["zoom_reason"] = zoom_decision.reason
            task.metadata["zoom_max_face_px"] = zoom_decision.max_face_size_px

            try:
                output_queue.put_nowait(task)
                dispatched += 1
            except Full:
                faces_dropped += 1
                if faces_dropped % 20 == 0:
                    logger.warning(
                        "Output queue full — dropped face (frame=%d face=%d, "
                        "total_dropped=%d)",
                        packet.frame_id, face_id, faces_dropped,
                    )

        # ── Periodic stats ──
        if frames_processed % _STATS_LOG_INTERVAL == 0:
            avg_faces = total_faces / frames_processed if frames_processed > 0 else 0
            zoom_rate_pct = (
                100.0 * zoom_triggers_total / frames_processed
                if frames_processed > 0 else 0.0
            )
            logger.info(
                "Detection stats: frames=%d, total_faces=%d, avg=%.1f/frame, "
                "dropped=%d, zoom_triggers=%d (%.1f%%)",
                frames_processed, total_faces, avg_faces, faces_dropped,
                zoom_triggers_total, zoom_rate_pct,
            )

    logger.info(
        "Detection shut down — frames=%d, faces=%d, dropped=%d, "
        "zoom_triggers=%d",
        frames_processed, total_faces, faces_dropped, zoom_triggers_total,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Standalone test — run with: python -m engine.pipeline.detection
# ═══════════════════════════════════════════════════════════════════════════════

def _test_detection():
    """
    Quick standalone test: capture → detection → display.

    Bypasses the full pipeline. Runs capture and detection in child processes,
    then draws bounding boxes in the main process.

    Verifies:
        1. Detector loads successfully
        2. Multiple faces detected per frame
        3. Filtering (min size, confidence) works
        4. FaceTasks are created with correct fields
        5. Meta queue carries frame metadata

    Run:
        python -m engine.pipeline.detection
    """
    from multiprocessing import Process
    from backend.engine.utils.logger import setup_logger
    from backend.engine.pipeline.capture import capture_process

    setup_logger("test")
    test_logger = logging.getLogger("test")

    # Pick the first configured camera for the smoke test. Reads from
    # the PostgreSQL-backed persistent store; falls back to USB
    # device 0 when the store is empty (fresh install).
    try:
        from backend.camera import store as _camera_store
        _cams = _camera_store.load()
    except Exception:
        _cams = []
    if _cams:
        cam_cfg = _cams[0]
        camera_id = cam_cfg["camera_id"]
        source = cam_cfg["source"]
    else:
        camera_id = "cam0"
        source = 0

    setup_logger(f"capture.{camera_id}")
    setup_logger("detection")

    shutdown = Event()

    # Queues
    q_capture = Queue(maxsize=config.CAPTURE_QUEUE_SIZE)
    q_faces = Queue(maxsize=config.DETECTION_QUEUE_SIZE)
    q_meta = Queue(maxsize=config.RESULT_QUEUE_SIZE)

    # Start capture + detection processes
    procs = [
        Process(
            target=capture_process,
            args=(camera_id, source, q_capture, shutdown, config.CAPTURE_QUEUE_SIZE),
            daemon=True,
        ),
        Process(
            target=detection_process,
            args=(camera_id, q_capture, q_faces, q_meta, shutdown),
            daemon=True,
        ),
    ]
    for p in procs:
        p.start()
        test_logger.info("Started %s (pid=%d)", p.name, p.pid)

    fps = 0.0
    prev_time = time.time()

    test_logger.info("Detection test running — press 'q' to quit")

    try:
        while not shutdown.is_set():
            # Get metadata (frame + expected face count)
            try:
                frame_id, num_faces, frame = q_meta.get(timeout=1.0)
            except Exception:
                continue

            # Collect FaceTasks for this frame (non-blocking drain)
            tasks = []
            for _ in range(num_faces):
                try:
                    task: FaceTask = q_faces.get(timeout=0.05)
                    tasks.append(task)
                except Exception:
                    break

            # FPS
            now = time.time()
            dt = now - prev_time
            if dt > 0:
                fps = 0.9 * fps + 0.1 * (1.0 / dt)
            prev_time = now

            # Draw results
            display = frame.copy()
            for t in tasks:
                x1, y1, x2, y2 = t.bbox
                color = (0, 255, 0)
                cv2.rectangle(display, (x1, y1), (x2, y2), color, 2)
                label = f"face={t.face_id} det={t.det_score:.2f}"
                cv2.putText(
                    display, label, (x1, y1 - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1,
                )

            # Overlay info
            cv2.putText(
                display,
                f"FPS: {fps:.1f} | Faces: {num_faces}",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2,
            )

            cv2.imshow("Detection Test", display)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    except KeyboardInterrupt:
        pass

    shutdown.set()
    for p in procs:
        p.join(timeout=3)
        if p.is_alive():
            p.terminate()
    cv2.destroyAllWindows()
    test_logger.info("Test complete")


if __name__ == "__main__":
    _test_detection()
