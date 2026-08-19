"""
face_db.py — Face Embedding Database (FAISS-backed)

FAISS IndexFlatIP provides exact inner-product search. Because all
embeddings are L2-normalized, inner product == cosine similarity.

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │  FaceDatabase                                               │
    │                                                             │
    │  _index: faiss.IndexFlatIP(512)   ── FAISS flat index       │
    │  _names: list[str]                ── name per row           │
    │                                                             │
    │  register(name, embedding)   → add (with duplicate cap)     │
    │  search(embedding, k=1)      → (name, score) or Unknown     │
    │  search_batch(embeddings)    → [(name, score), ...]         │
    │  save(path)                  → persist to disk               │
    │  load(path)                  → restore from disk             │
    │  list_identities()           → unique registered names       │
    └─────────────────────────────────────────────────────────────┘

Search method:
    faiss.IndexFlatIP computes exact inner product against all stored
    vectors. With L2-normalized embeddings this equals cosine similarity.

    Scaling characteristics:
        100 faces   →  ~0.01ms/query (instant)
        10,000      →  ~0.1ms/query
        1,000,000+  →  swap to IndexIVFFlat for approximate search

Persistence:
    - Index: saved via faiss.write_index() as binary (fast load)
    - Names: saved as .json (human-readable, editable)
    - Backward-compatible: can migrate old .npy databases on first load

Thread/Process safety:
    This class is used within a SINGLE process (matching_process).
    No locks needed. If you share it across processes, add a Lock.
"""

import json
import logging
import os
from typing import Dict, List, Tuple

import faiss
import numpy as np

import backend.config as config

logger = logging.getLogger("face_db")


