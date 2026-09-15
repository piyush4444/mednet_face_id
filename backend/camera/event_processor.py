import time
from contextlib import contextmanager
from multiprocessing import Process

from backend.app.db.postgres import SessionLocal
from backend.app.utils.time_ist import fmt_ist
from backend.config import ENABLE_LOGGING
from backend.camera.services import alert_service, tracking_service
from backend.camera.tracker import FaceTracker
from backend.camera.utils.camera_roles import (
    bind_role_map,
    get_camera_role,
    has_exit_camera,
)
from backend.camera.ws_throttle import WSThrottle


EXIT_TIMEOUT = 3.0  # seconds without a detection → EXIT (per-camera, legacy)
UPDATE_INTERVAL = 1.0  # min seconds between UPDATE broadcasts per user
ATTENDANCE_CHECK_INTERVAL = 5.0  # DB decision checks; local logs throttle further
GLOBAL_TIMEOUT = 10  # seconds (you can tune later)

# If strict mode (has_exit_camera()) prevents an exit because the user was
# last seen on an "entry" camera, forcefully exit them anyway if they have
# been unseen for this multiplier * GLOBAL_TIMEOUT. Prevents ghost users.
HARD_TIMEOUT_MULTIPLIER = 5

# PRESENCE snapshots go on every event in the old code (~24 Hz). Throttle
# to 2 Hz unless the set of INSIDE users actually changes — entry/exit
# are already broadcast as distinct ENTRY/EXIT events, so PRESENCE is
# only useful as a "who's still here" heartbeat.
PRESENCE_MIN_INTERVAL_S = 0.5
PRESENCE_THROTTLE_KEY = "__presence__"

# DETECTION feeds both the WS dashboard AND the MJPEG overlay cache
# (via detection_cache, STALE_AFTER=0.7s). Keep the heartbeat below
# the cache staleness so overlays don't blink out between emits.
DETECTION_MIN_INTERVAL_S = 0.5

# Strict-mode gate: only honor role=="exit" if at least one exit camera
# is actually configured. Otherwise fall back to "any global timeout wins".
# Resolved live via camera_roles.has_exit_camera() — role edits through
# the admin API take effect without a restart.

GLOBAL_PRESENCE = {}

