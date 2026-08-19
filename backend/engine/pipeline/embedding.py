"""
embedding.py — Stage 4: Face Embedding (Vectorization)

Responsibilities:
    - Receive aligned FaceTask objects (112×112 face images)
    - Run inference through InsightFace ArcFace → 512-d vector
    - L2-normalize the embedding (required for cosine similarity)
    - Store result in task.embedding
    - Drop image data after embedding (saves pickle cost)

Performance profile:
    ─────────────────────────────────────────────────────────────
    This is the HEAVIEST stage in the pipeline.

    Stage          Typical latency     Bottleneck
    ─────────────────────────────────────────────────────────────
    Capture        ~1 ms               I/O
    Detection      ~10-30 ms           GPU/CPU inference
    Alignment      ~1 ms               CPU (affine warp)
    Embedding      ~15-50 ms  ◀◀◀      GPU/CPU inference (heaviest)
    Matching       ~0.1 ms             FAISS lookup
    ─────────────────────────────────────────────────────────────

    Optimizations applied:
    1. Model loaded ONCE at process start (not per-task)
    2. track_id cache skips re-embedding known tracked faces (future)
    3. drop_images() after embedding reduces downstream pickle cost
    4. Non-blocking queue push — never waits on downstream

Model:
    InsightFace FaceAnalysis with "buffalo_l" model pack (ArcFace).
    _load_recognizer() and extract_embedding() isolate the model.
    The rest of the pipeline is model-agnostic.

Normalization:
    L2 normalization is CRITICAL. FAISS IndexFlatIP computes inner product,
    which equals cosine similarity ONLY when vectors are unit-length.
    Every embedding MUST be normalized before leaving this stage.
"""

import logging
import os
import sys
import time
from multiprocessing import Queue, Event
from queue import Empty, Full

import cv2
import numpy as np

# ── CUDA DLL setup (must run BEFORE onnxruntime / insightface import) ─────────
# On Windows, pip-installed NVIDIA packages (nvidia-cublas-cu12, nvidia-cudnn-cu12,
# etc.) place DLLs in site-packages/nvidia/<lib>/bin/. Python and ONNX Runtime
# don't search there automatically. We register these directories so that
# onnxruntime_providers_cuda.dll can find its dependencies (cublasLt64_12.dll,
# cudnn64_9.dll, cufft64_11.dll, etc.) at load time.
def _register_nvidia_dll_dirs():
    """Add pip-installed NVIDIA CUDA DLL directories to the DLL search path."""
    nvidia_dir = os.path.join(sys.prefix, "Lib", "site-packages", "nvidia")
    if not os.path.isdir(nvidia_dir):
        return
    for lib_name in os.listdir(nvidia_dir):
        bin_dir = os.path.join(nvidia_dir, lib_name, "bin")
        if os.path.isdir(bin_dir):
            # os.add_dll_directory: used by Python's ctypes / DLL loader
            os.add_dll_directory(bin_dir)
            # PATH: used by ONNX Runtime's native LoadLibrary calls
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")

if sys.platform == "win32":
    _register_nvidia_dll_dirs()

# Suppress noisy ONNX Runtime CUDA-probe errors at startup.
# ORT tries to load CUDAExecutionProvider and logs ERROR-level messages
# to stderr when CUDA DLLs are missing — even if CPU fallback works fine.
# Level 3 = WARNING only.
os.environ.setdefault("ORT_LOG_LEVEL", "3")

from backend.engine.models.face_task import FaceTask
import backend.config as config

logger = logging.getLogger("embedding")

# ── Periodic stats interval ──
_STATS_LOG_INTERVAL = 100


# ═══════════════════════════════════════════════════════════════════════════════
# Model loading — InsightFace ArcFace (buffalo_l)
# ═══════════════════════════════════════════════════════════════════════════════

