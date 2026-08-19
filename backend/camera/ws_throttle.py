"""
ws_throttle.py — Emit-on-change-or-deadline throttling for WebSocket events.

Why
---
Every AI inference tick produced a DETECTION event per camera, and every
tracked event produced a full PRESENCE snapshot. At 4 cameras × 6 FPS
that is up to ~24 DETECTION/s + ~24 PRESENCE/s hitting the WS bridge
and the React dashboard — enough to pin the browser's main thread on
re-renders while showing the user no new information.

The human eye doesn't need 24 Hz bounding-box updates on a dashboard,
but it DOES need to see identity changes immediately (a new person
entering, a face flipping from Unknown → a known name).

Algorithm
---------
Per-key state: last emit timestamp + last payload signature.

Emit when:
  * the signature is different from last emit (change-driven), OR
  * at least `min_interval` seconds have elapsed since the last emit
    (heartbeat — keeps the UI alive for frame counters, conf drift, etc.)

Otherwise drop. The signature is a cheap hashable projection of the
payload — e.g. frozenset of user IDs present in a frame — so unchanged
state skips the WS queue entirely.

Thread-safety
-------------
Each instance is owned by a single producer process (AI worker or event
processor), both of which are single-threaded on the hot path. No lock.
"""

from __future__ import annotations

import time
from typing import Any, Hashable


class WSThrottle:
    """Per-key change-or-heartbeat throttle.

    Instantiate once per broadcaster; call ``should_emit(key, signature)``
    before putting the payload on the WS queue. Returns True when the
    caller should emit.
    """

    __slots__ = ("min_interval", "_last_emit", "_last_sig")

    def __init__(self, min_interval: float = 0.5):
        self.min_interval = float(min_interval)
        self._last_emit: dict[str, float] = {}
        self._last_sig: dict[str, Any] = {}

    def should_emit(self, key: str, signature: Hashable) -> bool:
        now = time.monotonic()
        last_ts = self._last_emit.get(key, 0.0)
        last_sig = self._last_sig.get(key, _SENTINEL)

        if signature != last_sig or (now - last_ts) >= self.min_interval:
            self._last_emit[key] = now
            self._last_sig[key] = signature
            return True
        return False

    def reset(self, key: str | None = None) -> None:
        """Forget throttle state for one key (or all if None). Useful on
        camera reconnect so the next payload always flushes."""
        if key is None:
            self._last_emit.clear()
            self._last_sig.clear()
        else:
            self._last_emit.pop(key, None)
            self._last_sig.pop(key, None)


# Distinct sentinel so the very first call always emits (None could be a
# legitimate signature value for "empty set of identities").
_SENTINEL = object()