class EventProcessor(Process):
    def __init__(self, event_queue, ws_queue=None, camera_roles=None):
        super().__init__()
        self.event_queue = event_queue
        self.ws_queue = ws_queue
        self.camera_roles = camera_roles
        # { camera_id: { user_id: {"last_seen": ts, "name": str, "floor": str} } }
        self.camera_state: dict = {}
        # { camera_id: { user_id: last_update_sent_ts } }
        self.last_update_sent: dict = {}
        # {(camera_id, user_id): monotonic epoch}; prevents a DB query per
        # video frame while still evaluating punch-window boundaries quickly.
        self.last_attendance_checked: dict = {}
        # { user_id } — users with an open session; gates re-entry
        self.active_sessions: set = set()
        # One-cycle immunity for sessions restored from DB — prevents an
        # exit-storm right after restart if many rows are still INSIDE.
        self.recovered_users: set = set()
        # Change-or-heartbeat throttle for PRESENCE snapshots.
        self._presence_throttle = WSThrottle(
            min_interval=PRESENCE_MIN_INTERVAL_S,
        )
        # Per-camera DETECTION throttle (feeds dashboard + MJPEG overlays).
        self._detection_throttle = WSThrottle(
            min_interval=DETECTION_MIN_INTERVAL_S,
        )
        # Per-camera identity trackers, owned here so state is coherent
        # across all cameras regardless of how many AI workers produced
        # the detections. Lazy-init on first sighting of a camera_id.
        self._trackers: dict[str, FaceTracker] = {}

    @contextmanager
    def _get_db(self):
        """Yield a fresh DB session that is closed after use.

        Each entry/update/exit gets its own short-lived session so that:
        - A Postgres restart doesn't leave us with a dead connection.
        - Unhandled exceptions can't leak open transactions.
        - Connection resources are returned promptly to the pool.
        """
        session = SessionLocal()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _is_user_inside(self, user_id) -> bool:
        return user_id in self.active_sessions

    def _load_active_sessions(self):
        """Seed active_sessions from Postgres so a restart doesn't lose
        track of users who were INSIDE when the process died.

        Staged recovery:
          1. Seed GLOBAL_PRESENCE with the *real* last-seen timestamp from
             the DB so genuinely-stale users exit promptly instead of
             getting a full GLOBAL_TIMEOUT grace period.
          2. Mark each recovered user with one-cycle immunity via
             `recovered_users` — the next sweep skips them once, giving
             cameras one tick to re-detect before we decide they're gone.
        """
        from backend.app.db.models import PatientSession

        try:
            # Materialize every field we need *inside* the session scope.
            # Touching s.patient.name after the `with` block closes the
            # session raises DetachedInstanceError on lazy load.
            recovered: list[tuple] = []
            with self._get_db() as db:
                rows = (
                    db.query(PatientSession)
                    .filter(PatientSession.status == "INSIDE")
                    .all()
                )
                for s in rows:
                    ts_source = s.last_seen or s.entry_time
                    last_seen = ts_source.timestamp() if ts_source else time.time()
                    name = s.patient.name if s.patient else str(s.patient_id)
                    recovered.append((s.patient_id, last_seen, name, s.current_floor))

            for patient_id, last_seen, name, floor in recovered:
                self.active_sessions.add(patient_id)
                self.recovered_users.add(patient_id)
                GLOBAL_PRESENCE[patient_id] = {
                    "last_seen": last_seen,
                    "cameras": set(),
                    "name": name,
                    "floor": floor,
                }
            print(
                f"[RECOVERY] restored {len(recovered)} active session(s): "
                f"{sorted(self.active_sessions)} "
                f"(one-cycle exit immunity applied)"
            )
        except Exception as exc:
            print(f"[RECOVERY ERROR] {exc}")


    def _broadcast(self, payload):
        if self.ws_queue is None:
            return
        try:
            self.ws_queue.put_nowait(payload)
        except Exception:
            pass

    def run(self):
        bind_role_map(self.camera_roles)
        print("[EVENT] Processor started")
        self._load_active_sessions()

        try:
            while True:
                self._sweep_exits()
                self._tick_trackers()

                if self.event_queue.empty():
                    time.sleep(0.05)
                    continue

                event = self.event_queue.get()
                self.handle_event(event)
        except KeyboardInterrupt:
            print("[EVENT] Processor stopped")

    def _tick_trackers(self):
        """Time-based expire for every tracker. AI workers only push
        events on non-empty face frames, so without this tick, stale
        tracks would persist past EXPIRE_SECONDS during quiet periods
        and a new face arriving at a similar bbox could inherit a stale
        identity via IoU match."""
        now = time.time()
        for tracker in self._trackers.values():
            try:
                tracker.expire(now)
            except Exception as exc:
                print(f"[TRACKER TICK ERROR] {exc}")

    def _sweep_exits(self):
        now = time.time()

        to_remove = []

        for user_id, data in GLOBAL_PRESENCE.items():
            last_seen = data["last_seen"]

            if now - last_seen > GLOBAL_TIMEOUT:
                # One-cycle immunity for DB-recovered users — give cameras
                # a full sweep tick to re-detect before declaring exit.
                if user_id in self.recovered_users:
                    self.recovered_users.discard(user_id)
                    print(
                        f"[RECOVERY SKIP] user={user_id} — immunity "
                        f"consumed, eligible for exit next cycle"
                    )
                    continue

                last_camera = data.get("last_camera")
                role = get_camera_role(last_camera) if last_camera else "inside"
                
                is_hard_timeout = (now - last_seen) > (GLOBAL_TIMEOUT * HARD_TIMEOUT_MULTIPLIER)

                if has_exit_camera():
                    # Strict mode — only exit if last camera is role=="exit".
                    should_exit = role == "exit" or is_hard_timeout
                else:
                    # Fallback — no exit camera configured, any timeout wins.
                    should_exit = True

                # Log once per "stuck" episode (keeps logs clean when a user
                # lingers past timeout on a non-exit camera).
                if not data.get("_exit_check_logged"):
                    print(
                        f"[EXIT CHECK] user={user_id} "
                        f"last_cam={last_camera} role={role} "
                        f"should_exit={should_exit}"
                    )
                    data["_exit_check_logged"] = True

                if should_exit:
                    if is_hard_timeout and role != "exit":
                        print(f"[HARD EXIT] user={user_id} (forced after {GLOBAL_TIMEOUT * HARD_TIMEOUT_MULTIPLIER}s)")
                    else:
                        print(f"[GLOBAL EXIT] user={user_id}")
                    
                    self._handle_exit(user_id)
                    to_remove.append(user_id)

        for uid in to_remove:
            GLOBAL_PRESENCE.pop(uid, None)

    def _get_tracker(self, camera_id: str) -> FaceTracker:
        tracker = self._trackers.get(camera_id)
        if tracker is None:
            tracker = FaceTracker(camera_id=camera_id)
            self._trackers[camera_id] = tracker
        return tracker

    def _observe_attendance(self, camera_id, user_id, face, now):
        key = (camera_id, user_id)
        if now - self.last_attendance_checked.get(key, 0) < ATTENDANCE_CHECK_INTERVAL:
            return
        self.last_attendance_checked[key] = now
        try:
            from backend.app.services import attendance_service

            with self._get_db() as db:
                attendance_service.observe_recognition(
                    db,
                    user_id=int(user_id),
                    camera_id=camera_id,
                    confidence=face.get("identity_confidence"),
                )
        except Exception as exc:
            # Attendance must never interrupt recognition, overlays, or local
            # presence tracking. Its outbox/audit path reports failures later.
            print(
                f"[ATTENDANCE ERROR] user={user_id} camera={camera_id} {exc}"
            )

    def handle_event(self, event):
        now = time.time()
        camera_id = event["camera_id"]
        floor = event["floor"]
        role = get_camera_role(camera_id)
        if ENABLE_LOGGING:
            print(f"[ROLE] cam={camera_id} role={role}")

        cam_users = self.camera_state.setdefault(camera_id, {})

        # Apply per-camera identity smoothing here (previously in the AI
        # worker). Doing it in this single-process observer means tracker
        # state is coherent regardless of NUM_AI_WORKERS — the previous
        # implementation fragmented across workers and caused flicker.
        faces = self._get_tracker(camera_id).update(event.get("faces", []))

        # DETECTION broadcast (throttled) — feeds the dashboard AND the
        # MJPEG overlay cache. Signature is the set of (user_id or
        # ("u", track_id)) so a stable Unknown track doesn't re-trigger
        # emits, but a new face or identity flip does. An empty face set
        # still signals "no one in frame" and must propagate — guard that
        # by keying the throttle so we emit when faces == [] and the
        # last sig was non-empty.
        det_sig = frozenset(
            f.get("user_id") or ("u", f.get("track_id"))
            for f in faces
        )
        if self._detection_throttle.should_emit(camera_id, det_sig):
            self._broadcast({
                "type": "DETECTION",
                "camera_id": camera_id,
                "faces": faces,
            })

        for face in faces:
            user_id = face.get("user_id")
            name = face.get("identity")

            if not user_id or not name or name == "Unknown":
                continue

            self._observe_attendance(camera_id, user_id, face, now)

            if user_id not in GLOBAL_PRESENCE:
                GLOBAL_PRESENCE[user_id] = {
                    "last_seen": now,
                    "cameras": set(),
                    "last_camera": camera_id,
                    "name": name,
                    "floor": floor,
                }
            GLOBAL_PRESENCE[user_id]["last_seen"] = now
            GLOBAL_PRESENCE[user_id]["cameras"].add(camera_id)
            GLOBAL_PRESENCE[user_id]["last_camera"] = camera_id
            GLOBAL_PRESENCE[user_id]["name"] = name
            GLOBAL_PRESENCE[user_id]["floor"] = floor

            # User confirmed present — drop any lingering recovery immunity
            # so a *later* real exit isn't protected by startup state.
            self.recovered_users.discard(user_id)
            GLOBAL_PRESENCE[user_id].pop("_exit_check_logged", None)

            is_active = self._is_user_inside(user_id)

            if not is_active:
                if role == "entry":
                    print(
                        f"[ENTRY ALLOWED] user={user_id} cam={camera_id}"
                    )
                    cam_users[user_id] = {
                        "last_seen": now,
                        "name": name,
                        "floor": floor,
                        "role": role,
                    }
                    self._on_entry(camera_id, user_id, cam_users[user_id])
                else:
                    print(
                        f"[ENTRY BLOCKED] user={user_id} cam={camera_id} "
                        f"role={role}"
                    )
            else:
                # Already inside → refresh presence + throttled UPDATE.
                rec = cam_users.setdefault(user_id, {
                    "last_seen": now,
                    "name": name,
                    "floor": floor,
                    "role": role,
                })
                rec["last_seen"] = now
                rec["name"] = name
                rec["floor"] = floor

                sent_map = self.last_update_sent.setdefault(camera_id, {})
                if now - sent_map.get(user_id, 0) >= UPDATE_INTERVAL:
                    sent_map[user_id] = now
                    self._on_update(camera_id, user_id, rec)

        # Per-frame [TRACK]/[GLOBAL] dumps fire on every event (~tens of
        # times per second per camera) and are identical run-to-run when
        # the active set hasn't changed. Gate behind explicit debug logging
        # so they don't bury real WARN/ERROR output.
        if ENABLE_LOGGING:
            print(
                f"[TRACK] cam={camera_id} "
                f"active={list(cam_users.keys())}"
            )
            print(
                "[GLOBAL] "
                + str({
                    uid: {
                        "last_seen": fmt_ist(rec["last_seen"]),
                        "cameras": list(rec["cameras"]),
                        "name": rec.get("name"),
                        "floor": rec.get("floor"),
                    }
                    for uid, rec in GLOBAL_PRESENCE.items()
                })
            )

        # Snapshot the global presence dict and ship it to the main
        # process via the WS queue. Convert sets → lists for pickling.
        #
        # Throttle: signature is the frozenset of present user IDs. If
        # nobody entered or left since the last emit AND the heartbeat
        # deadline hasn't hit, skip — downstream already saw ENTRY/EXIT
        # for the changes we care about, so PRESENCE is purely a "who's
        # still here" keepalive and doesn't need per-frame fidelity.
        presence_sig = frozenset(GLOBAL_PRESENCE.keys())
        if self._presence_throttle.should_emit(
            PRESENCE_THROTTLE_KEY, presence_sig,
        ):
            self._broadcast({
                "type": "PRESENCE",
                "snapshot": {
                    uid: {
                        "last_seen": rec["last_seen"],
                        "cameras": list(rec["cameras"]),
                    }
                    for uid, rec in GLOBAL_PRESENCE.items()
                },
            })

        alert_service.handle_event({
            "type": "DETECTION",
            "camera_id": camera_id,
            "floor": floor,
            "faces": faces,  # smoothed; alert logic sees stable identities
        })

    def _on_entry(self, camera_id, user_id, rec):
        floor = rec["floor"]
        name = rec["name"]

        with self._get_db() as db:
            tracking_service.handle_entry(db, user_id, camera_id, floor)
        self.active_sessions.add(user_id)

        print(f"[ENTRY] {name} at {camera_id}")
        self._broadcast({
            "type": "ENTRY",
            "patient_id": user_id,
            "name": name,
            "camera_id": camera_id,
            "floor": floor,
        })

    def _on_update(self, camera_id, user_id, rec):
        with self._get_db() as db:
            tracking_service.handle_update(
                db, user_id, camera_id, rec["floor"],
            )

        # Per-detection presence heartbeat — fires ~once/sec per patient
        # per camera. The information is already on the wire via the
        # broadcast below and visible to operators on the dashboard;
        # printing it to stdout is pure log spam. Kept silent here so
        # real INFO/WARN messages stay readable.
        self._broadcast({
            "type": "UPDATE",
            "patient_id": user_id,
            "name": rec["name"],
            "camera_id": camera_id,
            "floor": rec["floor"],
        })

    def _handle_exit(self, user_id):
        """Global exit — user hasn't been seen on any camera for GLOBAL_TIMEOUT."""
        data = GLOBAL_PRESENCE.get(user_id, {})
        last_camera = data.get("last_camera")
        rec = {
            "name": data.get("name") or str(user_id),
            "floor": data.get("floor"),
        }

        # Prune any per-camera state for this user so we don't double-exit
        # if legacy `_on_exit` paths ever fire again.
        for cam_id, users in list(self.camera_state.items()):
            users.pop(user_id, None)
            self.last_update_sent.get(cam_id, {}).pop(user_id, None)
            if not users:
                self.camera_state.pop(cam_id, None)
                self.last_update_sent.pop(cam_id, None)

        self._on_exit(last_camera, user_id, rec)

    def _on_exit(self, camera_id, user_id, rec):
        with self._get_db() as db:
            tracking_service.handle_exit(
                db, user_id, camera_id, rec["floor"],
            )
        self.active_sessions.discard(user_id)

        print(f"[EXIT] {rec['name']}")
        exit_payload = {
            "type": "EXIT",
            "patient_id": user_id,
            "name": rec["name"],
            "camera_id": camera_id,
            "floor": rec["floor"],
        }
        self._broadcast(exit_payload)
        alert_service.handle_event(exit_payload)
