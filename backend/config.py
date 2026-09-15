"""
config.py — Central configuration for the face recognition pipeline.

All tunable parameters live here. Import this module from any stage
to access consistent settings across the entire system.
"""

import os as _os

# ──────────────────────── Hardware Scaling ────────────────────────

CPU_CORES = max(1, int(_os.getenv("CPU_CORES", "6")))
GPU_ENABLED = _os.getenv("GPU_ENABLED", "true").strip().lower() in {
    "1", "true", "yes", "on",
}

# Dynamic face scaling
MAX_FACES_BASE = 6          # safe baseline (low system)
MAX_FACES_MAX = 20          # upper cap (high system)

# multiplier per core
FACES_PER_CORE = 1.5


# ──────────────────────────── Camera ────────────────────────────
# The runtime camera roster lives in PostgreSQL ``camera_master`` and is
# managed via the admin API (see backend/camera/store.py). This module
# only carries pipeline-level tuning constants — nothing per-camera.

FRAME_WIDTH = 1920
FRAME_HEIGHT = 1080
CAMERA_FPS = 25

# Optional frame downscale before entering the pipeline.
# Resizing 1080p → 960 on the long edge (aspect preserved) cuts frame
# pickle size from ~6 MB to ~1.5 MB per queue hop — big win when many
# cameras feed a single AI worker. Detector resizes internally to
# DETECTION_INPUT_SIZE anyway, so quality is unchanged at sensible caps.
RESIZE_FRAME = True                 # Set False to keep original resolution
PROCESS_MAX_DIM = 960               # Long-edge cap (aspect ratio preserved)
PROCESS_WIDTH = 640                 # [DEPRECATED, kept for legacy callers]
PROCESS_HEIGHT = 480                # [DEPRECATED, kept for legacy callers]

# ───────────────────────── Concurrency ──────────────────────────
# Number of AI worker processes that consume from the shared frame
# queue. Identity smoothing lives in EventProcessor (single-process
# observer), so increasing this scales inference throughput without
# fragmenting tracker state. See manager.start_ai_workers for details.
NUM_AI_WORKERS = 1

# ──────────────────────────── Queues ────────────────────────────
# Bounded queue sizes — small values enforce backpressure & frame freshness.
CAPTURE_QUEUE_SIZE = 2
RESULT_QUEUE_SIZE = 4
# Queue sizes that depend on MAX_FACES_PER_FRAME are defined below
# (after the final computed value).

# ────────────────────────── Detection ───────────────────────────
DETECTION_CONFIDENCE = 0.42          # Minimum confidence to keep a detected face
DETECTION_INPUT_SIZE = (800, 800)   # Resize for detector (speed vs accuracy)
                                    # MUST be divisible by 32 — RetinaFace uses
                                    # feature pyramid strides [8,16,32]; non-multiples
                                    # cause "operands could not be broadcast" errors
                                    # (anchors generated at declared size, network
                                    # output computed at padded size). Valid: 640,
                                    # 672, 704, 736, 768, 800. NOT valid: 720.
MIN_FACE_SIZE = 24                  # Minimum face width/height in pixels
                                    # Faces smaller than this are filtered out —
                                    # too small to align/recognize reliably
STRICT_FACE_LIMIT = False           # If True, only cap faces when count exceeds
                                    # MAX_FACES_PER_FRAME. If False (default),
                                    # always sort + slice to top N.
