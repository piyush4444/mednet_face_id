# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Iris** (a product of Synora AI Labs) — real-time multi-camera face recognition + OPD front-desk workflow for hospitals. FastAPI backend + React 19/Vite SPA + PostgreSQL + FAISS. See `README.md` and `docs/` for product/operator context; this file is for engineering only.

## Commands

### Run

```bash
# Backend (FastAPI + camera workers). From repo root.
python run_backend.py                  # autoreload + cameras on (default)
python run_backend.py --no-reload      # production-ish
python run_backend.py --no-cameras     # API only, no multi-camera workers
python run_backend.py --debug-log      # SYNORA_DEBUG_LOG=true
python run_backend.py --test-mode      # synthetic camera simulator

# Direct uvicorn equivalent
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000

# Frontend (Vite dev server)
python run_frontend.py                 # npm run dev (auto npm install if needed)
python run_frontend.py --mode build    # → frontend/dist
python run_frontend.py --mode lint
python run_frontend.py --api-url http://localhost:8000/api/v1
```

API: `http://127.0.0.1:8000/api/v1`. Swagger: `/docs`. Frontend dev: `http://localhost:5173`.

### Install

```bash
pip install -r backend/requirements.txt       # GPU (CUDA 12 + cuDNN 9) — default
pip install -r backend/requirements-cpu.txt   # CPU-only — also set GPU_ENABLED=False, GPU_DEVICE_ID=-1 in backend/config.py
cd frontend && npm install
```

### Lint / tests

Frontend: `cd frontend && npm run lint` (ESLint 9, flat config). There is no backend test runner configured — don't invent one; if you need to verify changes, run a focused script or hit the API.

## Critical invariants

These will bite you if ignored. They're spread across multiple files, so read this section first.

### Pipeline singleton

The detector + recognizer + FAISS index are owned by a **process-wide singleton** at `backend/app/services/pipeline_service.py`. **Never** instantiate `FaceDatabase` or load the recognizer anywhere else — doing so creates a divergent in-memory copy and writes silently fail to propagate to the canonical one. Access via `get_face_db()`, `get_recognizer()`, `init_pipeline()`.

### Don't combine `--reload` with `START_CAMERA_SYSTEM=1`

uvicorn's reloader will fork-bomb the camera child processes on every file save. `run_backend.py` defaults are fine; if invoking uvicorn directly with reload, pass `--no-cameras` or unset the env var.

### Don't use `uvicorn --workers >1` with cameras

The camera stack uses `multiprocessing` (frame grabbers, AI workers, event processor) with a `SharedMemory` frame pool. Multiple uvicorn workers each spawn their own camera children → resource contention. Scale via `NUM_AI_WORKERS` in `backend/config.py` instead.

### FAISS / Postgres dual storage

PostgreSQL is the relational truth (unified `users` table with `user_type` discriminator: PATIENT, DOCTOR, EMPLOYEE, VISITOR, RELATIVE). FAISS holds L2-normalised 512-d ArcFace embeddings persisted to `database/face_index.bin` + `database/name_map.json`. On boot, the lifespan loads FAISS from disk, rebuilds from DB embeddings if missing, and runs a reconciliation pass that drops FAISS keys for users no longer in Postgres. If you see "FAISS hit but no Postgres row" warnings, the two have drifted — restart clears it.

### `DETECTION_INPUT_SIZE` must be divisible by 32

RetinaFace uses FPN strides [8,16,32]; non-multiples produce "operands could not be broadcast" errors. Valid: 640, 672, 704, 736, 768, 800. NOT 720.

### Frontend `VITE_API_URL` is baked at build time

It's not read at runtime. `frontend/.env.production` (committed) drives `npm run build` on the server. If the public URL changes, edit `.env.production`, commit, push, redeploy.

### Camera roster lives in PostgreSQL `camera_master`, not env

Managed via the `/api/v1/cameras` admin API. `backend/camera/store.py` preserves the worker-facing dictionary contract while performing transactional database CRUD. A legacy `database/cameras.json` is imported only when the table is empty.

### `SIMILARITY_THRESHOLD` discrepancy