class FaceDatabase:
    """
    FAISS-backed face embedding database with cosine similarity search.

    Usage:
        db = FaceDatabase()
        db.load()                              # Load from disk (if exists)
        db.register("Alice", embedding_vec)    # Add a face
        name, score = db.search(query_vec)     # Find closest match
        db.save()                              # Persist to disk
    """

    def __init__(self):
        self._dim = config.EMBEDDING_DIM  # 512
        self._index = faiss.IndexFlatIP(self._dim)
        self._names: List[str] = []

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def size(self) -> int:
        """Number of registered face embeddings."""
        return self._index.ntotal

    @property
    def is_empty(self) -> bool:
        return self._index.ntotal == 0

    # ── Registration ─────────────────────────────────────────────────────────

    def _count_for_identity(self, name: str) -> int:
        """Count how many embeddings are registered for a given name."""
        return sum(1 for n in self._names if n == name)

    def register(self, name: str, embedding: np.ndarray) -> int:
        """
        Register a face embedding under the given name.

        Multiple embeddings per name are supported (different angles,
        lighting), capped at config.MAX_EMBEDDINGS_PER_IDENTITY.

        Args:
            name:       Identity name (e.g., "Alice").
            embedding:  L2-normalized 512-d float32 vector.

        Returns:
            Total number of embeddings in the database after registration.
        """
        # ── Duplicate cap ──
        current_count = self._count_for_identity(name)
        if current_count >= config.MAX_EMBEDDINGS_PER_IDENTITY:
            logger.warning(
                "Identity '%s' already has %d embeddings (max=%d) — "
                "skipping registration. Use remove_identity() first to "
                "re-register.",
                name, current_count, config.MAX_EMBEDDINGS_PER_IDENTITY,
            )
            return self.size

        embedding = embedding.astype(np.float32).flatten()

        # Validate shape
        if embedding.shape[0] != self._dim:
            logger.error(
                "Cannot register '%s': embedding dim=%d, expected=%d",
                name, embedding.shape[0], self._dim,
            )
            return self.size

        # Verify normalization
        norm = np.linalg.norm(embedding)
        if abs(norm - 1.0) > 1e-3:
            logger.warning(
                "Embedding for '%s' is not normalized (norm=%.4f), "
                "normalizing...",
                name, norm,
            )
            if norm > 0:
                embedding = embedding / norm

        # ── Add to FAISS index ──
        self._index.add(embedding.reshape(1, -1))
        self._names.append(name)

        logger.info(
            "Registered '%s' (%d/%d for this identity) — "
            "database now has %d embeddings (%d identities)",
            name,
            current_count + 1,
            config.MAX_EMBEDDINGS_PER_IDENTITY,
            self.size,
            len(self.list_identities()),
        )

        return self.size

    # ── Search ───────────────────────────────────────────────────────────────

    def search(
        self,
        embedding: np.ndarray,
        k: int = None,
        threshold: float = None,
    ) -> Tuple[str, float]:
        """
        Find the best matching identity for the given embedding.

        Pulls the top-k nearest embeddings from FAISS, then aggregates
        by identity using the maximum score per identity. Because each
        identity may have multiple embeddings (different angles, lighting),
        top-k > 1 lets a side-profile query match against the side-profile
        embedding rather than getting overruled by an unrelated frontal
        embedding that happens to be the single closest neighbour.

        Args:
            embedding:  L2-normalized 512-d float32 query vector.
            k:          Number of nearest neighbours to retrieve.
                        Defaults to config.FAISS_TOP_K.
            threshold:  Minimum similarity to accept a match.
                        Defaults to config.SIMILARITY_THRESHOLD.

        Returns:
            (name, similarity_score) — best identity above threshold,
            or ("Unknown", best_raw_score) otherwise.
        """
        if k is None:
            k = config.FAISS_TOP_K
        if threshold is None:
            threshold = config.SIMILARITY_THRESHOLD

        if self.is_empty:
            return "Unknown", 0.0

        # Don't ask FAISS for more neighbours than we actually have.
        k = max(1, min(k, self.size))

        query = embedding.astype(np.float32).reshape(1, -1)
        D, I = self._index.search(query, k)

        # Aggregate the top-k by identity, keeping the highest score per
        # identity. Skip FAISS sentinel idx == -1 (returned when k > ntotal).
        best_per_identity: dict[str, float] = {}
        for score, idx in zip(D[0], I[0]):
            idx = int(idx)
            if idx < 0:
                continue
            name = self._names[idx]
            score = float(score)
            if score > best_per_identity.get(name, -1.0):
                best_per_identity[name] = score

        if not best_per_identity:
            return "Unknown", 0.0

        best_name, best_score = max(
            best_per_identity.items(), key=lambda kv: kv[1]
        )

        if best_score >= threshold:
            return best_name, best_score
        return "Unknown", best_score

    def search_batch(
        self,
        embeddings: np.ndarray,
        threshold: float = None,
    ) -> List[Tuple[str, float]]:
        """
        Search for multiple embeddings at once (single FAISS call).

        Args:
            embeddings:  (N, 512) array of L2-normalized vectors.
            threshold:   Minimum similarity.

        Returns:
            List of (name, score) tuples, one per query.
        """
        if threshold is None:
            threshold = config.SIMILARITY_THRESHOLD

        if self.is_empty:
            return [("Unknown", 0.0)] * len(embeddings)

        queries = embeddings.astype(np.float32)
        D, I = self._index.search(queries, 1)

        results = []
        for i in range(len(queries)):
            score = float(D[i][0])
            idx = int(I[i][0])
            if idx >= 0 and score >= threshold:
                results.append((self._names[idx], score))
            else:
                results.append(("Unknown", score))

        return results

    # ── Persistence ──────────────────────────────────────────────────────────

    def save(
        self,
        index_path: str = None,
        names_path: str = None,
    ):
        """
        Save the database to disk **atomically**.

        Writes to temporary files first (``*.tmp``), then renames into
        place via ``os.replace()``.  ``os.replace`` is atomic on the
        same filesystem on both Linux and Windows, so a concurrent
        reader (e.g. the AI-worker's ``_maybe_reload_database``) will
        never see a half-written index — it either gets the old file or
        the new one.

        Cross-file consistency
        ----------------------
        ``os.replace`` is per-file atomic, but two replaces in sequence
        leave a sub-millisecond window where one file is updated and the
        other is not. The AI worker's reload poll keys off the index
        file's mtime (see ``pipeline_runner._maybe_reload_database``),
        so we **commit the name map first, then the index**. By the time
        a reader notices the index mtime change, the name map already
        carries any new patient IDs; the reader can never see "new index
        with embedding for id X + old name map missing X".

        Args:
            index_path:  Path for FAISS index binary. Defaults to config.FAISS_INDEX_PATH.
            names_path:  Path for JSON name map. Defaults to config.NAME_MAP_PATH.
        """
        if index_path is None:
            index_path = config.FAISS_INDEX_PATH
        if names_path is None:
            names_path = config.NAME_MAP_PATH

        if self.is_empty:
            logger.warning("Database is empty — nothing to save")
            return

        os.makedirs(os.path.dirname(index_path) or ".", exist_ok=True)

        # Stage 1: write to temp files next to the final paths.
        tmp_index = index_path + ".tmp"
        tmp_names = names_path + ".tmp"

        try:
            faiss.write_index(self._index, tmp_index)

            with open(tmp_names, "w") as f:
                json.dump(self._names, f, indent=2)

            # Stage 2: atomic swap. ORDER MATTERS — names map first so
            # that if the AI worker's mtime poll fires between these two
            # replaces, it either still sees the old index (and skips the
            # reload entirely) or sees the new index with a name map that
            # already covers every embedding in it. Reversing this order
            # opens a tiny window where the reader loads new embeddings
            # whose patient IDs aren't yet in the name map.
            os.replace(tmp_names, names_path)
            os.replace(tmp_index, index_path)
        except Exception:
            # Clean up temp files on failure so they don't linger.
            for tmp in (tmp_index, tmp_names):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            raise

        logger.info(
            "Saved database: %d embeddings → %s, %s",
            self.size, index_path, names_path,
        )

    def load(
        self,
        index_path: str = None,
        names_path: str = None,
    ) -> bool:
        """
        Load the database from disk.

        Supports both FAISS index files and legacy .npy files.
        If a legacy .npy file is found, it is migrated to FAISS format.

        Args:
            index_path:  Path to FAISS index file.
            names_path:  Path to JSON name map.

        Returns:
            True if loaded successfully, False if files don't exist.
        """
        if index_path is None:
            index_path = config.FAISS_INDEX_PATH
        if names_path is None:
            names_path = config.NAME_MAP_PATH

        # ── Try FAISS index first ──
        if os.path.exists(index_path) and os.path.exists(names_path):
            return self._load_faiss(index_path, names_path)

        # ── Backward compat: try legacy .npy format ──
        npy_path = index_path + ".npy" if not index_path.endswith(".npy") else index_path
        if os.path.exists(npy_path) and os.path.exists(names_path):
            logger.info("Found legacy .npy database — migrating to FAISS")
            if self._load_legacy_npy(npy_path, names_path):
                # Save in new format immediately
                self.save(index_path, names_path)
                logger.info("Migration complete — saved as FAISS index")
                return True

        logger.info(
            "No saved database found at %s / %s — starting fresh",
            index_path, names_path,
        )
        return False

    def _load_faiss(self, index_path: str, names_path: str) -> bool:
        """Load from FAISS binary index + JSON names."""
        try:
            loaded_index = faiss.read_index(index_path)
        except Exception as e:
            logger.error("Failed to load FAISS index from %s: %s", index_path, e)
            return False

        with open(names_path, "r") as f:
            loaded_names = json.load(f)

        if loaded_index.ntotal != len(loaded_names):
            logger.error(
                "Database mismatch: %d embeddings vs %d names — resetting",
                loaded_index.ntotal, len(loaded_names),
            )
            return False

        if loaded_index.d != self._dim:
            logger.error(
                "Embedding dimension mismatch: file has %d, expected %d",
                loaded_index.d, self._dim,
            )
            return False

        self._index = loaded_index
        self._names = loaded_names

        logger.info(
            "Loaded FAISS database: %d embeddings, %d identities",
            self.size, len(self.list_identities()),
        )
        return True

    def _load_legacy_npy(self, npy_path: str, names_path: str) -> bool:
        """Migrate from old numpy .npy format to FAISS."""
        try:
            loaded_emb = np.load(npy_path).astype(np.float32)
            with open(names_path, "r") as f:
                loaded_names = json.load(f)
        except Exception as e:
            logger.error("Failed to load legacy .npy database: %s", e)
            return False

        if loaded_emb.shape[0] != len(loaded_names):
            logger.error(
                "Legacy database mismatch: %d embeddings vs %d names",
                loaded_emb.shape[0], len(loaded_names),
            )
            return False

        if loaded_emb.shape[1] != self._dim:
            logger.error(
                "Legacy embedding dimension mismatch: %d vs %d",
                loaded_emb.shape[1], self._dim,
            )
            return False

        self._index = faiss.IndexFlatIP(self._dim)
        self._index.add(loaded_emb)
        self._names = loaded_names

        logger.info(
            "Migrated legacy database: %d embeddings, %d identities",
            self.size, len(self.list_identities()),
        )
        return True

    # ── Utilities ────────────────────────────────────────────────────────────

    def list_identities(self) -> List[str]:
        """Return sorted list of unique registered names."""
        return sorted(set(self._names))

    def count_per_identity(self) -> Dict[str, int]:
        """Return {name: count} of embeddings per identity."""
        counts: Dict[str, int] = {}
        for name in self._names:
            counts[name] = counts.get(name, 0) + 1
        return counts

    def remove_identity(self, name: str) -> int:
        """
        Remove all embeddings for a given identity.

        Rebuilds the FAISS index without the removed entries.
        O(N) but removals are rare compared to searches.

        Args:
            name:  Identity name to remove.

        Returns:
            Number of embeddings removed.
        """
        if self.is_empty:
            return 0

        keep_indices = [i for i, n in enumerate(self._names) if n != name]
        removed = self.size - len(keep_indices)

        if removed == 0:
            logger.info("Identity '%s' not found in database", name)
            return 0

        if keep_indices:
            # Reconstruct kept embeddings and rebuild index
            kept_emb = np.vstack([
                self._index.reconstruct(i) for i in keep_indices
            ]).astype(np.float32)
            self._index.reset()
            self._index.add(kept_emb)
            self._names = [self._names[i] for i in keep_indices]
        else:
            self._index.reset()
            self._names = []

        logger.info(
            "Removed '%s' (%d embeddings) — database now has %d",
            name, removed, self.size,
        )
        return removed
