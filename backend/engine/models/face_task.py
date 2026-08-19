"""
face_task.py — Data structures that flow through the pipeline.

Every pipeline stage reads/writes specific fields on these objects.
Using dataclasses keeps them lightweight, pickle-safe, and self-documenting.

Pipeline data flow:
    ┌──────────┐         ┌───────────┐         ┌──────────┐         ┌───────────┐         ┌───────────┐         ┌──────────┐         ┌─────────────┐
    │ Capture  │──Q1────▶│ Detection │──Q2────▶│ Tracking │──Q3────▶│ Alignment │──Q4────▶│ Embedding │──Q5────▶│ Matching │──Q6────▶│ Aggregation │
    └──────────┘         └───────────┘         └──────────┘         └───────────┘         └───────────┘         └──────────┘         └─────────────┘
     FramePacket      FaceTask created      track_id set         aligned_face set       embedding set       identity set         FrameResult built
                      (bbox, face_img,      is_new_track set                            (cached if tracked) confidence set
                       landmarks, det_score)

    Detection also sends metadata to aggregation via a SEPARATE meta_queue:
        meta_queue: (frame_id, num_faces, original_frame)
    This tells the aggregator how many FaceTasks to collect before emitting
    a FrameResult. See pipeline/orchestrator.py for implementation.

PERFORMANCE NOTE — Pickling numpy arrays through multiprocessing Queues:
    ─────────────────────────────────────────────────────────────────────
    Every Queue.put() pickles the object; Queue.get() unpickles it.
    Numpy arrays are copied byte-for-byte during this process.

    Mitigations built into this design:
    1. Queue sizes are BOUNDED (config.py) — backpressure, not buffering
    2. Capture stage drops stale frames aggressively (drop-oldest policy)
    3. face_img is a SMALL crop (not the full 1080p frame)
    4. original_frame defaults to None — never sent unless explicitly needed
    5. drop_images() releases arrays early when no longer needed
    6. repr=False on all ndarrays prevents accidental serialization in logs

    For V2, consider shared memory (multiprocessing.shared_memory) or
    passing only indices into a memory-mapped ring buffer.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import time
import backend.config as config


# ═══════════════════════════════════════════════════════════════════════════════
# FramePacket — Capture → Detection
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FramePacket:
    """
    A raw camera frame moving from Capture → Detection.

    Produced by:  capture.py (Stage 1)
    Consumed by:  detection.py (Stage 2)

    The detection stage unpacks this, runs the detector on the full frame,
    and produces one FaceTask per detected face.

    After detection, this object is discarded. The original frame is forwarded
    to the aggregation stage via meta_queue as:
        meta_queue.put((frame_id, num_faces, frame))
    so the aggregator can pair it with completed FaceTasks for rendering.
    """

    # ── Payload ──
    frame: np.ndarray = field(repr=False)       # Full BGR image (H, W, 3), uint8
    #                         ^^^^^^^^^^
    #                         Suppress raw pixel data in repr/logging

    # ── Identity ──
    frame_id: int = 0                           # Monotonically increasing frame counter
    timestamp: float = field(default_factory=time.time)
    camera_id: str = ""                         # Source camera id (e.g. "cam_entry");
                                                # propagated through the pipeline so
                                                # downstream stages know which camera
                                                # this frame came from.


# ═══════════════════════════════════════════════════════════════════════════════
# FaceTask — The core unit of work flowing through stages 2–5
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FaceTask:
    """
    Represents a single detected face moving through the pipeline.

    One frame may produce N FaceTask objects (one per detected face).
    Each task is processed INDEPENDENTLY through alignment → embedding → matching.

    Field ownership by stage:
    ─────────────────────────────────────────────────────────────────────────────
    Field            Set by              Read by              Notes
    ─────────────────────────────────────────────────────────────────────────────
    frame_id         detection           all stages           Links back to frame
    face_id          detection           aggregation          Per-frame face index
    timestamp        detection           display/logging      From FramePacket
    bbox             detection           display/renderer     [x1, y1, x2, y2]
    det_score        detection           filtering/display    Detection confidence
    face_img         detection           alignment            Small crop from frame
    landmarks        detection           alignment            5-point; may be None
    original_frame   detection (opt)     —                    DEFAULT None (saves memory)
    aligned_face     alignment           embedding            112×112 normalized crop
    embedding        embedding           matching             512-d L2-normalized vector
    identity         matching            display              Name or "Unknown"
    confidence       matching            display              Cosine similarity score
    track_id         tracking            embedding/matching   Avoids re-embedding
    is_new_track     tracking            embedding            True → compute embedding
    metadata         any stage           any stage            Extensible catch-all
    ─────────────────────────────────────────────────────────────────────────────

    Size considerations (approximate per FaceTask in queue):
        face_img    ~150×150×3  =  ~67 KB   (small crop, NOT full 1080p)
        aligned_face 112×112×3  =  ~37 KB
        embedding    512×4      =   2 KB
        landmarks    5×2×4      =  40 bytes
        Total: ~106 KB per face — acceptable for bounded queues of size 4-8
    """

    # ── Stage 2: Detection (REQUIRED — set at creation) ──────────────────────

    frame_id: int                                   # Which frame this face came from
    face_id: int                                    # Index within that frame (0, 1, 2…)
    bbox: List[int]                                 # Bounding box [x1, y1, x2, y2] in frame coords
    timestamp: float = field(default_factory=time.time)
    camera_id: str = ""                             # Source camera id, propagated from the
                                                    # parent FramePacket. Used by tracking
                                                    # (per-camera track namespaces), matching
                                                    # (per-camera smoothing), and aggregation
                                                    # (per-camera FrameResults).

    # Detection confidence score from the face detector.
    # Use this to filter low-quality detections downstream.
    # Typical range: 0.0–1.0 (model-dependent).
    det_score: float = 0.0

    # Cropped face region from the original frame.
    # Shape: (crop_h, crop_w, 3), BGR, uint8.
    # This is a SMALL crop — NOT the full 1080p frame.
    # Set by detection; read by alignment.
    face_img: Optional[np.ndarray] = field(default=None, repr=False)

    # 5-point facial landmarks in FULL-FRAME coordinates:
    #   [[left_eye_x, left_eye_y],
    #    [right_eye_x, right_eye_y],
    #    [nose_x, nose_y],
    #    [left_mouth_x, left_mouth_y],
    #    [right_mouth_x, right_mouth_y]]
    #
    # Set by detection; read by alignment.
    # MAY BE None if the detector doesn't provide landmarks.
    # When None, alignment falls back to center-crop resizing.
    landmarks: Optional[np.ndarray] = field(default=None, repr=False)

    # Optional reference to the full original frame.
    # ⚠️ DEFAULT None — NEVER set this unless absolutely necessary.
    # A 1080p BGR frame is ~6 MB. Passing it through the queue means
    # pickling 6 MB per face per frame — instant FPS killer.
    # The aggregation stage gets the frame via meta_queue, not here.
    original_frame: Optional[np.ndarray] = field(default=None, repr=False)

    # ── Stage 3: Alignment ───────────────────────────────────────────────────

    # Geometrically normalized face aligned to canonical position.
    # Shape: (112, 112, 3), BGR, uint8.
    # Set by alignment; read by embedding.
    # Kept separate from face_img so we preserve the raw crop for debugging.
    aligned_face: Optional[np.ndarray] = field(default=None, repr=False)

    # ── Stage 4: Embedding ───────────────────────────────────────────────────

    # 512-dimensional L2-normalized feature vector (ArcFace).
    # Shape: (512,), float32. ~2 KB — negligible.
    # Set by embedding; read by matching.
    embedding: Optional[np.ndarray] = field(default=None, repr=False)

    # ── Stage 5: Matching ────────────────────────────────────────────────────

    identity: str = "Unknown"                       # Best-match name, or "Unknown"
    confidence: float = 0.0                         # Cosine similarity of best match

    # ── Tracking ──────────────────────────────────────────────────────────────

    # Assigned by the centroid tracker (pipeline/tracking.py).
    # When set, the embedding stage can SKIP re-embedding if the track_id
    # was already embedded in a recent frame — massive performance win.
    track_id: Optional[int] = None

    # True when this is the first time we see this track (no cached embedding).
    # False when the tracker matched this face to an existing track.
    # The embedding stage checks this: if False AND embedding cached → reuse.
    is_new_track: bool = True

    # ── Extensible metadata ──────────────────────────────────────────────────
    # Use this dict for ad-hoc data without changing the dataclass schema.
    # Examples:
    #   task.metadata["age"]         = 28            # future attribute stage
    #   task.metadata["anti_spoof"]  = True          # future liveness check
    #   task.metadata["blur_score"]  = 0.12          # face quality filter
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ── Convenience helpers ──────────────────────────────────────────────────

    @property
    def bbox_tuple(self) -> Tuple[int, int, int, int]:
        """Return bbox as (x1, y1, x2, y2) tuple for unpacking."""
        return tuple(self.bbox)

    @property
    def bbox_width(self) -> int:
        return self.bbox[2] - self.bbox[0]

    @property
    def bbox_height(self) -> int:
        return self.bbox[3] - self.bbox[1]

    @property
    def bbox_area(self) -> int:
        return self.bbox_width * self.bbox_height

    @property
    def bbox_center(self) -> Tuple[int, int]:
        """Center point of the bounding box."""
        cx = (self.bbox[0] + self.bbox[2]) // 2
        cy = (self.bbox[1] + self.bbox[3]) // 2
        return (cx, cy)

    @property
    def is_identified(self) -> bool:
        """True if this face matched a registered identity."""
        return self.identity != "Unknown"

    @property
    def has_embedding(self) -> bool:
        return self.embedding is not None

    @property
    def has_landmarks(self) -> bool:
        """Check if usable landmarks exist (not None, correct shape)."""
        return (
            self.landmarks is not None
            and self.landmarks.shape == (5, 2)
        )

    def drop_images(self):
        """
        Release image data to free memory after embedding is extracted.

        Call this after the embedding stage if you want to reduce the pickle
        size for the matching → aggregation hop. The aggregator only needs
        bbox + identity + confidence for rendering (it has the original frame
        from meta_queue).
        """
        self.face_img = None
        self.aligned_face = None
        self.original_frame = None

    def summary(self) -> str:
        """One-line human-readable summary for logging."""
        parts = [f"det={self.det_score:.2f}"]
        if self.has_landmarks:
            parts.append("lmk")
        if self.aligned_face is not None:
            parts.append("aligned")
        if self.has_embedding:
            parts.append("emb")
        if self.track_id is not None:
            parts.append(f"trk={self.track_id}")
        if self.is_identified:
            parts.append(f"id={self.identity}({self.confidence:.2f})")
        else:
            parts.append("Unknown")

        return (
            f"FaceTask(f={self.frame_id} n={self.face_id} "
            f"bbox={self.bbox} {' '.join(parts)})"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# FrameResult — Aggregation → Display
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FrameResult:
    """
    Aggregated result for one frame — ready for display/rendering.

    Produced by:  orchestrator.py (aggregation stage)
    Consumed by:  renderer.py (display loop in main process)

    Contains the original frame (received via meta_queue, NOT from FaceTasks)
    plus all completed FaceTask objects for that frame. The display renderer
    iterates over `faces` to draw bounding boxes, names, and confidence scores.
    """

    frame_id: int
    frame: np.ndarray = field(repr=False)           # Original full-frame for drawing
    camera_id: str = ""                             # Source camera id; lets the renderer
                                                    # route frames to per-camera windows.
    faces: List[FaceTask] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self):
        # Sort faces highest-confidence first, then cap to the per-frame
        # limit, so the renderer sees a stable ordered list and never
        # gets flooded by pathological scenes.
        #
        # This used to sit in the class body (sorted(...)/slice on the
        # Field descriptor) which crashed at import time — behaviour is
        # unchanged in spirit but now actually runs per instance.
        self.faces.sort(key=lambda f: f.confidence, reverse=True)
        if len(self.faces) > config.MAX_FACES_PER_FRAME:
            self.faces = self.faces[:config.MAX_FACES_PER_FRAME]

    @property
    def num_faces(self) -> int:
        return len(self.faces)

    @property
    def identified_faces(self) -> List[FaceTask]:
        """Only faces that matched a registered identity."""
        return [f for f in self.faces if f.is_identified]

    @property
    def unknown_faces(self) -> List[FaceTask]:
        """Faces that did not match any registered identity."""
        return [f for f in self.faces if not f.is_identified]