MAX_TOTAL_FACES = 30
# ``MAX_FACES_PER_CAMERA`` is a per-frame cap used by the detector to
# bound work when many faces appear at once. We pre-size assuming up
# to ``EXPECTED_MAX_CAMERAS`` simultaneously active feeds. The actual
# camera count varies at runtime (cameras can be added/removed via the
# admin API), but this constant is fixed at import time — overshooting
# is benign (slightly tighter per-camera cap), undershooting just
# means a few extra faces get processed per frame on a single feed.
EXPECTED_MAX_CAMERAS = 8
MAX_FACES_PER_CAMERA = max(4, MAX_TOTAL_FACES // EXPECTED_MAX_CAMERAS)

# ────────────── Multi-scale Detection — Zoom Trigger (Phase 1) ──
# Phase 1 only emits a flag + log when the detector probably missed a
# distant face; no zoom is executed yet. Phase 2 will hook the same
# signal to run a region-zoomed re-detection pass.
#
# Threshold lives in the SAME coordinate space as MIN_FACE_SIZE — i.e.
# the post-PROCESS_MAX_DIM frame. With PROCESS_MAX_DIM=960, a face
# below 60 px is roughly a person ≥3 m from a 95° HFOV camera; that's
# the regime where we want a zoom pass.
#
# Cooldown bounds log spam (and Phase 2 GPU cost) on idle cameras —
# 5 frames at 25 FPS ≈ 200 ms between evaluations.
ZOOM_TRIGGER_ENABLED = False        # OFF — re-enable after IP-cam swap to baseline new lens
ZOOM_SMALL_FACE_THRESHOLD_PX = 60
ZOOM_TRIGGER_COOLDOWN_FRAMES = 5

# ────────────────────────── Alignment ───────────────────────────
ALIGNED_FACE_SIZE = (112, 112)      # ArcFace canonical input size
NORMALIZE_PIXELS = False            # If True: scale pixels to [-1, 1] float32
                                    # False for InsightFace (handles it internally)
                                    # True when feeding PyTorch/ONNX models directly

# ────────────────────────── Embedding ───────────────────────────
EMBEDDING_DIM = 512                 # ArcFace produces 512-d vectors
GPU_DEVICE_ID = int(_os.getenv(
    "GPU_DEVICE_ID", "0" if GPU_ENABLED else "-1"
))                                  # 0 = first GPU (CUDA), -1 = CPU only
                                    # Used by InsightFace, ONNX Runtime, PyTorch
GPU_SCALING_FACTOR = 2.0 if GPU_ENABLED else 1.0

MAX_FACES_PER_FRAME = min(
    MAX_FACES_MAX,
    int(CPU_CORES * FACES_PER_CORE * GPU_SCALING_FACTOR)
)

# ── Derived queue sizes (depend on MAX_FACES_PER_FRAME) ──
DETECTION_QUEUE_SIZE = MAX_FACES_PER_FRAME // 2 + 2
ALIGNMENT_QUEUE_SIZE = MAX_FACES_PER_FRAME + 2
EMBEDDING_QUEUE_SIZE = MAX_FACES_PER_FRAME + 2
MATCHING_QUEUE_SIZE = MAX_FACES_PER_FRAME + 2

# ────────────────────────── Matching ────────────────────────────
SIMILARITY_THRESHOLD = 0.60         # Cosine similarity cutoff for "known" vs "unknown"
FAISS_TOP_K = 3                     # Top-k neighbours; aggregated by max-score-per-identity
MAX_EMBEDDINGS_PER_IDENTITY = 10    # Max embeddings per person (prevents duplicates)
                                    # Multiple angles/lighting improve accuracy,
                                    # but beyond ~10 there's diminishing returns
SMOOTHING_WINDOW = 5                # Majority vote window size (frames)
                                    # Eliminates identity flickering.
                                    # 5 at 30 FPS ≈ 170ms of history.
                                    # Set to 1 to disable smoothing.

# ──────────────────────── Database Paths ────────────────────────
# Anchored to the project root (this file's parent's parent) so that the
# FAISS index is read/written at the SAME absolute location regardless of
# the cwd uvicorn is launched from. A relative "database/..." path causes
# silent drift: starting the server from project root vs. backend/app
# writes to two different files, and old registrations vanish from the
# active index.
_PROJECT_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
DATABASE_DIR = _os.path.join(_PROJECT_ROOT, "database")
FAISS_INDEX_PATH = _os.path.join(_PROJECT_ROOT, "database", "face_index.bin")
NAME_MAP_PATH = _os.path.join(_PROJECT_ROOT, "database", "name_map.json")
FACE_IMAGES_DIR = _os.path.join(_PROJECT_ROOT, "database", "faces")

# ────────────────────────── Display ─────────────────────────────
DISPLAY_SCALE = 0.6                 # Downscale display window (1.0 = full 1080p)
BBOX_COLOR_KNOWN = (0, 255, 0)      # Green
BBOX_COLOR_UNKNOWN = (0, 0, 255)    # Red
FONT_SCALE = 0.7
FONT_THICKNESS = 1.5

# ──────────────────────── Tracking ──────────────────────────
TRACKING_QUEUE_SIZE = 8             # Queue between detection → tracking
TRACKING_DISTANCE_THRESHOLD = 50    # Max centroid distance (pixels) to match a track
TRACK_MAX_AGE = 10                  # Frames before an unseen track is removed

# ──────────────────────── Performance ───────────────────────────
AGGREGATION_TIMEOUT = 0.35           # Max seconds to wait for all faces in a frame
LOG_LEVEL = "INFO"

# Motion-based frame skipping — skip detector/recognizer when the scene
# hasn't changed (idle hallway). Cheap 64×64 grayscale mean-abs-diff.
#   THRESHOLD  ~0..255; ~2.5 catches ~1% pixel change, ignores codec noise.
#   MAX_SKIP_S forces a pipeline run every N seconds regardless, so
#              trackers still tick and aged tracks expire predictably.
MOTION_SKIP_ENABLED = True
MOTION_SKIP_THRESHOLD = 2.5
MOTION_SKIP_MAX_INTERVAL_S = 2.0

# ──────────────────────── Logging / console ─────────────────────
# Master switch for verbose console output.
#
#   SYNORA_DEBUG_LOG=true   → root logger at DEBUG, all print() pass through
#   SYNORA_DEBUG_LOG=false  → root logger at WARNING (default)
#                             DEBUG/INFO log calls suppressed.
#                             print() is NEVER monkey-patched — third-party
#                             libraries, tracebacks, and system messages
#                             always work.
#
# Override per-run via env:
#   SYNORA_DEBUG_LOG=true uvicorn backend.app.main:app
#
# WARNING and ERROR always pass through so real problems stay visible.
import os as _os_log
import logging as _logging_log

ENABLE_LOGGING = _os_log.getenv("SYNORA_DEBUG_LOG", "false").lower() == "true"

if ENABLE_LOGGING:
    _logging_log.getLogger().setLevel(getattr(_logging_log, LOG_LEVEL, _logging_log.DEBUG))
else:
    # Suppress verbose log channels — warn/error untouched.
    # NOTE: We intentionally do NOT touch builtins.print. Monkey-patching
    # print() silences every library in the process (numpy, opencv,
    # sqlalchemy, etc.) and swallows critical error messages from code
    # we don't control. Logger-level gating is the correct approach.
    _logging_log.getLogger().setLevel(_logging_log.WARNING)
