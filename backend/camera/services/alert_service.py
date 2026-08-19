"""
alert_service.py — Stateful alerting hook for tracking events.

Unknown alerts are gated on identity_confidence, sustained presence
(history window), and a per-track cooldown. EXIT alerts have their own
simple cooldown. Must stay fast: no DB, no network.
"""

import time


# EXIT cooldown.
EXIT_COOLDOWN = 5.0

# Unknown-face gating.
UNKNOWN_ALERT_THRESHOLD = 0.6       # identity_confidence floor
UNKNOWN_MIN_SAMPLES = 10            # history entries before alerting
UNKNOWN_MIN_DURATION = 1.5          # seconds spanned by those samples
UNKNOWN_COOLDOWN = 10.0             # seconds between alerts per track
UNKNOWN_HISTORY_WINDOW = 3.0        # drop samples older than this

# Cache TTL for sweep.
CACHE_TTL = 30.0


alert_cache = {
    "unknown": {},  # track_id -> last_alert_time
    "exit": {},     # patient_id -> last_alert_time
}

# track_id -> list[timestamp] of qualifying unknown sightings
unknown_history: dict = {}

_last_sweep = 0.0


def log_alert(kind: str, event: dict, **extra):
    patient = extra.get("patient") or event.get("name") or event.get("patient_id")
    camera = event.get("camera_id")
    print(f"[ALERT] type={kind} patient={patient} camera={camera}")


def _sweep(now: float):
    global _last_sweep
    if now - _last_sweep < CACHE_TTL:
        return
    _last_sweep = now

    for bucket in alert_cache.values():
        stale = [k for k, ts in bucket.items() if now - ts > CACHE_TTL]
        for k in stale:
            bucket.pop(k, None)

    stale_hist = [
        tid for tid, hist in unknown_history.items()
        if not hist or now - hist[-1] > CACHE_TTL
    ]
    for tid in stale_hist:
        unknown_history.pop(tid, None)


def handle_event(event: dict):
    try:
        now = time.time()
        _sweep(now)

        etype = event.get("type")

        if etype == "EXIT":
            patient_id = event.get("patient_id")
            if not patient_id:
                return
            last = alert_cache["exit"].get(patient_id)
            if last is None or (now - last) > EXIT_COOLDOWN:
                alert_cache["exit"][patient_id] = now
                log_alert("Patient exited", event)
            return

        if etype != "DETECTION":
            return

        for face in event.get("faces", []) or []:
            if face.get("identity") != "Unknown":
                continue

            identity_conf = float(face.get("identity_confidence") or 0.0)
            if identity_conf < UNKNOWN_ALERT_THRESHOLD:
                continue

            track_id = face.get("track_id")
            if not track_id:
                continue

            hist = unknown_history.setdefault(track_id, [])
            hist.append(now)
            # bound history: drop samples outside the rolling window
            cutoff = now - UNKNOWN_HISTORY_WINDOW
            while hist and hist[0] < cutoff:
                hist.pop(0)

            if len(hist) < UNKNOWN_MIN_SAMPLES:
                continue
            if (hist[-1] - hist[0]) < UNKNOWN_MIN_DURATION:
                continue

            last = alert_cache["unknown"].get(track_id)
            if last is not None and (now - last) < UNKNOWN_COOLDOWN:
                continue

            alert_cache["unknown"][track_id] = now
            log_alert("Unknown person detected", event, patient="Unknown")

    except Exception as exc:
        print(f"[ALERT ERROR] {exc}")
