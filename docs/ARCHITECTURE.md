# Iris — Multi-Camera Face Recognition & Patient Tracking System

> A product of **Synora AI Labs**. Throughout this doc, "Iris" refers to
> the product/system; "Synora" appears only in infrastructure namespaces
> (env vars, paths, service names) where it denotes the company-level
> deployment.

## System Design & Engineering Documentation

> **Source of truth.** Verified against the codebase as of 2026-05-25. Where the implementation differs from an idealized design, this doc describes what the code **actually does**, with file paths and line references for verification.

---

## 1. System Overview

**Purpose.** Iris is a real-time, multi-camera computer vision platform for clinical environments, built by Synora AI Labs. It identifies registered users (patients, doctors, employees, visitors, and relatives) across a network of cameras, automatically opens and closes "presence sessions," and surfaces live activity to a React operator dashboard.

**Key capabilities (as implemented):**

- **Multi-source ingestion:** RTSP IP cameras (and pre-existing USB devices for legacy installs) — the runtime roster lives in PostgreSQL `camera_master` and is managed through the admin API in §3.12. The previous in-code `backend/camera/config.py` has been retired.
- **Real-time inference:** InsightFace `buffalo_l` (RetinaFace detector + ArcFace 512-d embeddings), with Haar-cascade fallback when InsightFace fails to load.
- **Identity tracking:** Per-camera IoU/centroid spatial tracker plus a temporal majority-vote smoother (5-frame window).
- **Automated session management:** PostgreSQL `PatientSession` rows transition INSIDE → OUT based on continuous presence and `last_seen` timeouts.
- **Live delivery:** MJPEG streams (`/api/v1/stream/{camera_id}`) and a single WebSocket channel (`/api/v1/ws/live`) broadcast real-time events.
- **Front-desk OPD workflow.** A dedicated front-desk page scans a patient (USB webcam or system camera), prefills an OPD form, assigns a daily per-department token (`GEN-014`, `CARD-003`), prints a thermal slip with a QR code, and persists the visit. Tokens, status timelines, and admin config (departments / doctors / rooms) live in their own tables — see §3.13.

**Use case.** Hospital ward + OPD intake. Face recognition opens / closes presence sessions automatically; at the front desk the same recognition prefills the OPD form so the operator only picks a department and clicks **Generate Token**. Sessions and OPD visits together give "who was here, when, and which doctor they went to."

---

## 2. High-Level Architecture

