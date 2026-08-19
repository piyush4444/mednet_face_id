"""
zoom_trigger.py — Phase 1 of multi-scale detection.

Pure decision logic that flags when the current frame *probably* needed
a second, zoomed-in detection pass. Phase 1 only emits the flag and a
log line; Phase 2 will hook the same signal to actually run a zoom
crop + re-detect.

Why a sidecar module?
    - Keeps detection.py's hot loop unchanged in flow.
    - Decision is a pure function — testable with synthetic bbox lists
      in isolation, no camera or model required.
    - Phase 2 swaps in additional logic (motion-mask intersect, ROI
      ranking, etc.) by editing one file.

Trigger predicates (Phase 1):
    1. ``no_faces``  — detector returned nothing AT ALL.
    2. ``all_small`` — every detected face is below the "small" threshold
                       (cosmetic to keep them, but suggests a far face we
                       missed entirely).

Future predicate (Phase 2/3, stubbed but not implemented here):
    3. ``motion_uncovered`` — motion mask shows N moving regions but
                              detection found < N faces. The uncovered
                              regions are exactly where to zoom.

Cooldown:
    On a fully idle camera ``no_faces`` would fire every frame. The
    ZoomCooldown gate throttles to one evaluation per N frames per
    camera so logs stay readable and Phase 2's GPU cost stays bounded.

Config knobs (in backend/config.py):
    ZOOM_TRIGGER_ENABLED            master switch
    ZOOM_SMALL_FACE_THRESHOLD_PX    bbox max-edge below this → "small"
    ZOOM_TRIGGER_COOLDOWN_FRAMES    min frames between trigger evals
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

import backend.config as config


# ─────────────────────────────────────────────────────────────────────────────
# Decision dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ZoomDecision:
    """Result of one zoom-trigger evaluation."""

    needs_zoom: bool
    reason: str                                    # "no_faces" | "all_small" | "none" | "disabled"
    max_face_size_px: int                          # 0 when no faces detected
    small_face_regions: Tuple[Tuple[int, int, int, int], ...] = field(default_factory=tuple)


_NULL_DECISION = ZoomDecision(False, "disabled", 0, ())
_NO_TRIGGER = ZoomDecision(False, "none", 0, ())


# ─────────────────────────────────────────────────────────────────────────────
# Pure decision function
# ─────────────────────────────────────────────────────────────────────────────

def evaluate(faces: List[dict]) -> ZoomDecision:
    """
    Decide whether a zoom-redetect pass is warranted for the current frame.

    Phase 1 only computes the decision; no zoom is executed. The return
    value travels with each FaceTask via ``task.metadata['needs_zoom']``
    (and friends) so Phase 2 consumers can act on it without changing
    queue formats.

    Args:
        faces: post-filter detections, each ``{'bbox': [x1,y1,x2,y2], ...}``
               in the SAME coordinate space as ``MIN_FACE_SIZE`` (i.e.
               the post-PROCESS_MAX_DIM frame).

    Returns:
        ZoomDecision describing whether and why a zoom pass should fire.
    """
    if not getattr(config, "ZOOM_TRIGGER_ENABLED", False):
        return _NULL_DECISION

    threshold = int(getattr(config, "ZOOM_SMALL_FACE_THRESHOLD_PX", 60))

    # Predicate 1: detector found nothing. Could be empty scene OR
    # everyone is too far away. Phase 2 disambiguates with motion mask.
    if not faces:
        return ZoomDecision(True, "no_faces", 0, ())

    # Predicate 2: faces exist but they're all small. Strong hint that
    # there's a closer/larger face the detector missed at this scale.
    sizes: List[int] = []
    small_regions: List[Tuple[int, int, int, int]] = []
    for f in faces:
        bbox = f.get("bbox")
        if not bbox or len(bbox) < 4:
            continue
        x1, y1, x2, y2 = bbox[0], bbox[1], bbox[2], bbox[3]
        size = max(x2 - x1, y2 - y1)
        sizes.append(size)
        if size < threshold:
            small_regions.append((int(x1), int(y1), int(x2), int(y2)))

    if not sizes:
        # Defensive: malformed detections — treat as no faces.
        return ZoomDecision(True, "no_faces", 0, ())

    max_size = max(sizes)
    if max_size < threshold:
        return ZoomDecision(True, "all_small", max_size, tuple(small_regions))

    return _NO_TRIGGER


# ─────────────────────────────────────────────────────────────────────────────
# Per-camera cooldown gate
# ─────────────────────────────────────────────────────────────────────────────

class ZoomCooldown:
    """
    Per-camera throttle so we don't fire (and log) the trigger on
    every single frame of an idle hallway.

    Not thread-safe — each detection_process owns its own instance,
    so there's no cross-process contention.
    """

    __slots__ = ("_interval", "_last_fired_frame")

    def __init__(self, interval_frames: int):
        # Guard: an interval < 1 would fire every frame. Treat <=0 as 1.
        self._interval = max(1, int(interval_frames))
        # Sentinel: very negative so the first frame always admits.
        self._last_fired_frame: int = -10 ** 9

    def admit(self, frame_id: int) -> bool:
        """
        Returns True if the trigger may fire on this frame_id, in which
        case the gate's internal timer is bumped. Returns False otherwise.
        """
        if frame_id - self._last_fired_frame >= self._interval:
            self._last_fired_frame = frame_id
            return True
        return False

    def reset(self) -> None:
        """Re-arm immediately — useful in tests or on camera restart."""
        self._last_fired_frame = -10 ** 9