There are two copies — the effective one is in `backend/config.py` (0.60). A field exists in `app/core/config.py` (Pydantic Settings, 0.45) but is **not wired into the recognition path**. If you change the threshold, change `backend/config.py`.

## Architecture

### Two subsystems sharing one process

1. **FastAPI web server** (`backend/app/`) — REST + WebSocket + MJPEG. Single uvicorn process, thread pool for blocking work (e.g., `cv2.imencode` for MJPEG via `ThreadPoolExecutor`).
2. **Camera system** (`backend/camera/`) — spawned by the FastAPI lifespan iff `START_CAMERA_SYSTEM=1`. Per-camera `multiprocessing.Process` frame grabbers, one or more AI worker processes, an event processor, and a `SharedMemory` frame pool.

Both import the shared, stateless AI helpers from `backend/engine/` (detection, alignment, embedding, matching, FAISS).

### Camera subsystem flow

```
frame_grabber.py (per camera, process)
        │ frame handle via multiprocessing.Queue
        │ pixels via SharedMemory ring buffer
        ▼
ai_worker.py / pipeline_runner.py  (1+ processes, NUM_AI_WORKERS)
        │ detect → align → embed → FAISS match
        ▼
event_processor.py (single process — owns tracker & identity smoothing)
        │ DETECTION / SESSION / PRESENCE events
        ▼
event_bridge.py (thread inside FastAPI) → ws_manager → /api/v1/ws/live
```

Identity smoothing is centralised in `event_processor.py` so increasing `NUM_AI_WORKERS` scales inference without fragmenting tracker state. The pipeline runner also watches the FAISS index file mtime and reloads on change so new registrations are picked up by AI workers without restart.

### Backend layout

```
backend/
  app/                                 # FastAPI subsystem
    main.py                            # lifespan: init_db, init_pipeline, optionally spawn camera stack
    api/router.py                      # mounts all routes under /api/v1
    api/routes/                        # health, register, recognize, users, patients (legacy),
                                       # tracking, ws, stream, history, metrics, cameras,
                                       # frontdesk, frontdesk_admin
    core/config.py                     # pydantic-settings (DATABASE_URL, ALLOWED_ORIGINS, ...)
    db/models.py                       # User (unified), UserType, PatientSession, UserRelation
    db/frontdesk_models.py             # Department, Room, OPDVisit, TokenCounter, VisitStatusHistory
    db/postgres.py                     # engine, session, init_db (creates tables + idempotent one-shot migrations)
    services/
      pipeline_service.py              # THE singleton: detector, recognizer, FAISS
      face_service.py                  # register / recognize / update / delete
      user_service.py                  # unified-user CRUD
      frontdesk_service.py             # OPD visit lifecycle, queue, slip
      token_service.py                 # per-department daily token allocation
      session_service.py               # presence session IN/OUT
      event_bridge.py                  # cross-process queue → FastAPI WS
      ws_manager.py                    # WebSocket connection registry
  camera/
    manager.py                         # wires queues, Manager().dict() proxies, frame pool at lifespan start
    registry.py                        # runtime add/remove/restart under asyncio.Lock; emits CAMERA_RECONNECTED
    store.py                           # PostgreSQL camera-roster repository
    frame_grabber.py / worker.py       # per-camera RTSP/USB capture
    pipeline_runner.py / ai_worker.py  # inference loop; reloads FAISS on mtime change
    event_processor.py                 # tracker + identity smoothing + session lifecycle
    shared_frame_buffer.py             # SharedMemory frame pool
    frame_queue.py / event_queue.py    # bounded, drop-oldest
  engine/                              # stateless, imported by both subsystems
    pipeline/                          # detection, alignment, embedding, matching, zoom_trigger
    database/face_db.py                # FaceDatabase (FAISS IndexFlatIP + name_map)
    models/                            # InsightFace loader
  config.py                            # Hardware + pipeline tuning constants (see below)
```

