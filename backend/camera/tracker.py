"""
tracker.py — lightweight IoU tracker for stabilizing face identity across
frames on a per-camera basis.

Not a full MOT system: no Kalman filter, no re-identification. Just
enough to smooth out per-frame identity flicker ("Unknown" flashes,
momentary swaps) by binding detections to persistent track IDs and
preferring the first confident identity we saw for that track.
"""

import time

from backend.config import ENABLE_LOGGING


IOU_THRESHOLD = 0.4
EXPIRE_SECONDS = 2.0
STICKY_CONF_FLOOR = 0.3  # identities held above this are sticky


def _iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    iw = max(0, ix2 - ix1)
    ih = max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


class Track:
    __slots__ = (
        "track_id", "bbox", "identity", "user_id", "user_type",
        "identity_conf", "last_seen",
    )

    def __init__(self, track_id, bbox, identity, user_id, identity_conf, user_type=None):
        self.track_id = track_id
        self.bbox = bbox
        self.identity = identity
        self.user_id = user_id
        self.user_type = user_type
        self.identity_conf = identity_conf
        self.last_seen = time.time()


class FaceTracker:
    """Per-camera tracker. Keep one instance per camera_id."""

    def __init__(self, camera_id: str = "?"):
        self.camera_id = camera_id
        self._tracks: dict[int, Track] = {}
        self._next_id = 1

    def _expire(self, now):
        stale = [
            tid for tid, t in self._tracks.items()
            if now - t.last_seen > EXPIRE_SECONDS
        ]
        for tid in stale:
            del self._tracks[tid]

    def expire(self, now: float | None = None) -> None:
        """Public time-based tick. Retire any tracks older than
        EXPIRE_SECONDS without binding new detections.

        Needed because the tracker now lives in EventProcessor and only
        sees events when AI workers report non-empty faces. During quiet
        periods (no one in frame) update() never runs, so without a
        periodic expire(), stale tracks would linger past their TTL and
        an IoU match against a new face could inherit a stale identity.
        """
        self._expire(now if now is not None else time.time())

    def update(self, detections):
        """
        detections: list of dicts with keys {bbox, identity, user_id,
            identity_confidence, confidence}.

        Returns: same list with each dict gaining `track_id` and with
        `identity` / `user_id` / `identity_confidence` smoothed against
        the track's sticky identity.
        """
        now = time.time()
        self._expire(now)

        unmatched_track_ids = set(self._tracks.keys())
        results = []

        for det in detections:
            bbox = det["bbox"]

            best_tid = None
            best_iou = 0.0
            for tid in unmatched_track_ids:
                iou = _iou(bbox, self._tracks[tid].bbox)
                if iou > best_iou:
                    best_iou = iou
                    best_tid = tid

            new_identity = det.get("identity", "Unknown")
            new_user_id = det.get("user_id")
            new_user_type = det.get("user_type")
            new_conf = float(det.get("identity_confidence", 0.0))

            if best_tid is not None and best_iou >= IOU_THRESHOLD:
                track = self._tracks[best_tid]
                unmatched_track_ids.discard(best_tid)

                sticky = (
                    track.identity
                    and track.identity != "Unknown"
                    and track.identity_conf >= STICKY_CONF_FLOOR
                )

                if sticky:
                    # Keep existing identity unless a stronger known ID arrives.
                    if (
                        new_identity
                        and new_identity != "Unknown"
                        and new_conf > track.identity_conf
                    ):
                        track.identity = new_identity
                        track.user_id = new_user_id
                        track.user_type = new_user_type
                        track.identity_conf = new_conf
                else:
                    if new_identity and new_identity != "Unknown":
                        track.identity = new_identity
                        track.user_id = new_user_id
                        track.user_type = new_user_type
                        track.identity_conf = new_conf

                track.bbox = bbox
                track.last_seen = now
            else:
                track = Track(
                    track_id=self._next_id,
                    bbox=bbox,
                    identity=new_identity,
                    user_id=new_user_id,
                    identity_conf=new_conf,
                    user_type=new_user_type,
                )
                self._next_id += 1
                self._tracks[track.track_id] = track

            face = {
                **det,
                "track_id": track.track_id,
                "identity": track.identity,
                "user_id": track.user_id,
                "user_type": track.user_type,
                "identity_confidence": track.identity_conf,
            }

            # Per-frame trace — only when explicit debug logging is on,
            # otherwise this fires once per detection and drowns real
            # warnings. `live` is this frame's FAISS match score, `sticky`
            # is the smoothed/locked-in score the track is carrying.
            if ENABLE_LOGGING:
                print(
                    f"[TRK] cam={self.camera_id} "
                    f"tid={face['track_id']} "
                    f"user_id={face.get('user_id')} "
                    f"name={face.get('identity')} "
                    f"live={new_conf:.2f} "
                    f"sticky={float(track.identity_conf or 0.0):.2f}"
                )

            results.append(face)

        return results


# NOTE: the old module-level `_trackers` dict and `get_tracker()` helper
# were removed. They lived in the AI worker process and fragmented across
# workers. Trackers are now instantiated and owned by EventProcessor (a
# single process observing all cameras), so state stays coherent
# regardless of NUM_AI_WORKERS.
