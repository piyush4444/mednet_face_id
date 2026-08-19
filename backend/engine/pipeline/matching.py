"""
matching.py — Stage 5: Face Matching (Database Lookup)

Responsibilities:
    - Receive FaceTask objects with 512-d embeddings from the embedding stage
    - Query the face database to find the closest registered identity
    - If cosine similarity ≥ threshold → assign identity name + confidence
    - If below threshold → mark as "Unknown"
    - Push completed FaceTask downstream to aggregation

Data flow:
    ┌─────────────────┐          ┌──────────────────────────────────┐
    │ Embedding Queue  │────────▶│ Matching Process                  │
    │ FaceTask         │          │                                   │
    │ (with embedding) │          │  1. Guard: no embedding → Unknown │
    └─────────────────┘          │  2. db.search(embedding)          │
                                  │  3. score ≥ threshold → identity  │
                                  │  4. score < threshold → Unknown   │
                                  │  5. put_nowait → output queue     │
                                  └──────────────────────────────────┘
                                                │
                                                ▼
                                  ┌─────────────────┐
                                  │ Aggregation Queue│
                                  │ FaceTask         │
                                  │ (identity set)   │
                                  └─────────────────┘

Performance:
    This is the FASTEST stage — a single matrix multiply.
    For 100 registered faces: ~0.01ms per query.
    For 10,000 faces: ~0.1ms per query.
    Bottleneck is NOT here. No GPU needed.

    When scaling to 100k+ faces, swap FaceDatabase with FAISS:
        # In _load_database():
        import faiss
        index = faiss.IndexFlatIP(config.EMBEDDING_DIM)
        # FaceDatabase.search() → index.search()
"""

import logging
import time
from multiprocessing import Queue, Event
from queue import Empty, Full

import numpy as np

from backend.engine.models.face_task import FaceTask
from backend.engine.database.face_db import FaceDatabase
import backend.config as config

logger = logging.getLogger("matching")

# ── Periodic stats interval ──
_STATS_LOG_INTERVAL = 200


# ═══════════════════════════════════════════════════════════════════════════════
# Identity Smoother — majority vote over sliding window
# ═══════════════════════════════════════════════════════════════════════════════

class IdentitySmoother:
    """
    Temporal smoothing for identity labels via majority voting.

    Problem:
        Frame-by-frame matching produces flickering:
          Piyush → Unknown → Piyush → Unknown → Piyush

        This happens because:
        - Slight head rotation changes the embedding
        - Some frames miss the similarity threshold by a tiny margin
        - Detector jitter shifts the crop, changing the embedding slightly

    Solution:
        Use the persistent track_id assigned by the tracking stage.
        For each track_id, keep a sliding window of the last N identity
        results and return the majority vote.

        Window size 5 at 30 FPS = ~170ms of history. A face is labeled X
        only if X appears ≥3 out of the last 5 frames → stable output.
    """

    def __init__(self, window_size: int = 5, max_age: int = 10):
        """
        Args:
            window_size:  Number of recent results to keep per track.
            max_age:      Remove history for tracks unseen for this many frames.
        """
        from collections import deque

        self.window_size = window_size
        self.max_age = max_age

        # track_id → { history: deque, last_seen: frame_id }
        self._tracks: dict = {}

    def smooth(self, task: FaceTask) -> tuple:
        """
        Apply temporal smoothing to a FaceTask's identity.

        Args:
            task:  FaceTask with .track_id, .identity, .confidence set.

        Returns:
            (smoothed_identity, smoothed_confidence)
        """
        from collections import Counter, deque

        tid = task.track_id

        # No track_id → no smoothing possible, return raw result
        if tid is None:
            return task.identity, task.confidence

        # Get or create history for this track
        if tid not in self._tracks:
            self._tracks[tid] = {
                "history": deque(maxlen=self.window_size),
                "last_seen": task.frame_id,
            }

        entry = self._tracks[tid]
        entry["history"].append((task.identity, task.confidence))
        entry["last_seen"] = task.frame_id

        # Majority vote
        history = entry["history"]
        counts = Counter(identity for identity, _ in history)
        winner, _ = counts.most_common(1)[0]

        # Average confidence for the winning identity
        winner_scores = [conf for ident, conf in history if ident == winner]
        avg_conf = sum(winner_scores) / len(winner_scores)

        return winner, avg_conf

    def prune(self, current_frame_id: int):
        """Remove history for tracks not seen recently."""
        stale = [
            tid for tid, entry in self._tracks.items()
            if (current_frame_id - entry["last_seen"]) > self.max_age
        ]
        for tid in stale:
            del self._tracks[tid]

    @property
    def active_count(self) -> int:
        return len(self._tracks)


# ═══════════════════════════════════════════════════════════════════════════════
# Database loading
# ═══════════════════════════════════════════════════════════════════════════════