The single runtime is the FastAPI service at [backend/app/main.py](../backend/app/main.py). It owns the REST API, MJPEG streamer, WebSocket broadcaster, FAISS index, and PostgreSQL session. **When `START_CAMERA_SYSTEM=1` is set in the environment** ([backend/app/main.py:107](../backend/app/main.py#L107)), the lifespan also spawns the camera worker, AI worker, and event-processor child processes via [backend/camera/manager.py](../backend/camera/manager.py); without that flag the API runs alone (useful for `uvicorn --reload` and CI).

Stateless inference and database helpers — face detection, alignment, embedding, FAISS index — live under [backend/engine/](../backend/engine/) and are imported as a library by both the FastAPI process and the AI worker. They are **not** a separate runnable backend.

```text
+----------------------+       +-------------------------+       +-----------------------+
|   Camera Sources     |       |  Per-Camera Worker      |       |   AI Stages           |
|  USB / RTSP (cv2)    |==>==> |  Process (manager.py)   |==>==> | Detect → Align →      |
|                      |       |  multiprocessing.Queue  |       | Embed → Match (FAISS) |
+----------------------+       +-------------------------+       +-----------------------+
                                          |                                   |
                                          v                                   v
+----------------------+       +-------------------------+       +-----------------------+
|  MJPEG StreamingResp |<==<== |  Annotated frame queue  |       |  IoU/Centroid Tracker |
|  /api/v1/stream/{id} |       |  (cv2 draw + JPEG enc)  |       |  + Identity Smoother  |
+----------------------+       +-------------------------+       +-----------------------+
          |                                                                  |
          v                                                                  v
+----------------------+       +-------------------------+       +-----------------------+
|  React SPA (Vite)    |<==<== |  WebSocket /ws/live     |<==<== |  Session Service      |
|  Dashboard / pages   |       |  event_bridge polls     |       |  (PostgreSQL writes)  |
+----------------------+       +-------------------------+       +-----------------------+
```

**Process & thread boundaries (actual):**

- **FastAPI process (main):** uvicorn + ASGI app, MJPEG streaming, REST routers, WebSocket manager, FAISS index, SQLAlchemy session.
- **Per-camera worker processes:** spawned by [backend/camera/manager.py](../backend/camera/manager.py) using `multiprocessing.Process`. Each owns a `cv2.VideoCapture`, runs detection → embedding → matching → tracking, and pushes annotated frames + events back through `multiprocessing.Queue` objects.
- **JPEG encoder thread pool:** `ThreadPoolExecutor` inside the FastAPI process (see [backend/app/api/routes/stream.py](../backend/app/api/routes/stream.py)) — keeps `cv2.imencode` off the asyncio event loop.
- **Event bridge thread:** drains the cross-process WS event queue and republishes through the FastAPI WebSocket manager (`event_bridge.py`).

> **Doc note.** Earlier drafts described frames being pickled through `multiprocessing.Queue`. The shipping code uses **per-camera worker processes** that publish frame _handles_ through `multiprocessing.Queue`, while the actual pixel data travels through a `SharedMemory` ring buffer — see `FrameBufferPool` in [backend/camera/shared_frame_buffer.py](../backend/camera/shared_frame_buffer.py), wired up in [backend/camera/manager.py:201-204](../backend/camera/manager.py#L201-L204). The `PROCESS_MAX_DIM = 960` long-edge cap ([backend/config.py:36](../backend/config.py#L36)) still applies — it bounds the slot size in the pool and keeps detector input sane.

---

## 3. Core Components

### 3.1 Camera Manager, Registry & Workers

- **Files:** [backend/camera/manager.py](../backend/camera/manager.py), [backend/camera/registry.py](../backend/camera/registry.py), [backend/camera/store.py](../backend/camera/store.py), [backend/camera/worker.py](../backend/camera/worker.py).
- **Responsibility split:**
  - **`store.py`** is the PostgreSQL repository for `camera_master`. It preserves the original worker-facing dictionary shape, validates facility/location placement, and owns transactional CRUD. A legacy `database/cameras.json` is imported once, guarded by a durable `data_migrations` marker; it is never used as a runtime fallback.
  - **`registry.py`** owns the **runtime** lifecycle: `camera_id → multiprocessing.Process` map, allocation/release of per-camera shared dict slots (`stream_queues`, `stream_versions`, `viewer_counts`), restart logic, and the RTSP probe used by `POST /cameras/test`. All mutations are serialised under an `asyncio.Lock`. Blocking work (`Process.start/join`, `cv2.VideoCapture` probes) is offloaded via `asyncio.to_thread` so the event loop stays responsive. **After every successful spawn the registry publishes a `CAMERA_RECONNECTED` event** on the WS bridge with the bumped `stream_version`, so the dashboard's `<img>` remounts the moment a worker is (re)started — the new worker can't emit this itself because from its perspective it's a first connection, not a reconnect.
  - **`manager.py`** wires shared resources (queues, `Manager().dict()` proxies, frame pool) at FastAPI lifespan startup, then hands control to the registry. Camera workers are owned exclusively by the registry — they are intentionally **not** added to the lifespan's `procs` list, so `registry.stop_all()` is the single tear-down path.
  - **`worker.py`** is unchanged in lifecycle responsibility — one `multiprocessing.Process` per camera, `cv2.VideoCapture` for RTSP/USB, exponential RTSP reconnect backoff, viewer-count gating on producer-side pushes (see §3.8).
- **Runtime mutations.** Adding, editing, or deleting a camera at runtime goes through the admin API (§3.12) and never requires restarting the FastAPI process. The registry's `update_camera` only restarts a worker when `source`, `type`, or `active` actually change — cosmetic edits (`name`, `floor`, `role`) skip the restart so the live MJPEG doesn't blip. Renames propagate to the dashboard tile label on the next `/tracking/cameras` poll without re-mounting the `<img>`.
- **Spawn / stop ordering invariants.** _Add:_ allocate shared dict slots → spawn worker → emit `CAMERA_RECONNECTED`. _Remove:_ cancel any in-flight MJPEG → terminate → join → kill (escalating timeouts) → free shared dict slots. Keys exist iff the worker is alive (or, for `stream_queues`, the camera still exists in the store), keeping the stream endpoint's lookups race-free.
- **Capture:** `cv2.VideoCapture` (USB index or RTSP URL). RTSP reconnect uses an exponential backoff loop; empty frames trigger a `CAMERA_DISCONNECTED` event on the WS bridge, and the next successful frame after a reconnect bumps `stream_version` so the frontend re-mounts the `<img>`.
- **Backpressure:** `CAPTURE_QUEUE_SIZE = 2` ([backend/config.py:49](../backend/config.py#L49)). Older frames are dropped so AI always processes a near-current frame.

### 3.2 Detection

- **File:** [backend/engine/pipeline/detection.py](../backend/engine/pipeline/detection.py)
- **Primary:** InsightFace RetinaFace (`buffalo_l`), input size **800×800**, confidence **≥ 0.42** ([backend/config.py:55-56](../backend/config.py#L55-L56)). Tuned upward from the 640×640 / 0.5 default to extend useful detection range from ~1.7 m to ~2.9 m on the deployed Lenovo ultra-wide USB cam (95° HFOV) — see [HARDWARE_AND_CAMERAS.md](HARDWARE_AND_CAMERAS.md#72-per-tier-face-capacity) for the distance math.
- **Fallback:** OpenCV Haar cascade, used if InsightFace fails to load (CPU-only / model-missing environments).
- **Output:** bbox `[x1,y1,x2,y2]`, confidence, 5-point landmarks (when available).

### 3.3 Alignment

- **File:** [backend/engine/pipeline/alignment.py](../backend/engine/pipeline/alignment.py)
- 5-point similarity-transform alignment to ArcFace canonical 112×112. Pixel normalization is left to InsightFace (`NORMALIZE_PIXELS = False`, [backend/config.py:98](../backend/config.py#L98)).

### 3.4 Embedding

- **File:** [backend/engine/pipeline/embedding.py](../backend/engine/pipeline/embedding.py)
- InsightFace `buffalo_l` → 512-d L2-normalized vector (`EMBEDDING_DIM = 512`, [backend/config.py:103](../backend/config.py#L103)).
- GPU device selectable via `GPU_DEVICE_ID` (default 0; `-1` forces CPU).

### 3.5 Matching (FAISS)

- **File:** [backend/engine/database/face_db.py](../backend/engine/database/face_db.py)
- `faiss.IndexFlatIP(512)` — exact inner-product search over L2-normalized vectors, equivalent to cosine similarity.
- **Threshold:** `SIMILARITY_THRESHOLD = 0.60` for **all** recognition paths ([backend/config.py:120](../backend/config.py#L120)). Top-k = 3, aggregated by max score per identity. A second `SIMILARITY_THRESHOLD = 0.45` exists in [backend/app/core/config.py:71](../backend/app/core/config.py#L71) (Pydantic Settings) but is **currently dormant** — no callers read `settings.SIMILARITY_THRESHOLD`. Live tracking compensates for the single cutoff with the 5-frame majority-vote smoother described in §3.6.
- **Capacity:** `MAX_EMBEDDINGS_PER_IDENTITY = 10` ([backend/config.py:122](../backend/config.py#L122)). New registrations beyond the cap evict the oldest sample to bound the index.
- **Persistence:** binary `face_index.bin` + JSON `name_map.json` under `database/`. Loaded once at app startup.
- **Cross-process refresh.** Writes happen in the API process under a `threading.Lock` and are debounced 1 s by `schedule_save()`. AI workers in child processes notice new vectors via an **mtime poll** in [backend/camera/pipeline_runner.py:130-144](../backend/camera/pipeline_runner.py#L130-L144) (`_current_index_mtime` / `_maybe_reload_database`). Save ordering — name map renamed first, then index — guarantees readers never see a vector whose ID isn't in the map yet.

### 3.6 Tracker

- **File:** [backend/camera/tracker.py](../backend/camera/tracker.py)
- Centroid + IoU association (`IOU_THRESHOLD = 0.4`, `EXPIRE_SECONDS = 2.0`).
- Temporal stabilization via [IdentitySmoother](../backend/engine/pipeline/matching.py) — sliding window `SMOOTHING_WINDOW = 5` ([backend/config.py:125](../backend/config.py#L125)). At 30 FPS this is ~170 ms of history; majority vote suppresses one-frame flicker.

### 3.7 Session Service

- **File:** [backend/app/services/session_service.py](../backend/app/services/session_service.py)
- Translates confirmed identities into `PatientSession` rows.
- **Entry:** first confident match for a patient with no current INSIDE session opens one (entry_time = now).
- **Heartbeat:** `last_seen` is bumped on every confirmed re-detection.
- **Exit:** sessions older than the staleness window (`now − last_seen > timeout`) are flipped to OUT by a background sweep.
- **Invariant:** at most one INSIDE session per `patient_id` (enforced at the service layer).

### 3.8 MJPEG Stream Service

- **File:** [backend/app/api/routes/stream.py](../backend/app/api/routes/stream.py)
- Endpoint: `GET /api/v1/stream/{camera_id}` → `multipart/x-mixed-replace; boundary=frame`.
- Snapshot endpoint: `GET /api/v1/stream/{camera_id}/snapshot` returns a single overlay-free JPEG (used by the Front Desk scan flow).
- Per-camera output queue, `maxsize=1`; slow capture from the multiprocessing queue causes oldest frames to be dropped — the latest available frame is always served.
- JPEG encoding runs in a dedicated `ThreadPoolExecutor` (≤ 8 workers) so the event loop is never blocked by `cv2.imencode`.
- Frames are pre-annotated by the broadcaster (boxes + identity labels) so the visual feed is consistent with the WebSocket event stream. The snapshot endpoint reads the broadcaster's cached raw BGR frame and encodes without overlays.
- **Fan-out broadcaster:** one `StreamBroadcaster` task per camera owns the exclusive read of the multiprocessing source queue. Each viewer is registered as an `asyncio.Queue(maxsize=2)` subscriber; the broadcaster encodes once per frame and dispatches the same multipart bytes to all subscribers. This replaced the earlier single-consumer guard, which cancelled the previous viewer whenever a new one connected — a constraint that prevented multiple operator dashboards from sharing the same camera.
- **Per-subscriber back-pressure:** subscriber queues use drop-oldest semantics. A slow consumer loses frames locally but cannot block the broadcaster or any other subscriber.
- **Lifecycle:** the broadcaster is created lazily on first subscribe and shut down when the last subscriber leaves. `cancel_active_stream(camera_id)` (called by the camera registry on worker stop or restart) terminates the broadcaster cleanly and wakes every subscriber with a zero-length EOS sentinel so generators exit instead of hanging.
- **Viewer-count gating (producer-side):** the broadcaster increments the shared `viewer_counts` entry on first subscribe and decrements it on last unsubscribe. The camera worker reads this counter before pushing into the source queue and **skips the push entirely when no viewer is connected** — saving the ~1.5 MB pickle cost per capture per camera. Detection consumes from `frame_queue`, a separate path, and is unaffected.
- **Stream-version reconnect:** the broadcaster watches `stream_versions[camera_id]`. When the camera registry restarts a worker (drains the queue and bumps the version), the broadcaster resets its idle clock so existing subscribers are not timed out during the gap.

### 3.9 WebSocket Service

- **File:** [backend/app/api/routes/ws.py](../backend/app/api/routes/ws.py) + `event_bridge.py`
- Single endpoint: `/api/v1/ws/live`.
- A `ConnectionManager` tracks connected clients; the event bridge drains a cross-process `ws_queue` and broadcasts JSON payloads.
- **Event payloads emitted:** detection events with patient identity / status, session start / heartbeat / end, and camera health changes.

### 3.10 Database Layer

- **File:** [backend/app/db/postgres.py](../backend/app/db/postgres.py), models in [backend/app/db/models.py](../backend/app/db/models.py).
- **Engine:** **synchronous** SQLAlchemy `create_engine(...)` with `pool_pre_ping=True`. (No `asyncpg`, no `AsyncSession`.)
- **Schema bootstrap:** `Base.metadata.create_all()` at startup. **No Alembic migrations** are configured.
- **Tables (identity & tracking, [backend/app/db/models.py](../backend/app/db/models.py)):**
  - `User` — `id, user_type (PATIENT|DOCTOR|EMPLOYEE|VISITOR|RELATIVE), mrn, name, age, gender, dob, contact_number, address, department, doctor, category, role, specialty, staff_department, opd_department_id, opd_room_id, purpose, note, is_active, current_status, current_floor, current_camera_id, last_seen_at`. The class was renamed from `Patient` in the user-model expansion (`Patient = User` alias retained for legacy import sites); the underlying table is `users`. Type-specific fields are nullable — `mrn` is set for PATIENT rows only; `opd_department_id` / `opd_room_id` for DOCTOR; `role` / `staff_department` for EMPLOYEE; `purpose` for VISITOR.
  - `UserRelation` — `id, user_id, related_user_id, relation_type (SPOUSE|PARENT|CHILD|SIBLING|GUARDIAN|OTHER), note, created_at`. Directional link; the API surfaces both sides so a patient and their relative each see the link from their own profile.
  - `PatientSession` — `id, patient_id, status (INSIDE|OUT), entry_time, exit_time, last_seen, current_floor, current_camera`. Table name is `patient_sessions` (kept for FK stability); `patient_id` FKs `users.id` — all user types can record sessions, scoping is by `User.user_type` at query time.
- **Tables (front-desk OPD, [backend/app/db/frontdesk_models.py](../backend/app/db/frontdesk_models.py)):**
  - `Department` — `id, code, name, token_prefix, default_room_id, is_active`
  - `Room` — `id, name, floor, description, is_active`
  - `OPDVisit` — `id, patient_id, doctor_id, token_number, visit_type, department_id, room_id, department_name, doctor_name, room_name, note, status, source, created_by, created_at, updated_at, printed_at`. Both `patient_id` and `doctor_id` now FK to `users.id` (the standalone `Doctor` table was retired in Phase 1b and its rows folded into `users` with `user_type='DOCTOR'`). The `*_name` columns snapshot at issue time so slips and history stay readable even if the dept / doctor / room is later renamed or deactivated.
  - `TokenCounter` — `(counter_date, department_id) -> last_number`, used by the atomic per-department daily token allocator (see §3.13)
  - `VisitStatusHistory` — append-only log: `id, visit_id, from_status, to_status, changed_at, changed_by`. One row written on visit creation (`NULL → WAITING`) and one per real status change.
- **Soft-delete on config.** `Department` and `Room` are flipped to `is_active=False` instead of being deleted so historic `OPDVisit` FKs stay intact and the snapshotted name columns remain meaningful. `User` carries the same `is_active` flag for the equivalent soft-deactivation path on the people side.
- **FAISS vs Postgres:** FAISS is the active matching index (in-memory + binary file). Postgres holds user demographics, session state, and the full OPD record. `pgvector` is **not** used.

### 3.11 Frontend Live View

- **Path:** [frontend/](../frontend/), entry [frontend/src/main.jsx](../frontend/src/main.jsx).
- **Stack:** React 19 + Vite, **JavaScript / JSX (not TypeScript)**, Material-UI components, TailwindCSS for utility styling, `react-router-dom` for routing.
- **State:** small custom hooks (e.g. [useSocket](../frontend/src/hooks/useSocket.jsx), `useAlertStore`, `useWebcams`) backed by `useState` / context — **no Zustand**, no Redux.
- **Routes (`frontend/src/App.jsx`):**
  - `/` — Live View ([Dashboard.jsx](../frontend/src/pages/Dashboard.jsx))
  - `/frontdesk` — [FrontDesk](../frontend/src/pages/FrontDesk.jsx) (OPD intake, see §3.13)
  - `/register` — [Register](../frontend/src/pages/Register.jsx)
  - `/history` — [History](../frontend/src/pages/History.jsx) (sessions + OPD visits per date, per patient)
  - `/patients/:id` — [PatientProfile](../frontend/src/pages/PatientProfile.jsx)
  - `/settings` — [Settings](../frontend/src/pages/Settings.jsx) shell; sections are lazy-loaded:
    - `?section=frontdesk` → [FrontDeskConfig](../frontend/src/pages/FrontDeskAdmin.jsx) (rooms / departments / doctors)
    - `?section=manage` → [Patients](../frontend/src/pages/Patients.jsx) (edit, update-face, delete)
    - `?section=cameras` → [Cameras](../frontend/src/pages/Cameras.jsx) admin
  - `/patients` and `/cameras` redirect into `/settings` so older bookmarks still resolve.
- **Shared widgets:** [CustomSelect](../frontend/src/components/CustomSelect.jsx) is a portaled listbox replacement for `<select>` — native dropdowns were getting clipped by parent `overflow-hidden` cards. Used by every non-trivial select on the app (visit type, department, doctor, status, gender, camera, room).
- MJPEG streams are rendered via `<img src="…/stream/{id}" />`; WebSocket events update active-session and event-feed state. Tile labels use the human-readable `name` from `/tracking/cameras` (the field operators set in the admin UI), falling back to a formatted `camera_id` for legacy rows without a name.
- **Per-camera stream toggle:** [CameraCard](../frontend/src/components/CameraCard.jsx) holds a local `streamEnabled` flag (default `true`) with a "Stream On / Stream Off" button in the card overlay. When toggled off, the `<img>` is unmounted — the browser closes the MJPEG TCP connection, which cancels the server-side generator and decrements the viewer count, causing the camera worker to skip producer-side frame pushes (see §3.8). Detection, tracking, sessions, and WebSocket events continue uninterrupted; toggling back on remounts the `<img>` with a fresh `retryCount` and the stream resumes instantly.
- **Cameras admin page** ([frontend/src/pages/Cameras.jsx](../frontend/src/pages/Cameras.jsx)) is the UI for the admin API in §3.12 — list / add / edit / delete / restart / probe RTSP. Status pills (`Running` / `Stopped` / `Not running`) are driven by:
  - WS-push refetch — the existing `CAMERA_DISCONNECTED` / `CAMERA_RECONNECTED` events on `/ws/live` trigger a coalesced refetch (250 ms debounce), so a switch reboot that flaps several cameras only causes one round-trip.
  - A 30 s safety-net poll in case a WS event is dropped or the page is opened mid-reconnect.

### 3.12 Camera Admin API & Persistent Roster

- **Files:** [backend/app/api/routes/cameras.py](../backend/app/api/routes/cameras.py), [backend/app/schemas/cameras.py](../backend/app/schemas/cameras.py), [backend/camera/store.py](../backend/camera/store.py), [backend/camera/registry.py](../backend/camera/registry.py).
- **Source of truth.** PostgreSQL `camera_master`. `camera_master.code` preserves the stable external `camera_id`, so stream URLs, tracking history, and worker keys do not change. The previous in-code list is retired; a durable migration marker makes the legacy JSON seed a true one-time operation.
- **Endpoints (all under `/api/v1`):**

  | Method   | Path                    | Behaviour                                                                                  |
  | -------- | ----------------------- | ------------------------------------------------------------------------------------------ |
  | `GET`    | `/cameras`              | List every camera with runtime `status` (`running` / `stopped` / `missing`).               |
  | `GET`    | `/cameras/{id}`         | Single camera record.                                                                      |
  | `POST`   | `/cameras`              | Create + spawn worker. Pydantic rejects `type=usb`. Returns 409 on duplicate name.         |
  | `PATCH`  | `/cameras/{id}`         | Partial update. Restarts worker only when `source` / `type` / `active` actually change.    |
  | `DELETE` | `/cameras/{id}`         | Stop worker, free shared dict slots, remove from store.                                    |
  | `POST`   | `/cameras/{id}/restart` | Bounce the worker without changing config (useful after a flaky link).                     |
  | `POST`   | `/cameras/test`         | Probe an RTSP URL with a short-lived `cv2.VideoCapture` (≤8 s wall clock); never persists. |

- **Validation** ([backend/app/schemas/cameras.py](../backend/app/schemas/cameras.py)):
  - `source` must start with `rtsp://` or `rtsps://`.
  - `floor` is normalised to `^[a-z0-9_]+$` (e.g. `"Floor 1"` → `floor_1`).
  - `name` must be unique (case-insensitive); enforced in the store, surfaced as **409 Conflict**.
  - `type=usb` is intentionally rejected on create — USB enumeration is host-specific and not visible from the browser. Pre-existing USB rows (from earlier installs) keep running and can be edited / restarted / deleted from the UI; only `POST /cameras` blocks _new_ USB rows. The response model accordingly types `source` as `Union[str, int]` so the legacy USB device-index integer (e.g. `0`) doesn't trip Pydantic on read; the request models keep `source: str` so new entries can only be RTSP URLs.
- **`camera_id` is server-assigned + immutable.** Generated as `cam_<uuid8>` so the user never has to reason about uniqueness, and `PatientSession.current_camera_id` references stay valid across renames.
- **Concurrency model.** Every mutation grabs the registry's `asyncio.Lock` for its entire duration, so two concurrent admin requests on the same `camera_id` cannot race database writes or worker spawn / stop. Each repository operation uses its own short-lived SQLAlchemy session and transaction.
- **Lifespan integration.** The registry is wired during FastAPI startup ([backend/app/main.py](../backend/app/main.py)) — only when `START_CAMERA_SYSTEM=1`. Mutating routes return **503** with a clear message if hit before that wiring completes (e.g. CRUD called against a server booted without the camera stack).
- **Stream cleanup on remove / restart.** The registry calls [`stream.cancel_active_stream`](../backend/app/api/routes/stream.py) before terminating a worker. This is best-effort — Starlette's response-sender task is not directly addressable, so the actual MJPEG generator may continue polling for ≤ `IDLE_TIMEOUT = 15 s` until its empty-poll timer fires. With the producer gone, no more frames are sent to the client during this window; the connection then closes cleanly.
- **Live re-mount on respawn.** After every successful `_spawn_worker_sync` (boot, add, restart, source-change), the registry publishes a `CAMERA_RECONNECTED` event on the WS bridge with the bumped `stream_version`. The dashboard's `CameraCard` keys its `<img>` remount on that version, so an edited or restarted camera resumes its live feed within ~1 s of the new worker producing its first frame — no manual refresh, no waiting for the 15 s `IDLE_TIMEOUT` to lapse on the old generator.

#### Failure modes & their UX

| Failure                                         | What happens                                                                                                               | What the user sees                                                   |
| ----------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| RTSP URL wrong / unreachable                    | `/cameras/test` returns `{ok: false, error: ...}` synchronously.                                                           | Inline red banner inside the add/edit modal.                         |
| Worker process fails to start                   | Store row is committed, but `_workers` map has no entry. `status` resolves to `missing`.                                   | Row appears with red "Not running" pill; **Restart** button retries. |
| Camera goes offline at runtime                  | Worker emits `CAMERA_DISCONNECTED` on the WS; admin page coalesces and refetches, status flips to `missing`.               | Status pill updates without a manual refresh.                        |
| Backend started without `START_CAMERA_SYSTEM=1` | Registry is uninitialised; CRUD routes return **503** with a clear message; `GET /cameras` still works (reads from store). | Page lists rows but mutations show a toast error.                    |

#### Live role reload

Camera roles (and the strict-exit-mode gate) are resolved live by [backend/camera/utils/camera_roles.py](../backend/camera/utils/camera_roles.py). The parent loads a `multiprocessing.Manager` role map from PostgreSQL at boot, and the registry updates that map after every committed create, update, or delete. Editing a role, or adding/removing the first `role: exit` camera, therefore takes effect immediately without restarting capture workers or querying PostgreSQL on the detection hot path.

### 3.13 Front Desk OPD Workflow

The front desk runs alongside the tracking pipeline. It reuses the same
recognition engine but persists into its own tables ([backend/app/db/frontdesk_models.py](../backend/app/db/frontdesk_models.py))
and has its own routes ([backend/app/api/routes/frontdesk.py](../backend/app/api/routes/frontdesk.py),
[backend/app/api/routes/frontdesk_admin.py](../backend/app/api/routes/frontdesk_admin.py)) +
services ([backend/app/services/frontdesk_service.py](../backend/app/services/frontdesk_service.py),
[backend/app/services/frontdesk_admin_service.py](../backend/app/services/frontdesk_admin_service.py),
[backend/app/services/token_service.py](../backend/app/services/token_service.py)).

**User-type guard.** OPD tokens are issued only for `user_type=PATIENT`
rows. `/frontdesk/scan` and `/frontdesk/search` will still match
doctors / employees / visitors / relatives and surface them on the
operator UI (with a "View profile" link instead of the OPD form), but
the visit-creation path is patient-only. Doctors are referenced from
visits via `OPDVisit.doctor_id → users.id` where the target row has
`user_type=DOCTOR`.

#### Flow (one patient)

```text
1. Scan frame  ──▶  POST /frontdesk/scan         (wraps recognize_faces)
                    ├─ ranks identified faces by bbox area (largest =
                    │  closest to camera = person at the counter),
                    │  ties broken by recognition confidence
                    ├─ returns the primary as `patient` / `confidence`
                    ├─ returns ALL identified faces as `candidates[]`
                    │  with full _patient_summary embedded, so the
                    │  operator can one-click swap to a different
                    │  person without re-scanning (queue handling)
                    └─ returns `unrecognised_count` for any
                       detected-but-Unknown faces in the frame
2. Optional manual fallback  ──▶  GET /frontdesk/search?q=mrn|phone|name
3. Operator picks: visit_type (GENERAL | SPECIALIST | DOCTOR), department,
   doctor (if applicable), note.
4. POST /frontdesk/visits
   ├─ idempotent within 30s: same patient + same dept reuses the existing
   │  active visit instead of creating a duplicate.
   ├─ allocate_token() does SELECT ... FOR UPDATE on token_counters for
   │  (today, department_id) → bumps last_number atomically.
   ├─ resolves room: explicit room_id > doctor.room > department.default_room.
   ├─ snapshots department_name / doctor_name / room_name into the visit
   │  row (so rename / soft-delete of config rows does not break old slips).
   └─ writes VisitStatusHistory(NULL → WAITING).
5. Frontend renders the slip + QR (encodes the visit URL) and calls
   window.print(). Print CSS in index.css hides the rest of the page.
6. Operator advances status via PATCH /frontdesk/visits/{id} — each
   real transition appends to VisitStatusHistory.
```

#### Token allocation (atomic, per-department, daily reset)

[token_service.py](../backend/app/services/token_service.py):

```python
INSERT INTO token_counters (counter_date, department_id, last_number)
VALUES (:today, :dept, 0)
ON CONFLICT (counter_date, department_id) DO NOTHING;

SELECT ... FROM token_counters
WHERE counter_date = :today AND department_id = :dept
FOR UPDATE;                       # row-lock for the duration of the txn

UPDATE last_number = last_number + 1;
RETURN f"{prefix}-{last_number:03d}"
```

The unique constraint on `(counter_date, department_id)` plus `FOR UPDATE`
serialises concurrent front-desk terminals on the same department for the
same day. Resets are implicit — at midnight the next allocation hits a
fresh row.

#### Live queue + WebSocket events

The front-desk page subscribes to two events on the existing `/ws/live`
channel (no new channel):

- `frontdesk.visit.created`
- `frontdesk.visit.status_changed`

Both carry the serialised visit. A 15-second polling fallback covers the
case where WS is offline. Broadcast is best-effort; a WS failure never
fails the API request that created the visit.

#### History page integration

The History page merges two data sources per date:

- `GET /history/date/{date}` — face-tracking sessions (existing).
- `GET /frontdesk/patients/by-date/{date}` — distinct patients with at
  least one OPD visit on the date.

Patients in only the OPD list get an `OPD ×N` badge in the row; clicking
opens a tabbed detail with a **Sessions** view and an **OPD Visits**
view, each filterable by date.

#### Settings & admin

Departments / doctors / rooms are managed at `/settings?section=frontdesk`
([FrontDeskAdmin.jsx](../frontend/src/pages/FrontDeskAdmin.jsx), which exports
the `FrontDeskConfig` component embedded by [Settings.jsx](../frontend/src/pages/Settings.jsx)).
Deletes are soft; the snapshot columns on `OPDVisit` mean deactivating a
doctor or renaming a department does not corrupt historical slips.

### 3.14 Single-facility identity and operations model

Iris serves exactly one facility. `facility_master` remains as a singleton
configuration row because cameras, locations, punches, and client-HIS payloads
need a stable relational/configuration anchor; it is not a tenant selector.

| Table | Purpose |
| --- | --- |
| `facility_master` | Singleton Mednet deployment record and client-HIS identifiers. Created only by `backend.scripts.seed_initial`; read-only at `/api/v1/facility`. |
| `location_master` | Named places inside a facility (floor / corridor / room / gate), hierarchical via `parent_location_id` (FHIR-Location style). CRUD at `/api/v1/locations`; cameras reference facilities and optional locations through `camera_master`. |
| `person_facility` | Operational membership in the singleton facility: client-facing `person_type`, MRN, visit counter, and last-visit time. There is one effective membership per user in this deployment. |
| `person_tracking_logs` | Append-only punch / sighting events: `visit_type` IN/OUT (official punches from gate/kiosk cameras) or TRACKER_IN/TRACKER_OUT (internal zone sightings, never exported), `visit_number` snapshot, `source` KIOSK/CAMERA/MANUAL. Presence state stays derived. |
| `punch_export_queue` | Outbound punch deliveries to the client attendance API (EMPLOYEE/DOCTOR mappings only, one punch per request). `biometric_idx` mirrors the client's unique transaction id so retries are idempotent. Pusher lands in a later phase. |
| `pre_registration_log` | One row per pre-registration pushed to the client HIS; stores their `preRegnId` / `tokenNo` (shown on the kiosk) plus raw request/response JSONB for audit. Forwarder lands in a later phase. |

Identity stays in `users` — that table **is** the person registry. Person ids
stay integers because the client punch contract requires a numeric
`biometricUID`; a `person_guid` UUID column exists for future cross-site sync.
Phase 1 adds the registry columns (`prefix`, `first/middle/last_name`,
`whatsapp_number`, `email`, `city/state/country/pin_code`,
`national_id_type`+`national_id`, next-of-kin trio, `photo_url`).

`python -m backend.scripts.seed_initial` idempotently creates the Mednet row
and the first superadmin. Startup never invents a facility or credentials.
The internal `facility_id` columns remain for referential integrity and
integration payload compatibility, but all service resolution is automatic.

Client-HIS endpoint URLs + credentials are env vars
(`CLIENT_PUNCH_API_URL`, `CLIENT_PREREG_API_URL`, … — see CONFIGURATION.md §2);
facility integration identifiers live on the singleton facility row.

**Kiosk** (Phase 2): a second Vite entry point ([kiosk.html](../frontend/kiosk.html)
→ [src/kiosk/](../frontend/src/kiosk/)) gives the entry-gate screen its own
URL and bundle (no router/MUI/WS — ~5 kB gzipped). Device binding (camera,
IN/OUT mode, facility, kiosk serial) is chosen once and persisted in
localStorage. The loop: capture a webcam frame every ~1.5 s → `POST
/kiosk/scan` ([routes/kiosk.py](../backend/app/api/routes/kiosk.py) →
[kiosk_service.py](../backend/app/services/kiosk_service.py)) → largest face
recognized → punch recorded (duplicate window `KIOSK_DUPLICATE_WINDOW`,
default 120 s) → greeting (staff: welcome/goodbye + punch time; patients on
IN additionally get "Pre-register for my visit" / "I am visiting someone
else") → back to scanning. Unknown faces see "please visit the front desk"
(the kiosk never self-registers). EMPLOYEE/DOCTOR punches enqueue a
`punch_export_queue` row; patient pre-registrations store a
client-shaped payload in `pre_registration_log` as PENDING — the actual
HTTP pushes to the client HIS are the export phase.

**Outbound integration** (Phase 3): a single daemon thread
([export_worker.py](../backend/app/services/export_worker.py), started from
the FastAPI lifespan) drains two queues to the partner HIS via a
dependency-free stdlib HTTP layer
([client_api.py](../backend/app/services/client_api.py)). Staff punches
(`punch_export_queue`) are delivered by the worker; patient
pre-registrations are pushed **inline** by `POST /kiosk/prereg` (so the
kiosk shows the returned `tokenNo` immediately) with the worker as retry
safety net. Reliability: a row is claimed (`status→SENDING`, atomic
rowcount-checked UPDATE) before its HTTP call so nothing double-sends;
failures back off exponentially (`EXPORT_BACKOFF_BASE`→`_CAP`) and
dead-letter after `EXPORT_MAX_ATTEMPTS`; a row stuck in `SENDING` (crash
mid-send) is reclaimed after `EXPORT_STALE_SECONDS`. Idempotency is also
enforced client-side (`biometricIDX`). The worker stays **dormant unless a
`CLIENT_*_API_URL` is set**, so an unconfigured deploy never calls out.
`/api/v1/integrations/*` exposes queue health + manual retry (API_REFERENCE.md §23).

**Profile photos** ([media_service.py](../backend/app/services/media_service.py)):
registration saves the first face-bearing image as the user's DP under
`MEDIA_DIR` (default `database/media/`, gitignored — biometric PII), served at
`/media/*` via StaticFiles. Photos are re-encoded to clean JPEGs (EXIF and any
embedded payload discarded) and named by the random `person_guid` so URLs are
not enumerable. The DB stores media-root-relative paths; setting
`MEDIA_BASE_URL` (future media server) repoints every resolved URL with no
data migration. `/media` must be auth-gated when feat/auth-rbac lands.

---

## 4. Data Flow & Pipeline

End-to-end path for one frame on Camera 1:

1. **Capture.** Worker reads frame via `cv2.VideoCapture` and downscales the long edge to 960 px ([backend/config.py:38](../backend/config.py#L38)).
2. **Motion gate (optional).** A 64×64 grayscale mean-abs-diff against the previous frame; if below `MOTION_SKIP_THRESHOLD = 2.5` ([backend/config.py:144](../backend/config.py#L144)), the AI stages are skipped — but at most for `MOTION_SKIP_MAX_INTERVAL_S = 2.0` seconds, so trackers still tick.
3. **Detect.** RetinaFace returns bboxes + landmarks; faces below `MIN_FACE_SIZE = 24 px` ([backend/config.py:57](../backend/config.py#L57)) or below confidence 0.4 are dropped.
4. **Align.** 112×112 ArcFace crop.
5. **Embed.** 512-d L2-normalized vector.
6. **Match.** FAISS top-3 inner product → max-per-identity aggregation → cosine ≥ 0.60 confirms a known patient.
7. **Smooth.** Identity decision passes through a 5-frame majority-vote window before being treated as confirmed.
8. **Track.** IoU/centroid tracker associates the bbox with an existing track; expired tracks are pruned after 2 s.
9. **Session update.** [session_service](../backend/app/services/session_service.py) opens an INSIDE session or bumps `last_seen`.
10. **Annotate + encode.** Worker draws bbox + label, pushes the annotated frame to the per-camera stream queue (`maxsize=1`) and an event payload to the WS queue.
11. **Deliver.** MJPEG endpoint flushes the JPEG; the WebSocket bridge broadcasts the JSON event.

---

## 5. AI / ML Pipeline

| Stage     | Model / Algorithm                      | Notes                                                     |
| --------- | -------------------------------------- | --------------------------------------------------------- |
| Detection | InsightFace RetinaFace (`buffalo_l`)   | Haar cascade fallback when GPU/InsightFace is unavailable |
| Alignment | 5-point similarity transform           | Output 112×112, BGR                                       |
| Embedding | ArcFace (ResNet) via `buffalo_l`       | 512-d, L2-normalized                                      |
| Indexing  | FAISS `IndexFlatIP`                    | Exact search; fast enough for sub-100k identities         |
| Decision  | Cosine ≥ 0.60 (pipeline) / ≥ 0.45 (REST) → known; below → unknown | Smoothed by 5-frame majority vote in pipeline path  |

**Unknown handling.** Unknown faces are kept only as transient tracker IDs inside the running process — they are **not** persisted to Postgres or FAISS, which keeps the index clean.

---

## 6. Tracking System

Centralized at the **camera-worker level** (not cross-camera). Each camera worker runs its own tracker; cross-camera identity continuity is achieved purely through embedding match (the same patient ID appears on both cameras when seen). There is no explicit cross-camera trajectory fusion in the current code.

- **Spatial:** centroid distance + IoU, threshold 0.4.
- **Temporal:** `IdentitySmoother` window of 5 frames, majority vote.
- **Track lifetime:** 2 s of unmatched frames before the track is dropped.

---

## 7. Performance Optimizations (in code today)

| Optimization                                | File / Setting                                                                                                                | Effect                                                                                  |
| ------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Frame resize cap (long edge ≤ 960 px)       | [backend/config.py:36](../backend/config.py#L36)                                                                                 | Bounds SharedMemory slot size and keeps detector input sane                             |
| **SharedMemory frame transport**            | [backend/camera/shared_frame_buffer.py](../backend/camera/shared_frame_buffer.py)                                                | Zero-copy frame hand-off across stages; replaces pickling                               |
| Per-camera output queue `maxsize=1`         | [backend/app/api/routes/stream.py](../backend/app/api/routes/stream.py)                                                          | Always-fresh MJPEG; no accumulating lag                                                 |
| JPEG encode in ThreadPoolExecutor           | stream.py                                                                                                                     | Keeps `cv2.imencode` off the asyncio loop                                               |
| Viewer-count gating on `stream_queue` push  | [worker.py](../backend/camera/worker.py), [manager.py](../backend/camera/manager.py), [stream.py](../backend/app/api/routes/stream.py) | Skips MJPEG frame copy per capture when no client is watching; detection unaffected     |
| Motion-gated inference                      | [backend/config.py:143-145](../backend/config.py#L143-L145)                                                                      | Skips detector on idle scenes; forced run every ≤2 s                                    |
| FAISS `IndexFlatIP` over normalized vectors | [face_db.py](../backend/engine/database/face_db.py)                                                                              | Exact cosine match, no quantization error                                               |
| Bounded embedding history per identity (10) | [backend/config.py:100](../backend/config.py#L100)                                                                               | Prevents unbounded index growth                                                         |
| Debounced FAISS save (1 s) + atomic rename  | [pipeline_service.py](../backend/app/services/pipeline_service.py), [face_db.py](../backend/engine/database/face_db.py)             | Coalesces burst registrations into a single disk write; readers never see partial state |

> **Known limitation.** SQLAlchemy is synchronous and the engine is invoked from FastAPI route handlers. Under heavy session-write load this can serialize requests on the worker thread. Migrating to `asyncpg` + `AsyncSession` is a tracked roadmap item.

---

## 8. Database & Session Management

**Connection.** `DATABASE_URL` from [backend/app/core/config.py](../backend/app/core/config.py) (Pydantic `BaseSettings` reading `.env`). Synchronous engine, `pool_pre_ping=True`, default pool sizing.

**Session lifecycle:**

- **Open:** confirmed identity with no INSIDE session ⇒ insert `PatientSession(status=INSIDE, entry_time=now, last_seen=now)`. `Patient.current_status` is updated to INSIDE.
- **Heartbeat:** subsequent confirmed matches update `last_seen` (and `Patient.last_seen_at`).
- **Close:** background sweep flips sessions to OUT when `now − last_seen` exceeds the configured staleness window; `exit_time` is stamped.
- **Recovery on boot:** sessions still INSIDE at startup are reconciled — those whose `last_seen` is older than the recovery threshold are force-closed.

**Invariants enforced at the service layer:** at most one INSIDE session per `patient_id`; FAISS index ID ⇄ patient ID is a one-to-many mapping (≤ 10 vectors per patient).

---

## 9. Communication Layer

### REST API (mounted at `/api/v1`, see [backend/app/api/router.py](../backend/app/api/router.py))

| Method | Path                            | Purpose                                                         |
| ------ | ------------------------------- | --------------------------------------------------------------- |
| GET    | `/health`                       | Liveness probe                                                  |
| POST   | `/recognize`                    | One-shot recognition for an uploaded image                      |
| POST   | `/register/multi`               | Register a new user with multiple face samples; `user_type` form field selects PATIENT / DOCTOR / EMPLOYEE / VISITOR / RELATIVE |
| GET    | `/users?type=&q=&is_active=`    | Filtered user list (canonical)                                  |
| POST   | `/users`                        | Create a user of a given type (face enrol via update-face)      |
| GET    | `/users/{id}`                   | User detail + embedded relations                                |
| PATCH  | `/users/{id}`                   | Partial update with the extended field set                      |
| PUT    | `/users/{id}`                   | Legacy full-update; routes to PATCH when extended fields are present |
| DELETE | `/users/{id}?soft=true|false`   | Soft (flip `is_active`) or hard delete (purges FAISS row + thumbnails) |
| POST   | `/users/{id}/update-face/multi` | Replace a user's face samples                                   |
| GET    | `/users/{id}/relations`         | List every relation involving the user (both sides)             |
| POST   | `/users/{id}/relations`         | Link another user with a `relation_type`                        |
| DELETE | `/users/{id}/relations/{rel_id}`| Unlink                                                          |
| GET    | `/tracking/find`                | Search by name / MRN (re-listed under tracking)                 |
| GET    | `/tracking/presence`            | Aggregated presence snapshot                                    |
| GET    | `/tracking/cameras`             | Active-camera roster (id, name, floor, role) + grouped users    |
| GET    | `/tracking/current`             | Current detections per camera                                   |
| GET    | `/tracking/by-camera`           | Group active users by camera                                    |
| GET    | `/tracking/summary`             | Headline KPIs                                                   |
| GET    | `/history/date/{target_date}`   | Day-level session history (carries `user_type` per row)         |
| GET    | `/history/patient/{patient_id}` | Per-user session history (URL kept patient-flavoured)           |
| GET    | `/patients/active`              | *Deprecated alias* — list users currently INSIDE                |
| GET    | `/patients/{id}`                | *Deprecated alias* — user detail + embedded relations           |
| PATCH  | `/patients/{id}/status`         | *Deprecated alias* — manual status override                     |
| GET    | `/metrics`                      | System / pipeline metrics                                       |
| GET    | `/stream/{camera_id}`           | MJPEG stream (multipart/x-mixed-replace)                        |
| GET    | `/cameras`                      | Camera roster + runtime status (admin)                          |
| GET    | `/cameras/{id}`                 | Single camera record                                            |
| POST   | `/cameras`                      | Create + spawn (RTSP only)                                      |
| PATCH  | `/cameras/{id}`                 | Partial update; restarts worker only on source/type/active      |
| DELETE | `/cameras/{id}`                 | Stop worker + remove from store                                 |
| POST   | `/cameras/{id}/restart`         | Bounce a worker                                                 |
| POST   | `/cameras/test`                 | Probe an RTSP URL without persisting                            |
| GET    | `/stream/{camera_id}/snapshot`  | One JPEG frame from a live system camera (used by Front Desk)   |
| WS     | `/ws/live`                      | Live event broadcast                                            |

**Front Desk / OPD** (all under `/api/v1`, see [backend/app/api/routes/frontdesk.py](../backend/app/api/routes/frontdesk.py)):

| Method | Path                                              | Purpose                                                         |
| ------ | ------------------------------------------------- | --------------------------------------------------------------- |
| POST   | `/frontdesk/scan`                                 | Recognise faces in an uploaded frame; returns the closest-to-camera primary + every other identified face as `candidates[]` for one-click queue swap |
| GET    | `/frontdesk/search?q=…`                           | Manual MRN / phone / name fallback (same response shape as scan)|
| POST   | `/frontdesk/visits`                               | Create a visit + allocate a per-department daily token          |
| GET    | `/frontdesk/visits/today`                         | Today's queue (filter by department / status)                   |
| GET    | `/frontdesk/visits/{id}`                          | Single visit + slip payload                                     |
| PATCH  | `/frontdesk/visits/{id}`                          | Update status (WAITING → IN_CONSULT → DONE → CANCELLED)         |
| POST   | `/frontdesk/visits/{id}/reprint`                  | Re-fetch slip payload + stamp `printed_at`                      |
| GET    | `/frontdesk/visits/{id}/history`                  | Full status-transition timeline for a visit                     |
| GET    | `/frontdesk/visits/by-date/{date}`                | Every visit on a given date                                     |
| GET    | `/frontdesk/patients/{id}/visits`                 | Visit history for a patient                                     |
| GET    | `/frontdesk/patients/by-date/{date}`              | Distinct patients with a visit on a date (used by History page) |

**Front Desk admin** (under `/api/v1/frontdesk/admin/*`, see [backend/app/api/routes/frontdesk_admin.py](../backend/app/api/routes/frontdesk_admin.py)):

| Method   | Path                                | Behaviour                                       |
| -------- | ----------------------------------- | ----------------------------------------------- |
| GET      | `/departments`                      | List active (or all) departments                |
| POST     | `/departments`                      | Create. 409 on duplicate `code`.                |
| PATCH    | `/departments/{id}`                 | Partial update                                  |
| DELETE   | `/departments/{id}`                 | Soft-delete (`is_active=False`)                 |
| GET      | `/doctors?department_id=…`          | List doctors, optionally filtered by department |
| POST     | `/doctors`                          | Create                                          |
| PATCH    | `/doctors/{id}`                     | Partial update                                  |
| DELETE   | `/doctors/{id}`                     | Soft-delete                                     |
| GET      | `/rooms`                            | List rooms                                      |
| POST     | `/rooms`                            | Create. 409 on duplicate `name`.                |
| PATCH    | `/rooms/{id}`                       | Partial update                                  |
| DELETE   | `/rooms/{id}`                       | Soft-delete                                     |

### WebSocket

- Single broadcast channel; clients receive every event (no per-camera subscription).
- Event payloads include detection events, session transitions, and camera health changes.

### MJPEG ↔ WebSocket synchronization

The MJPEG frame is annotated **inside the camera worker** before being enqueued, so the boxes/labels rendered in the video are guaranteed to come from the same recognition result that produced the WebSocket event. Browser-side, the two streams arrive over independent TCP connections, but the data inside them is consistent.

---

## 10. Frontend Architecture

**Stack (verified against [frontend/package.json](../frontend/package.json)):**

- React 19 + Vite
- JavaScript / JSX (no TypeScript)
- Material-UI (MUI) for components, TailwindCSS for utilities
- `react-router-dom` for routing
- No Zustand / Redux — state lives in custom hooks + React context

**Component map:**

- Live View ([Dashboard.jsx](../frontend/src/pages/Dashboard.jsx)) — camera grid, live event feed, active-session panel.
- [Patients](../frontend/src/pages/Patients.jsx) / [PatientProfile](../frontend/src/pages/PatientProfile.jsx) — registry browsing.
- [Register](../frontend/src/pages/Register.jsx) — webcam capture for new patient enrollment (drives `/register/multi`).
- [History](../frontend/src/pages/History.jsx) — date-driven session-history view, backed by `/history/...`. (Renamed from `Calendar` — the page is about patient session history; the calendar widget is just the date picker.)
- [Cameras](../frontend/src/pages/Cameras.jsx) — admin UI for the `/cameras` API (§3.12). Add / edit / delete RTSP feeds, probe a URL before saving, and bounce a worker without restarting the backend. Status pills are kept in sync via a coalesced WS-driven refetch on `CAMERA_DISCONNECTED` / `CAMERA_RECONNECTED`, with a 30 s safety-net poll.
- [CameraCard](../frontend/src/components/CameraCard.jsx) — single MJPEG tile.
- [HeaderSearch](../frontend/src/components/HeaderSearch.jsx), [SearchBar](../frontend/src/components/SearchBar.jsx).

**Key hooks:** [useSocket](../frontend/src/hooks/useSocket.jsx) (WebSocket lifecycle), `useAlertStore`, `useWebcams`.

**Performance:** `React.memo` on tile components; WebSocket events are throttled in [useSocket](../frontend/src/hooks/useSocket.jsx) to keep the render loop responsive under burst.

---

## 11. Failure Handling & Edge Cases

- **RTSP disconnects.** Worker enters an exponential-backoff retry loop; emits `CAMERA_DISCONNECTED` on the WS bridge. The next successful frame after reconnect bumps `stream_version` so the dashboard `<img>` re-mounts.
- **RTSP capture tuning.** [run_backend.py](../run_backend.py) sets `OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;udp|stimeout;5000000|fflags;nobuffer|flags;low_delay|max_delay;500000` before importing uvicorn. UDP is the default because TCP head-of-line blocking can cause stale frames on jittery links. Override per host with `;tcp` for a stable LAN where retransmission is preferable. The decoder hints keep buffering under 0.5 s. If H.264 errors persist, reduce the camera bitrate (target ≤4 Mbps for 720p).
- **AI worker crash.** **Today there is no automatic respawn loop in `manager.py`** — a dead AI worker stays dead until the FastAPI process restarts (or an operator hits `POST /cameras/{id}/restart` on a per-camera worker, which goes through `Registry`). A watchdog is on the roadmap; for now run under `systemd Restart=on-failure` and monitor `/api/v1/metrics`.
- **Brief detection gaps.** IoU/centroid tracker carries the identity for up to 2 s without re-detection.
- **Identity flicker.** Suppressed by the 5-frame majority-vote smoother before reaching the session service.
- **Unknown faces.** Held only as in-memory tracker IDs; not persisted, not added to FAISS.
- **Backend restart.** Sessions still marked INSIDE at boot are reconciled — stale ones are force-closed so the in-memory and DB states agree.
- **FAISS / name-map drift.** All paths are anchored to the project root in [backend/config.py:115-120](../backend/config.py#L115-L120), so launching uvicorn from any cwd reads/writes the same index file.

---

## 12. Monitoring & Observability

**Today.**

- `/api/v1/metrics` exposes pipeline counters (FPS, queue depths, detection counts).
- Python `logging` is gated by `SYNORA_DEBUG_LOG` ([backend/config.py:164](../backend/config.py#L164)) — `true` enables INFO/DEBUG; default keeps the root logger at WARNING. `print()` is intentionally **not** monkey-patched, so library/traceback output is preserved.
- Per-camera FPS and queue depths are logged periodically by the manager.

**Roadmap.** Prometheus exporter + Grafana dashboard (`ai_inference_time_ms`, `camera_fps_current`, `queue_depth`, `active_sessions_count`).

---

## 13. Deployment Architecture

**Today.** The repository ships the Python backend and Vite frontend source.
It does not include production deployment automation, systemd units, reverse
proxy configuration, backup scripts, or a container image. Deployers must
provide those operational components separately.

**Hardware.** Real-time multi-camera operation needs a CUDA-capable GPU. The reference low-end build (RTX 3050 4 GB + Ryzen 5 5600H + 16 GB RAM) handles 3–4 cameras comfortably; see [HARDWARE_AND_CAMERAS.md](HARDWARE_AND_CAMERAS.md) for per-tier capacity, optimal config values, and the upgrade ladder. CPU-only mode is supported via `GPU_DEVICE_ID = -1` but throughput drops to a few FPS — fine for one-shot `/recognize` calls, not for live streams.

**Roadmap.** Optional containerization (`Dockerfile.api` + `Dockerfile.pipeline` with CUDA base) for teams that prefer Docker over bare-metal systemd.

---

## 14. Security Considerations

> **Important — current state.** The API ships **without authentication**. There is no JWT validation, no API-key middleware, and no auth guard on `/users`, `/patients`, or `/register/multi`. **Do not expose the backend to an untrusted network.** Add an auth layer (FastAPI dependency + JWT middleware, or fronting reverse proxy with mTLS) before any production deployment.

Other properties:

- **RTSP credentials.** Currently embedded in `camera_master.stream_url` and editable through the admin API. Camera endpoints require `cameras.manage`; a future hardening pass should encrypt credentials at rest and redact them from responses and logs.
- **Privacy.** Raw frames are not persisted. Faces are stored only as 512-d embeddings in FAISS plus enrollment thumbnails under `database/faces/`. Embeddings cannot be inverted to reconstruct an image.
- **Secrets.** Read by Pydantic `BaseSettings` from `.env`; never committed.

---

## 15. Design Decisions & Tradeoffs

| Decision                                                | Why                                                    | Alternatives                              | Tradeoff                                                                        |
| ------------------------------------------------------- | ------------------------------------------------------ | ----------------------------------------- | ------------------------------------------------------------------------------- |
| InsightFace `buffalo_l` (RetinaFace + ArcFace)          | High accuracy, robust to angle/lighting                | YOLOv8-Face, MediaPipe                    | Heavier compute; really wants a GPU                                             |
| FAISS `IndexFlatIP` (in-memory) + Postgres for metadata | Exact cosine search, simple, fast on ≤ 100k identities | `pgvector`, IVF/HNSW indexes              | Doesn't scale to millions of identities; rebuild required if it grows           |
| Per-camera worker process + `multiprocessing.Queue`     | Simple isolation; bypasses GIL                         | Single shared AI worker + `shared_memory` | Frame pickling cost (mitigated by 960 px cap); duplicated model load per worker |
| Synchronous SQLAlchemy                                  | Simpler integration with existing services             | `asyncpg` + `AsyncSession`                | Blocks worker threads under heavy DB load                                       |
| MJPEG over WebRTC                                       | Trivial integration with `<img>` and OpenCV            | WebRTC                                    | Higher bandwidth; no audio; perfectly fine for LAN dashboards                   |
| Pre-annotate frames in worker                           | Guarantees visual ↔ event consistency                  | Annotate in browser from WS events        | Slightly more CPU in worker; far simpler to reason about                        |

---

## 16. Future improvements

The full roadmap lives in [ROADMAP.md](ROADMAP.md) — V2 architecture,
service-decoupling plan (Redis bus, FAISS across the boundary, WS
fan-out), scalability tiers, migration plan, risk register, Front
Desk backlog, file-level TODOs. Per-tier hardware capacity and the
L4 future-state target live in
[HARDWARE_AND_CAMERAS.md](HARDWARE_AND_CAMERAS.md).

Top of the queue, in priority order:

1. **Auth layer** — JWT + role-based access control on every route. **Highest priority before any external deployment.**
2. **Service decoupling** — split FastAPI and the camera stack into independently deployable services over a Redis bus. Replaces the FAISS mtime poll with a `cmd:refresh_faces` push. See [ROADMAP.md §8](ROADMAP.md).
3. **Containerisation** — `Dockerfile.api` + `Dockerfile.pipeline` (CUDA base) + `docker-compose.yml` covering Postgres, Redis, backend, and frontend (Nginx).
4. **AI-worker watchdog** — auto-respawn dead child processes (currently relies on the host service manager).
5. **FAISS scaling** — `IndexFlatIP` → `IndexIVFFlat` / `IVFPQ` factory once the registry passes ~5k identities; eventually pgvector. See [ROADMAP.md §1.1](ROADMAP.md).
6. **TensorRT + fp16 export** — compile `buffalo_l` to TensorRT engines for ~2× inference throughput; mandatory on 4 GB-VRAM cards.
7. **Cross-camera identity fusion** — explicit handoff/track stitching when a user walks between overlapping FOVs.
8. **Prometheus metrics** + Grafana dashboard for production observability.
9. **Batched embedding service** — single GPU worker amortising ArcFace across cameras (3–6× throughput on the same card).
10. **Async DB** — migrate to `asyncpg` + `AsyncSession`; add Alembic migrations.

---

## 17. Appendix

### Key configuration (verified, [backend/config.py](../backend/config.py))

| Setting                       | Value      | Notes                                      |
| ----------------------------- | ---------- | ------------------------------------------ |
| `EMBEDDING_DIM`               | 512        | ArcFace output                             |
| `SIMILARITY_THRESHOLD`        | 0.60       | Cosine cutoff for known/unknown (pipeline) |
| `FAISS_TOP_K`                 | 3          | Top-k aggregated by max score per identity |
| `MAX_EMBEDDINGS_PER_IDENTITY` | 10         | Bounds index growth                        |
| `SMOOTHING_WINDOW`            | 5          | Frames; majority-vote smoother             |
| `DETECTION_CONFIDENCE`        | 0.42       | RetinaFace cutoff (tuned from 0.5 for range)|
| `DETECTION_INPUT_SIZE`        | (800, 800) | Detector resize (tuned from 640 for range) |
| `MIN_FACE_SIZE`               | 24 px      | Smaller faces filtered out (was 40)        |
| `PROCESS_MAX_DIM`             | 960        | Long-edge cap before pipeline              |
| `CAPTURE_QUEUE_SIZE`          | 2          | Backpressure on capture                    |
| `MOTION_SKIP_THRESHOLD`       | 2.5        | Idle-scene gating                          |
| `MOTION_SKIP_MAX_INTERVAL_S`  | 2.0        | Forced run interval                        |
| `IOU_THRESHOLD` (tracker)     | 0.4        | [tracker.py](../backend/camera/tracker.py)    |
| `EXPIRE_SECONDS` (tracker)    | 2.0        | [tracker.py](../backend/camera/tracker.py)    |
| `GPU_DEVICE_ID`               | 0          | `-1` forces CPU                            |

### Terminology

- **Embedding.** 512-d L2-normalized float vector representing a face.
- **FAISS.** Facebook AI Similarity Search; here used as in-memory `IndexFlatIP`.
- **IoU.** Intersection over Union of two bounding boxes.
- **Buffalo_l.** InsightFace's reference model bundle (RetinaFace + ArcFace ResNet50 + auxiliary heads).
- **MJPEG (multipart/x-mixed-replace).** HTTP streaming format where each "part" is a standalone JPEG; supported natively by `<img>`.

---

_Verified against the codebase on 2026-05-25._
