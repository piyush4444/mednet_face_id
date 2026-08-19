"""
alignment.py — Stage 3: Face Alignment

Responsibilities:
    - Receive FaceTask objects (with cropped face_img + optional landmarks)
    - Produce a standardized 112×112 aligned face for embedding
    - Store result in task.aligned_face (original face_img is PRESERVED)
    - Push task downstream

Two alignment paths:
    ┌──────────────────────────────────────────────────────────────────┐
    │                      FaceTask arrives                            │
    │                           │                                     │
    │              has_landmarks? ──── YES ────▶ Affine warp          │
    │                    │                      (5-point → canonical)  │
    │                    NO                             │              │
    │                    │                              │              │
    │              Center-crop + resize                 │              │
    │              (fallback — less accurate)            │              │
    │                    │                              │              │
    │                    ▼                              ▼              │
    │               aligned_face (112×112×3, BGR, uint8)              │
    │                           │                                     │
    │                    Pixel normalization                           │
    │               (optional, for ArcFace compat)                    │
    └──────────────────────────────────────────────────────────────────┘

Design notes:
    - Alignment normalizes pose/rotation so the embedding model sees
      consistent input regardless of how the person is oriented.
    - The reference landmarks are the standard canonical positions for
      ArcFace's 112×112 input, from the InsightFace codebase.
    - This stage is pure CPU (geometric transform) — no GPU needed.
    - face_img is kept unchanged for debugging / future stages.
    - Landmarks from detection are in FULL-FRAME coordinates. Before
      computing the transform, they must be shifted to crop-local coords.
"""

import logging
import time
from multiprocessing import Queue, Event
from queue import Empty, Full

import cv2
import numpy as np

from backend.engine.models.face_task import FaceTask
import backend.config as config

logger = logging.getLogger("alignment")

# ── Periodic stats interval ──
_STATS_LOG_INTERVAL = 200


# ═══════════════════════════════════════════════════════════════════════════════
# Reference landmarks — canonical positions for 112×112 ArcFace input
# ═══════════════════════════════════════════════════════════════════════════════

