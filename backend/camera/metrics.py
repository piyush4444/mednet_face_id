"""
metrics.py — Cross-process pipeline metrics, backed by a Manager() dict.

Design
------
Each worker process owns a logical *namespace* of counters and gauges.
Writes are local-only (a small per-worker dict) and we flush a snapshot
into the shared ``Manager().dict()`` at a slow cadence (~once/sec). That
keeps the hot loop allocation-free while the API/metrics endpoint reads
a near-live view without any locking.

Exposed values (see ``snapshot()`` for the full shape):

    camera.<cam_id>.grabber_fps         float   frames/sec from grabber
    camera.<cam_id>.worker_fps          float   frames/sec enqueued to AI
    camera.<cam_id>.motion_skip_rate    float   0..1, fraction of ticks skipped by motion gate
    camera.<cam_id>.frame_queue_drops   int     frames dropped on full queue
    camera.<cam_id>.stream_queue_drops  int     JPEGs dropped on full stream queue
    ai.inference_ms_p50                 float   median run_pipeline() time (excluding skipped)
    ai.inference_ms_p95                 float   95th percentile
    ai.detections_per_sec               float
    queue.frame_queue.depth             int     current size (sampled)
    queue.event_queue.depth             int
    queue.ws_queue.depth                int
"""
from __future__ import annotations

import collections
import threading
import time
from typing import Optional


# ── Shared state holder ──────────────────────────────────────────────────
# Wired at system start by ``backend/camera/manager.py::start_camera_system``.
# Before that, writes silently no-op so unit tests / standalone runs don't
# crash on missing IPC.
_shared: Optional[dict] = None


def bind(shared_dict) -> None:
    """Attach the Manager().dict() that backs cross-process metrics."""
    global _shared
    _shared = shared_dict


def get_shared():
    return _shared


def snapshot() -> dict:
    """Copy the current metrics view — safe to serialise for HTTP."""
    if _shared is None:
        return {}
    # Manager dict proxies are slow for iteration; copy once.
    return dict(_shared)


# ── Per-process collector ────────────────────────────────────────────────
# Each worker process creates one of these. Writes go into local fields
# and are flushed to the shared dict on a background timer.

_FLUSH_INTERVAL_S = 1.0
_P95_WINDOW = 200  # samples kept for percentile estimates


class Collector:
    """Lightweight per-process metrics sink."""

    def __init__(self, namespace: str):
        self.namespace = namespace.rstrip(".") + "."
        self._lock = threading.Lock()
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._samples: dict[str, collections.deque] = {}
        self._rate_state: dict[str, tuple[float, float]] = {}  # key -> (last_count, last_ts)

        self._flusher = threading.Thread(target=self._flush_loop, daemon=True)
        self._flusher.start()

    # ── recording ────────────────────────────────────────────────────
    def incr(self, key: str, n: float = 1.0) -> None:
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + n

    def gauge(self, key: str, value: float) -> None:
        with self._lock:
            self._gauges[key] = float(value)

    def observe(self, key: str, value: float) -> None:
        """Record a sample for percentile computation."""
        with self._lock:
            dq = self._samples.get(key)
            if dq is None:
                dq = collections.deque(maxlen=_P95_WINDOW)
                self._samples[key] = dq
            dq.append(float(value))

    # ── derived metrics ──────────────────────────────────────────────
    def rate_per_sec(self, key: str) -> float:
        """Return the per-second rate of a counter since the last call."""
        now = time.monotonic()
        with self._lock:
            curr = self._counters.get(key, 0.0)
            last_count, last_ts = self._rate_state.get(key, (curr, now))
            self._rate_state[key] = (curr, now)
        dt = max(1e-6, now - last_ts)
        return (curr - last_count) / dt

    def percentile(self, key: str, q: float) -> float:
        with self._lock:
            dq = self._samples.get(key)
            if not dq:
                return 0.0
            data = sorted(dq)
        if not data:
            return 0.0
        idx = int(q * (len(data) - 1))
        return float(data[idx])

    # ── flushing to shared dict ──────────────────────────────────────
    def _flush_loop(self):
        while True:
            time.sleep(_FLUSH_INTERVAL_S)
            if _shared is None:
                continue
            try:
                self._flush_once()
            except Exception:
                pass  # Metrics must never kill the pipeline.

    def _flush_once(self):
        with self._lock:
            counters_copy = dict(self._counters)
            gauges_copy = dict(self._gauges)
            sample_keys = list(self._samples.keys())

        # Publish raw counters + gauges.
        for k, v in counters_copy.items():
            _shared[self.namespace + k] = v
        for k, v in gauges_copy.items():
            _shared[self.namespace + k] = v

        # Publish per-second rates for anything that looks like a count.
        # We expose `<key>_per_sec` so consumers don't need state.
        for k in counters_copy:
            _shared[self.namespace + k + "_per_sec"] = self.rate_per_sec(k)

        # Publish p50/p95 for timing samples.
        for k in sample_keys:
            _shared[self.namespace + k + "_p50"] = self.percentile(k, 0.5)
            _shared[self.namespace + k + "_p95"] = self.percentile(k, 0.95)