`backend/config.py` holds everything tuning-related: GPU flags, frame sizes (`PROCESS_MAX_DIM=960`), queue sizes, detection params, similarity threshold, `NUM_AI_WORKERS`, motion-skip thresholds, path anchors. **Paths are anchored to the project root via `_PROJECT_ROOT`** so FAISS reads/writes to the same absolute location regardless of cwd. A relative path here causes silent drift (registrations vanish).

### Frontend layout

```
frontend/src/
  App.jsx                # Router + lazy routes + ToastContainer
  config.js              # API_URL + ENABLE_LOGGING resolution
  layout/MainLayout.jsx  # Shell: header, nav, SocketProvider, page slot
  pages/
    Dashboard.jsx        # Live camera grid + alerts (eager-loaded)
    Register.jsx         # Multi-image enrolment + user-type selector
    Patients.jsx         # "Manage Users" — kept by filename to avoid churning imports
    PatientProfile.jsx   # Any user type
    FrontDesk.jsx        # Scan → candidate strip → OPD form / NonPatientCard → slip + QR
    FrontDeskAdmin.jsx   # CRUD for departments / rooms / doctors
    Settings.jsx         # Tabbed: Manage Users · Cameras · Front Desk admin
    History.jsx          # Date-scoped presence + OPD visits
  hooks/
    useSocket.jsx        # SocketProvider + useSocketMessages — single WS per app
  store/                 # alertStore, connectionStore, searchStore
```

Routing: `/patients`, `/cameras`, `/frontdesk/admin` are all redirects to `/settings?section=…` for back-compat. Every non-Dashboard route is `React.lazy`-loaded.

### Live data paths the frontend uses

- **MJPEG**: `<img src="${API_URL}/stream/{camera_id}">`. Bounding boxes are drawn server-side; frontend just displays the multipart JPEG.
- **WebSocket**: single connection from `SocketProvider`, URL derived from `VITE_API_URL` (http→ws, https→wss; strips `/api/v1`, re-adds `/api/v1/ws/live`). Reconnects with exponential backoff (2s → 30s). Subscribe via `useSocketMessages(handler)`.
- Events today: `DETECTION`, `frontdesk.visit.created`, `frontdesk.visit.status_changed`.

### Unified user model (post Phases 1–8)

One `/users/*` API handles five types. UI implications you'll touch:
- `Register.jsx` has a type selector; sub-forms render conditionally. Relatives can attach to one or more patients via a debounced picker.
- `FrontDesk.jsx` branches `OPDForm` (patients) vs. `NonPatientCard` (everyone else).
- `PatientProfileCard` only renders demographics relevant to the user_type.

### Auth

**None ships with the backend.** Production deployments add auth at the reverse proxy. Don't add auth code into routes without confirming the deployment strategy first.

## Conventions

- Backend imports assume the repo root is on `sys.path`. `backend/app/main.py` inserts it at import time; `run_backend.py` also does. If you write a one-off script, prepend the repo root or `python -m`.
- New tables: register on `Base.metadata`, then they auto-create on next boot via `init_db()`. For non-trivial schema changes, write a one-shot idempotent migration in `db/postgres.py` (look at the patient→user rename and doctor unification for the pattern). Alembic is not currently used.
- Logging: gated by `SYNORA_DEBUG_LOG` env var (defaults off → root logger at WARNING). Do **not** monkey-patch `print()`; the comment in `backend/config.py` explains why.
- Frontend env: `.env.local` > `.env.production` (committed) > `.env` > `.env.example`. Production URL is committed in `.env.production`.

## Reference docs (in repo)

- `docs/ARCHITECTURE.md` — full design, verified against code with line refs.
- `docs/API_REFERENCE.md` — every endpoint + WS event with examples.
- `docs/CONFIGURATION.md` — every env var + tuning constant.
- `docs/DEPLOY.md` — self-host playbook + troubleshooting (§11).
- `docs/CHANGELOG.md` — why the code looks the way it does today.
- `backend/README.md`, `frontend/README.md` — per-subsystem dev workflow (deeper than this file).


## Git commits 
When asked to commit changes, **do not add a `Co-Authored-By: Claude ...` trailer** or any other Claude/Anthropic attribution to the commit message. Commits should appear authored solely by the user.