# Standard "ideal" positions for:
#   [left_eye, right_eye, nose_tip, left_mouth_corner, right_mouth_corner]
# These come from the InsightFace codebase and define where each landmark
# should land in the output image for optimal ArcFace performance.
ARCFACE_REF_LANDMARKS = np.array(
    [
        [38.2946, 51.6963],   # left eye
        [73.5318, 51.5014],   # right eye
        [56.0252, 71.7366],   # nose tip
        [41.5493, 92.3655],   # left mouth corner
        [70.7299, 92.2041],   # right mouth corner
    ],
    dtype=np.float32,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Alignment functions
# ═══════════════════════════════════════════════════════════════════════════════

def _estimate_similarity_transform(
    src_pts: np.ndarray,
    dst_pts: np.ndarray,
) -> np.ndarray:
    """
    Estimate a similarity transform (rotation + uniform scale + translation)
    from source to destination points using least squares.

    This is more robust than cv2.getAffineTransform (which uses exactly 3 points
    and can be thrown off by one bad landmark). Using all 5 landmarks gives a
    least-squares best fit.

    Args:
        src_pts:  (N, 2) source landmarks.
        dst_pts:  (N, 2) destination landmarks.

    Returns:
        (2, 3) affine matrix for cv2.warpAffine.
    """
    n = src_pts.shape[0]

    # Build the linear system: dst = M @ src (in homogeneous coords)
    # We solve for [a, b, tx, ty] where:
    #   dst_x = a * src_x - b * src_y + tx
    #   dst_y = b * src_x + a * src_y + ty
    # This constrains the transform to similarity (no shear/non-uniform scale).
    A = np.zeros((2 * n, 4), dtype=np.float64)
    b = np.zeros(2 * n, dtype=np.float64)

    for i in range(n):
        sx, sy = src_pts[i]
        dx, dy = dst_pts[i]

        A[2 * i]     = [sx, -sy, 1, 0]
        A[2 * i + 1] = [sy,  sx, 0, 1]
        b[2 * i]     = dx
        b[2 * i + 1] = dy

    # Least-squares solve
    params, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
    a, b_val, tx, ty = params

    # Build 2×3 affine matrix
    M = np.array([
        [a,     -b_val, tx],
        [b_val,  a,     ty],
    ], dtype=np.float64)

    return M


def align_with_landmarks(
    face_img: np.ndarray,
    landmarks: np.ndarray,
    bbox: list,
    target_size: tuple,
) -> np.ndarray:
    """
    Warp a cropped face to canonical aligned position using 5-point landmarks.

    Args:
        face_img:     Cropped face image (BGR, uint8).
        landmarks:    (5, 2) array — landmarks in FULL-FRAME coordinates.
        bbox:         [x1, y1, x2, y2] — bounding box used for cropping.
        target_size:  (W, H) output size, e.g. (112, 112).

    Returns:
        Aligned face image (target_size[1], target_size[0], 3), BGR, uint8.
    """
    # ── Convert landmarks from full-frame to crop-local coordinates ──
    # Detection gives landmarks in frame space, but face_img is a crop
    # starting at (bbox[0], bbox[1]). Shift accordingly.
    x1, y1 = bbox[0], bbox[1]
    local_landmarks = landmarks.astype(np.float32).copy()
    local_landmarks[:, 0] -= x1
    local_landmarks[:, 1] -= y1

    # ── Estimate similarity transform (all 5 points, least-squares) ──
    M = _estimate_similarity_transform(local_landmarks, ARCFACE_REF_LANDMARKS)

    # ── Warp ──
    aligned = cv2.warpAffine(
        face_img,
        M,
        target_size,
        borderMode=cv2.BORDER_REPLICATE,
    )

    return aligned


def align_center_crop(
    face_img: np.ndarray,
    target_size: tuple,
) -> np.ndarray:
    """
    Fallback alignment: center-crop and resize when landmarks are unavailable.

    Less accurate than landmark-based alignment, but still produces a
    usable input for the embedding model. The face bounding box from
    the detector is usually roughly centered, so a simple resize
    is a reasonable approximation.

    Args:
        face_img:     Cropped face image (BGR, uint8).
        target_size:  (W, H) output size, e.g. (112, 112).

    Returns:
        Resized face image (target_size[1], target_size[0], 3), BGR, uint8.
    """
    h, w = face_img.shape[:2]
    target_w, target_h = target_size

    # ── Center-crop to square aspect ratio ──
    # The detector's bbox may not be square. Crop the center square
    # region before resizing to avoid distortion.
    if h != w:
        side = min(h, w)
        y_off = (h - side) // 2
        x_off = (w - side) // 2
        face_img = face_img[y_off:y_off + side, x_off:x_off + side]

    # ── Resize to target ──
    aligned = cv2.resize(
        face_img,
        target_size,
        interpolation=cv2.INTER_LINEAR,
    )

    return aligned


def normalize_pixels(face: np.ndarray) -> np.ndarray:
    """
    Simple pixel normalization for ArcFace compatibility.

    ArcFace models typically expect input in one of these formats:
      1. uint8 [0, 255] — InsightFace's get() handles normalization internally
      2. float32 [-1, 1] — manual feeding into ONNX/PyTorch

    For MVP (InsightFace integration), we keep uint8 and let the model
    handle normalization. This function is here as a placeholder for
    when you feed the model directly.

    Current behavior: NO-OP (returns input unchanged).
    To enable float normalization, uncomment the transform below.

    Args:
        face:  Aligned face image (112×112×3, BGR, uint8).

    Returns:
        Normalized face image (same shape, same or different dtype).
    """
    if config.NORMALIZE_PIXELS:
        face = face.astype(np.float32)
        face = (face - 127.5) / 127.5    # Scale to [-1, 1]
        return face

    # InsightFace's get() handles normalization internally — pass uint8 through.
    return face


# ═══════════════════════════════════════════════════════════════════════════════
# Main process loop
# ═══════════════════════════════════════════════════════════════════════════════

def alignment_process(
    input_queue: Queue,
    output_queue: Queue,
    shutdown_event: Event,
):
    """
    Main loop for the face alignment stage.

    Runs as an independent multiprocessing.Process. For each FaceTask:
      1. Check if landmarks are available (task.has_landmarks)
      2. If YES → affine warp using 5-point similarity transform
      3. If NO  → center-crop + resize fallback
      4. Store result in task.aligned_face (face_img is preserved)
      5. Apply pixel normalization
      6. Push downstream (non-blocking, drop if full)

    Args:
        input_queue:     Receives FaceTask objects from detection.
        output_queue:    Sends aligned FaceTask objects to embedding.
                         Non-blocking — drops tasks if full.
        shutdown_event:  multiprocessing.Event — set to stop this process.
    """
    from backend.engine.utils.logger import setup_logger
    setup_logger("alignment")

    target_size = config.ALIGNED_FACE_SIZE  # (112, 112)

    aligned_count = 0
    fallback_count = 0
    failed_count = 0
    dropped_count = 0

    logger.info(
        "Alignment process started (target=%dx%d)",
        target_size[0], target_size[1],
    )

    while not shutdown_event.is_set():

        # ── Read next FaceTask ──
        try:
            task: FaceTask = input_queue.get(timeout=0.01)
        except Empty:
            continue

        # ── Guard: no face image ──
        if task.face_img is None:
            logger.warning(
                "No face_img for frame=%d face=%d — passing through",
                task.frame_id, task.face_id,
            )
            try:
                output_queue.put_nowait(task)
            except Full:
                dropped_count += 1
            continue

        # ── Guard: face too small to align meaningfully ──
        h, w = task.face_img.shape[:2]
        if h < config.MIN_FACE_SIZE or w < config.MIN_FACE_SIZE:
            logger.debug(
                "Face too small to align (%dx%d) frame=%d face=%d — skipping",
                w, h, task.frame_id, task.face_id,
            )
            try:
                output_queue.put_nowait(task)
            except Full:
                dropped_count += 1
            continue

        # ── Align ──
        try:
            if task.has_landmarks:
                # Landmark-based affine alignment (accurate)
                task.aligned_face = align_with_landmarks(
                    task.face_img,
                    task.landmarks,
                    task.bbox,
                    target_size,
                )
                aligned_count += 1
            else:
                # Center-crop fallback (no landmarks — e.g. Haar cascade)
                task.aligned_face = align_center_crop(
                    task.face_img,
                    target_size,
                )
                fallback_count += 1

            # ── Pixel normalization ──
            task.aligned_face = normalize_pixels(task.aligned_face)

        except Exception as e:
            failed_count += 1
            logger.warning(
                "Landmark alignment failed for frame=%d face=%d: %s — "
                "falling back to center-crop",
                task.frame_id, task.face_id, e,
            )
            # Never leave aligned_face as None — always attempt center-crop fallback.
            # A rough alignment is better than no alignment.
            try:
                task.aligned_face = align_center_crop(task.face_img, target_size)
                task.aligned_face = normalize_pixels(task.aligned_face)
            except Exception:
                # Truly unrecoverable — pass through with aligned_face=None.
                # Embedding stage will handle the None.
                logger.error(
                    "Center-crop fallback also failed for frame=%d face=%d",
                    task.frame_id, task.face_id,
                )

        # ── Push downstream (non-blocking) ──
        try:
            output_queue.put_nowait(task)
        except Full:
            dropped_count += 1
            if dropped_count % 20 == 0:
                logger.warning(
                    "Output queue full — dropped task (frame=%d face=%d, "
                    "total_dropped=%d)",
                    task.frame_id, task.face_id, dropped_count,
                )

        # ── Periodic stats ──
        total = aligned_count + fallback_count + failed_count
        if total > 0 and total % _STATS_LOG_INTERVAL == 0:
            logger.info(
                "Alignment stats: landmark=%d, fallback=%d, failed=%d, "
                "dropped=%d",
                aligned_count, fallback_count, failed_count, dropped_count,
            )

    logger.info(
        "Alignment shut down — landmark=%d, fallback=%d, failed=%d, "
        "dropped=%d",
        aligned_count, fallback_count, failed_count, dropped_count,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Standalone test — run with: python -m engine.pipeline.alignment
# ═══════════════════════════════════════════════════════════════════════════════

def _test_alignment():
    """
    Quick standalone test for both alignment paths.

    Creates synthetic FaceTasks with and without landmarks, runs them
    through alignment, and displays the results.

    Run:
        python -m engine.pipeline.alignment
    """
    from backend.engine.utils.logger import setup_logger

    setup_logger("alignment")
    test_logger = setup_logger("test")

    target = config.ALIGNED_FACE_SIZE

    # ── Test 1: Center-crop fallback (no landmarks) ──
    test_logger.info("Test 1: Center-crop fallback (no landmarks)")
    fake_face = np.random.randint(0, 255, (180, 150, 3), dtype=np.uint8)
    # Draw a cross so we can see alignment visually
    cv2.line(fake_face, (75, 0), (75, 180), (0, 255, 0), 2)
    cv2.line(fake_face, (0, 90), (150, 90), (0, 255, 0), 2)

    result_fallback = align_center_crop(fake_face, target)
    test_logger.info(
        "  Input: %s → Output: %s",
        fake_face.shape, result_fallback.shape,
    )
    assert result_fallback.shape == (target[1], target[0], 3), "Wrong output shape!"

    # ── Test 2: Landmark-based alignment ──
    test_logger.info("Test 2: Landmark-based alignment")
    fake_face_2 = np.random.randint(0, 255, (200, 200, 3), dtype=np.uint8)
    # Simulate landmarks (in crop-local space + bbox offset)
    bbox = [100, 50, 300, 250]  # face at x=100..300, y=50..250 in frame
    # Landmarks in full-frame coords (roughly matching face features)
    landmarks = np.array([
        [145, 110],   # left eye
        [245, 110],   # right eye
        [195, 150],   # nose
        [155, 195],   # left mouth
        [235, 195],   # right mouth
    ], dtype=np.float32)

    result_landmark = align_with_landmarks(fake_face_2, landmarks, bbox, target)
    test_logger.info(
        "  Input: %s → Output: %s",
        fake_face_2.shape, result_landmark.shape,
    )
    assert result_landmark.shape == (target[1], target[0], 3), "Wrong output shape!"

    # ── Test 3: Pixel normalization ──
    test_logger.info("Test 3: Pixel normalization")
    normalized = normalize_pixels(result_landmark)
    test_logger.info(
        "  dtype=%s, range=[%s, %s]",
        normalized.dtype, normalized.min(), normalized.max(),
    )

    # ── Display if possible ──
    try:
        cv2.imshow("Original (test 1)", fake_face)
        cv2.imshow("Fallback aligned", result_fallback)
        cv2.imshow("Landmark aligned", result_landmark)
        test_logger.info("Displaying results — press any key to close")
        cv2.waitKey(0)
        cv2.destroyAllWindows()
    except Exception:
        test_logger.info("No display available — skipping visual output")

    test_logger.info("All alignment tests passed!")


if __name__ == "__main__":
    _test_alignment()