def _load_database() -> FaceDatabase:
    """
    Load the face database from disk at process start.

    Returns:
        FaceDatabase instance (may be empty if no saved data exists).
    """
    db = FaceDatabase()
    loaded = db.load()

    if loaded:
        logger.info(
            "Database loaded: %d embeddings, %d identities: %s",
            db.size,
            len(db.list_identities()),
            ", ".join(db.list_identities()),
        )
    else:
        logger.info("No saved database — starting with empty database")

    return db


# ═══════════════════════════════════════════════════════════════════════════════
# Main process loop
# ═══════════════════════════════════════════════════════════════════════════════

def matching_process(
    camera_id: str,
    input_queue: Queue,
    output_queue: Queue,
    shutdown_event: Event,
):
    """
    Main loop for the face matching stage — ONE instance per camera.

    Runs as an independent multiprocessing.Process. For each FaceTask:
      1. Skip matching if embedding is None (mark as Unknown)
      2. Query the face database for the closest match
      3. Apply similarity threshold
      4. Set task.identity and task.confidence
      5. Push downstream (non-blocking, drop if full)

    Args:
        camera_id:       Identifier for the source camera (e.g. "cam_entry").
                         Used to prefix every log message with "[camera_id]"
                         and to keep the IdentitySmoother's per-track state
                         isolated from other cameras (each camera runs its
                         own matching_process → its own smoother → its own
                         track_id → history table).
        input_queue:     Receives FaceTask objects (with embeddings) from embedding.
        output_queue:    Sends FaceTask objects (with identity) to aggregation.
                         Non-blocking — drops tasks if full.
        shutdown_event:  multiprocessing.Event — set to stop this process.
    """
    from backend.engine.utils.logger import setup_logger, camera_logger
    setup_logger("matching")
    setup_logger("face_db")
    # Shadow module-level `logger` so every log line below is prefixed
    # with [camera_id] without changing the individual call sites.
    logger = camera_logger("matching", camera_id)

    # ── Load database at process start ──
    database = _load_database()

    # ── Identity smoother (majority vote over last N frames per track) ──
    smoother = IdentitySmoother(
        window_size=config.SMOOTHING_WINDOW,
        max_age=config.TRACK_MAX_AGE,
    )
    last_frame_id = -1

    matched_count = 0
    unknown_count = 0
    no_embedding_count = 0
    dropped_count = 0
    smoothed_overrides = 0
    total_search_ms = 0.0

    logger.info(
        "Matching process started (threshold=%.2f, db_size=%d, smoothing=%d)",
        config.SIMILARITY_THRESHOLD, database.size, config.SMOOTHING_WINDOW,
    )

    while not shutdown_event.is_set():

        # ── Read next FaceTask ──
        try:
            task: FaceTask = input_queue.get(timeout=0.01)
        except Empty:
            continue

        # ── Guard: no embedding ──
        if task.embedding is None:
            task.identity = "Unknown"
            task.confidence = 0.0
            no_embedding_count += 1
            task.metadata["match_skipped"] = True
            task.metadata["skip_reason"] = (
                task.metadata.get("skip_reason", "no_embedding")
            )
            logger.debug(
                "No embedding for frame=%d face=%d — marking Unknown",
                task.frame_id, task.face_id,
            )
            try:
                output_queue.put_nowait(task)
            except Full:
                dropped_count += 1
            continue

        # ── Guard: empty database ──
        if database.is_empty:
            task.identity = "Unknown"
            task.confidence = 0.0
            unknown_count += 1
            try:
                output_queue.put_nowait(task)
            except Full:
                dropped_count += 1
            continue

        # ── Search database ──
        t_start = time.perf_counter()
        task.identity, task.confidence = database.search(
            task.embedding,
            k=config.FAISS_TOP_K,
        )
        search_ms = (time.perf_counter() - t_start) * 1000
        total_search_ms += search_ms

        if task.is_identified:
            matched_count += 1
        else:
            unknown_count += 1

        # ── Identity smoothing (majority vote per track_id) ──
        # Prune stale tracks when we move to a new frame.
        if task.frame_id != last_frame_id:
            smoother.prune(task.frame_id)
            last_frame_id = task.frame_id

        raw_identity = task.identity
        task.identity, task.confidence = smoother.smooth(task)

        if task.identity != raw_identity:
            smoothed_overrides += 1
            logger.debug(
                "Smoothed: frame=%d face=%d raw='%s' → '%s' (%.3f)",
                task.frame_id, task.face_id,
                raw_identity, task.identity, task.confidence,
            )

        task.metadata["raw_identity"] = raw_identity
        task.metadata["smoothed"] = (task.identity != raw_identity)

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
        total_processed = matched_count + unknown_count
        if total_processed > 0 and total_processed % _STATS_LOG_INTERVAL == 0:
            avg_ms = total_search_ms / total_processed
            logger.info(
                "Matching stats: matched=%d, unknown=%d, no_emb=%d, "
                "dropped=%d, smoothed=%d, avg=%.3fms/query, db_size=%d",
                matched_count, unknown_count, no_embedding_count,
                dropped_count, smoothed_overrides, avg_ms, database.size,
            )

    # ── Final stats ──
    total_processed = matched_count + unknown_count
    avg_ms = (total_search_ms / total_processed) if total_processed > 0 else 0
    logger.info(
        "Matching shut down — matched=%d, unknown=%d, no_emb=%d, "
        "dropped=%d, smoothed=%d, avg=%.3fms/query",
        matched_count, unknown_count, no_embedding_count,
        dropped_count, smoothed_overrides, avg_ms,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Standalone test — run with: python -m engine.pipeline.matching
# ═══════════════════════════════════════════════════════════════════════════════

def _test_matching():
    """
    Quick standalone test for the matching stage.

    Tests:
        1. Empty database → all Unknown
        2. Register a face → search matches correctly
        3. Different face → Unknown (below threshold)
        4. Save and reload database
        5. Search speed benchmark

    Run:
        python -m engine.pipeline.matching
    """
    from backend.engine.utils.logger import setup_logger

    setup_logger("matching")
    setup_logger("face_db")
    test_logger = setup_logger("test")

    db = FaceDatabase()

    # ── Test 1: empty database ──
    test_logger.info("Test 1: Empty database search")
    query = np.random.randn(config.EMBEDDING_DIM).astype(np.float32)
    query /= np.linalg.norm(query)
    name, score = db.search(query)
    test_logger.info("  Result: '%s' (%.4f)", name, score)
    assert name == "Unknown", f"Expected Unknown, got {name}"

    # ── Test 2: register and match ──
    test_logger.info("Test 2: Register and match")
    alice_emb = np.random.randn(config.EMBEDDING_DIM).astype(np.float32)
    alice_emb /= np.linalg.norm(alice_emb)
    db.register("Alice", alice_emb)

    name, score = db.search(alice_emb)
    test_logger.info("  Alice self-match: '%s' (%.4f)", name, score)
    assert name == "Alice", f"Expected Alice, got {name}"
    assert score > 0.99, f"Self-match should be ~1.0, got {score}"

    # ── Test 3: different face → Unknown ──
    test_logger.info("Test 3: Different face (should be Unknown)")
    stranger = np.random.randn(config.EMBEDDING_DIM).astype(np.float32)
    stranger /= np.linalg.norm(stranger)
    name, score = db.search(stranger)
    test_logger.info("  Stranger: '%s' (%.4f)", name, score)
    # Random 512-d vectors have near-zero cosine similarity
    assert score < config.SIMILARITY_THRESHOLD, \
        f"Random vector should be below threshold, got {score}"

    # ── Test 4: multiple identities ──
    test_logger.info("Test 4: Multiple identities")
    bob_emb = np.random.randn(config.EMBEDDING_DIM).astype(np.float32)
    bob_emb /= np.linalg.norm(bob_emb)
    db.register("Bob", bob_emb)

    name, score = db.search(bob_emb)
    test_logger.info("  Bob self-match: '%s' (%.4f)", name, score)
    assert name == "Bob", f"Expected Bob, got {name}"

    test_logger.info("  Identities: %s", db.list_identities())
    test_logger.info("  Counts: %s", db.count_per_identity())

    # ── Test 5: save and reload ──
    test_logger.info("Test 5: Save and reload")
    import tempfile, os
    tmp_dir = tempfile.mkdtemp()
    emb_path = os.path.join(tmp_dir, "test_embeddings")
    names_path = os.path.join(tmp_dir, "test_names.json")

    db.save(emb_path, names_path)

    db2 = FaceDatabase()
    db2.load(emb_path, names_path)
    test_logger.info("  Reloaded: %d embeddings", db2.size)
    assert db2.size == db.size, f"Size mismatch: {db2.size} vs {db.size}"

    name, score = db2.search(alice_emb)
    assert name == "Alice", f"After reload, expected Alice, got {name}"
    test_logger.info("  Alice still matches after reload: '%s' (%.4f)", name, score)

    # ── Test 6: speed benchmark ──
    test_logger.info("Test 6: Speed benchmark")
    # Register 100 fake identities
    for i in range(100):
        e = np.random.randn(config.EMBEDDING_DIM).astype(np.float32)
        e /= np.linalg.norm(e)
        db.register(f"Person_{i}", e)

    n_queries = 1000
    t_start = time.perf_counter()
    for _ in range(n_queries):
        q = np.random.randn(config.EMBEDDING_DIM).astype(np.float32)
        q /= np.linalg.norm(q)
        db.search(q)
    elapsed = (time.perf_counter() - t_start) * 1000
    test_logger.info(
        "  %d queries against %d embeddings in %.1fms (%.3fms/query)",
        n_queries, db.size, elapsed, elapsed / n_queries,
    )

    # Cleanup
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)

    test_logger.info("All matching tests passed!")


if __name__ == "__main__":
    _test_matching()