def _load_recognizer():
    """
    Load the InsightFace ArcFace recognition model (buffalo_l).

    Returns ONLY the recognition sub-model, not the full FaceAnalysis app.
    We use .get_feat() for direct embedding extraction from pre-aligned
    faces — bypassing InsightFace's internal detection/alignment pipeline
    which expects full-frame images and fails on 112×112 crops.

    FaceAnalysis is used only as a loader: it downloads weights on first
    run, discovers model files, and initializes ONNX sessions. After
    loading, we extract app.models['recognition'] and discard the rest.

    GPU/CPU selection is controlled by config.GPU_DEVICE_ID:
        0  = first CUDA GPU
        -1 = CPU only (fallback)

    Returns:
        The ArcFace recognition model (ArcFaceONNX instance).
        Has .get_feat(img) for direct embedding from aligned face.

    Raises:
        RuntimeError: If model cannot be loaded (missing weights,
        incompatible ONNX Runtime, etc.). Caught by the process
        loop — pipeline degrades gracefully.
    """
    import io
    from contextlib import redirect_stdout, redirect_stderr
    from insightface.app import FaceAnalysis

    ctx_id = config.GPU_DEVICE_ID

    logger.info(
        "Loading InsightFace ArcFace model (buffalo_l, ctx_id=%d)...",
        ctx_id,
    )

    # FaceAnalysis downloads model weights on first run (~300 MB).
    # We must load the full pack (FaceAnalysis asserts 'detection' exists),
    # but we only keep the recognition model.
    # Redirect stdout/stderr to suppress InsightFace's verbose "Applied
    # providers" / "find model" prints that clutter pipeline logs.
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            app = FaceAnalysis(name="buffalo_l")
            app.prepare(ctx_id=ctx_id, det_size=(160, 160))
    except Exception as e:
        # GPU init can fail (no CUDA, wrong driver version, etc.).
        # Fall back to CPU before giving up entirely.
        if ctx_id >= 0:
            logger.warning(
                "GPU init failed (ctx_id=%d): %s — falling back to CPU",
                ctx_id, e,
            )
            try:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    app = FaceAnalysis(name="buffalo_l")
                    app.prepare(ctx_id=-1, det_size=(160, 160))
                logger.info("InsightFace loaded on CPU (fallback)")
            except Exception as cpu_err:
                logger.error("CPU fallback also failed: %s", cpu_err)
                raise RuntimeError(
                    f"Cannot load InsightFace model: GPU={e}, CPU={cpu_err}"
                ) from cpu_err
        else:
            raise RuntimeError(f"Cannot load InsightFace model: {e}") from e

    # Extract just the recognition model — this is all we need.
    # .get_feat() takes a pre-aligned 112×112 BGR image and returns
    # the embedding directly, no detection or re-alignment involved.
    rec_model = app.models.get("recognition")
    if rec_model is None:
        raise RuntimeError(
            "InsightFace loaded but 'recognition' model not found. "
            f"Available models: {list(app.models.keys())}"
        )

    # Log the actual ONNX execution provider (confirms GPU vs CPU)
    providers = rec_model.session.get_providers() if hasattr(rec_model, 'session') else ["unknown"]
    using_gpu = any("CUDA" in p or "Tensorrt" in p for p in providers)
    logger.info(
        "ArcFace recognition model ready — ONNX providers: %s (GPU=%s, input=%s)",
        providers, using_gpu, rec_model.input_size,
    )
    return rec_model


def _l2_normalize(embedding: np.ndarray) -> np.ndarray:
    """
    L2-normalize a vector to unit length.

    FAISS IndexFlatIP requires unit vectors so that inner product = cosine sim.
    This is a hard requirement — never skip this step.

    Args:
        embedding:  Raw embedding vector (any length).

    Returns:
        Unit-length vector (same shape), or zero vector if input norm is zero.
    """
    norm = np.linalg.norm(embedding)
    if norm > 0:
        return embedding / norm
    return embedding


def _ensure_uint8(aligned_face: np.ndarray) -> np.ndarray:
    """
    Ensure aligned face is uint8 BGR — the format .get_feat() expects.

    .get_feat() calls cv2.dnn.blobFromImages internally, which handles
    BGR→RGB conversion (swapRB=True) and mean/std normalization.
    We only need to undo any float normalization from Stage 3.

    Args:
        aligned_face:  Aligned face from Stage 3 (112×112, BGR, uint8 or float32).

    Returns:
        uint8 BGR image ready for .get_feat().
    """
    if aligned_face.dtype == np.uint8:
        return aligned_face

    face = aligned_face
    if face.min() < 0:
        # Undo [-1, 1] normalization from config.NORMALIZE_PIXELS
        face = (face * 127.5) + 127.5
    return face.clip(0, 255).astype(np.uint8)


def extract_embedding(recognizer, aligned_face: np.ndarray) -> np.ndarray:
    """
    Extract a normalized 512-d embedding from an aligned face image.

    Uses the ArcFace recognition model's .get_feat() method, which
    takes a pre-aligned face image directly — no detection or
    re-alignment step. This is the correct API for our pipeline
    since Stage 3 already produces aligned 112×112 faces.

    .get_feat() internally:
      1. Creates a blob via cv2.dnn.blobFromImages (handles BGR→RGB)
      2. Normalizes pixels (mean=127.5, std=127.5)
      3. Runs ONNX inference
      4. Returns raw embedding

    Args:
        recognizer:    ArcFace recognition model from _load_recognizer().
        aligned_face:  Aligned face image (112×112, BGR, uint8 or float32).

    Returns:
        L2-normalized 512-d float32 vector.
    """
    face = _ensure_uint8(aligned_face)

    # .get_feat() accepts a single image or a list of images.
    # Single image → returns shape (1, 512). We flatten to (512,).
    raw_embedding = recognizer.get_feat(face)

    embedding = raw_embedding.astype(np.float32).flatten()
    return _l2_normalize(embedding)


# ═══════════════════════════════════════════════════════════════════════════════
# Main process loop
# ═══════════════════════════════════════════════════════════════════════════════

def embedding_process(
    input_queue: Queue,
    output_queue: Queue,
    shutdown_event: Event,
):
    """
    Main loop for the face embedding stage.

    Runs as an independent multiprocessing.Process. For each FaceTask:
      1. Skip if aligned_face is None (alignment failed completely)
      2. Run the recognition model to get a 512-d vector
      3. L2-normalize the vector
      4. Store in task.embedding
      5. Optionally drop image data to reduce downstream pickle cost
      6. Push downstream (non-blocking, drop if full)

    Args:
        input_queue:     Receives aligned FaceTask objects from alignment.
        output_queue:    Sends FaceTask objects (with embeddings) to matching.
                         Non-blocking — drops tasks if full.
        shutdown_event:  multiprocessing.Event — set to stop this process.
    """
    from backend.engine.utils.logger import setup_logger
    setup_logger("embedding")

    # ── Load model ONCE (expensive — may download weights on first run) ──
    t_load = time.perf_counter()
    recognizer = _load_recognizer()
    load_time = (time.perf_counter() - t_load) * 1000

    embedded_count = 0
    skipped_count = 0
    failed_count = 0
    dropped_count = 0
    cache_hit_count = 0
    total_inference_ms = 0.0

    # Embedding cache keyed by track_id — reuse embeddings for tracked faces.
    # Entries are evicted when the track disappears (no cleanup needed here;
    # the tracker prunes stale tracks and new track_ids won't collide).
    # Bounded by number of active tracks (typically < 10).
    _embedding_cache: dict = {}

    logger.info(
        "Embedding process started (model loaded in %.0fms, dim=%d)",
        load_time, config.EMBEDDING_DIM,
    )

    while not shutdown_event.is_set():

        # ── Read next FaceTask ──
        try:
            task: FaceTask = input_queue.get(timeout=0.01)
        except Empty:
            continue

        # ── Guard: no aligned face ──
        if task.aligned_face is None:
            skipped_count += 1
            task.metadata["embedding_skipped"] = True
            task.metadata["skip_reason"] = "no_aligned_face"
            logger.debug(
                "No aligned_face for frame=%d face=%d — skipping embedding",
                task.frame_id, task.face_id,
            )
            # Still push downstream — matching will mark as Unknown
            try:
                output_queue.put_nowait(task)
            except Full:
                dropped_count += 1
            continue

        # ── Guard: wrong input shape ──
        expected_h, expected_w = config.ALIGNED_FACE_SIZE[1], config.ALIGNED_FACE_SIZE[0]
        if task.aligned_face.shape[:2] != (expected_h, expected_w):
            skipped_count += 1
            task.metadata["embedding_skipped"] = True
            task.metadata["skip_reason"] = f"bad_shape_{task.aligned_face.shape}"
            logger.warning(
                "Bad aligned_face shape %s for frame=%d face=%d — "
                "expected (%d, %d, 3), skipping",
                task.aligned_face.shape, task.frame_id, task.face_id,
                expected_h, expected_w,
            )
            try:
                output_queue.put_nowait(task)
            except Full:
                dropped_count += 1
            continue

        # ── Track-based embedding cache (skip re-embedding) ──
        if (
            task.track_id is not None
            and not task.is_new_track
            and task.track_id in _embedding_cache
        ):
            task.embedding = _embedding_cache[task.track_id]
            cache_hit_count += 1
            task.drop_images()
            try:
                output_queue.put_nowait(task)
            except Full:
                dropped_count += 1
            continue

        # ── Extract embedding ──
        try:
            t_start = time.perf_counter()
            task.embedding = extract_embedding(recognizer, task.aligned_face)
            inference_ms = (time.perf_counter() - t_start) * 1000
            total_inference_ms += inference_ms
            embedded_count += 1

            # Sanity check: verify embedding shape and normalization
            assert task.embedding.shape == (config.EMBEDDING_DIM,), \
                f"Bad embedding shape: {task.embedding.shape}"
            assert abs(np.linalg.norm(task.embedding) - 1.0) < 1e-5, \
                f"Embedding not normalized: norm={np.linalg.norm(task.embedding)}"

            # Cache embedding for this track
            if task.track_id is not None:
                _embedding_cache[task.track_id] = task.embedding

        except Exception as e:
            failed_count += 1
            task.metadata["embedding_skipped"] = True
            task.metadata["skip_reason"] = f"inference_error: {e}"
            logger.warning(
                "Embedding failed for frame=%d face=%d: %s",
                task.frame_id, task.face_id, e,
            )
            # task.embedding stays None → matching will mark as Unknown

        # ── Drop image data to reduce pickle cost ──
        # After embedding, we no longer need the pixel data.
        # The aggregator has the original frame via meta_queue.
        # Matching only needs the embedding vector (~2 KB vs ~100 KB).
        task.drop_images()

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
        total_processed = embedded_count + cache_hit_count
        if total_processed > 0 and total_processed % _STATS_LOG_INTERVAL == 0:
            avg_ms = total_inference_ms / embedded_count if embedded_count > 0 else 0
            logger.info(
                "Embedding stats: embedded=%d, cached=%d, skipped=%d, "
                "failed=%d, dropped=%d, avg=%.1fms/face",
                embedded_count, cache_hit_count, skipped_count,
                failed_count, dropped_count, avg_ms,
            )

    # ── Final stats ──
    avg_ms = (total_inference_ms / embedded_count) if embedded_count > 0 else 0
    logger.info(
        "Embedding shut down — embedded=%d, cached=%d, skipped=%d, "
        "failed=%d, dropped=%d, avg=%.1fms/face",
        embedded_count, cache_hit_count, skipped_count,
        failed_count, dropped_count, avg_ms,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Standalone test — run with: python -m engine.pipeline.embedding
# ═══════════════════════════════════════════════════════════════════════════════

def _test_embedding():
    """
    Quick standalone test for the embedding stage.

    Verifies:
        1. InsightFace model loads without error
        2. Embedding has correct shape (512,) and dtype (float32)
        3. Embeddings are L2-normalized (unit length)
        4. Same input produces same embedding (deterministic inference)
        5. Different inputs produce different embeddings
        6. Inference speed benchmark

    Run:
        python -m engine.pipeline.embedding
    """
    from backend.engine.utils.logger import setup_logger

    setup_logger("embedding")
    test_logger = setup_logger("test")

    # Load model
    test_logger.info("Loading InsightFace model...")
    rec = _load_recognizer()
    test_logger.info("Model loaded successfully")

    # Create two synthetic 112×112 face images.
    # .get_feat() doesn't need a real face — it always produces an
    # embedding from any 112×112 input (no detection step).
    face_a = np.random.randint(0, 255, (112, 112, 3), dtype=np.uint8)
    face_b = np.random.randint(0, 255, (112, 112, 3), dtype=np.uint8)

    # ── Test 1: shape and dtype ──
    emb_a = extract_embedding(rec, face_a)
    test_logger.info("Test 1: shape=%s, dtype=%s", emb_a.shape, emb_a.dtype)
    assert emb_a.shape == (config.EMBEDDING_DIM,), f"Wrong shape: {emb_a.shape}"
    assert emb_a.dtype == np.float32, f"Wrong dtype: {emb_a.dtype}"

    # ── Test 2: L2 normalization ──
    norm = np.linalg.norm(emb_a)
    test_logger.info("Test 2: L2 norm = %.6f (should be ~1.0)", norm)
    assert abs(norm - 1.0) < 1e-5, f"Not normalized: {norm}"

    # ── Test 3: deterministic (same input → same output) ──
    emb_a2 = extract_embedding(rec, face_a)
    is_deterministic = np.allclose(emb_a, emb_a2, atol=1e-5)
    test_logger.info("Test 3: deterministic = %s", is_deterministic)
    assert is_deterministic, "Same input should produce same embedding"

    # ── Test 4: different inputs → different embeddings ──
    emb_b = extract_embedding(rec, face_b)
    similarity = float(np.dot(emb_a, emb_b))
    test_logger.info("Test 4: similarity(A, B) = %.4f (should be < 1.0)", similarity)
    assert similarity < 0.99, "Different faces should produce different embeddings"

    # ── Test 5: inference speed ──
    n_runs = 50
    t_start = time.perf_counter()
    for _ in range(n_runs):
        extract_embedding(rec, face_a)
    elapsed = (time.perf_counter() - t_start) * 1000
    test_logger.info(
        "Test 5: %d inferences in %.1fms (%.2fms/face)",
        n_runs, elapsed, elapsed / n_runs,
    )

    test_logger.info("All embedding tests passed!")


if __name__ == "__main__":
    _test_embedding()
