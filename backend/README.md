# Iris Backend

FastAPI backend for Iris — real-time face recognition + patient/user tracking. Combines a REST + WebSocket API for the React frontend with an opt-in multi-camera streaming subsystem (multiprocessing) that runs detection → embedding → FAISS matching → event broadcasting on live RTSP/USB feeds.

Two independent subsystems share the same AI models and FAISS store:

| Subsystem | Purpose | How it runs |
|---|---|---|
| **FastAPI Web Server** ([`app/`](app/)) | REST + WS API: register identities, recognise from uploads, manage users + OPD visits, stream MJPEG, push live events | `uvicorn` — single process, thread pool |
| **Camera System** ([`camera/`](camera/)) | Multi-camera live recognition (frame grabbers, AI workers, event processor) | Spawned at startup when `START_CAMERA_SYSTEM=1` |

The shared AI pipeline (Detect → Align → Embed → Match) lives in [`engine/`](engine/) and is imported by both. Full architecture in [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md); endpoint-level reference in [`../docs/API_REFERENCE.md`](../docs/API_REFERENCE.md).

---

## Tech stack

- **Web framework:** FastAPI + Uvicorn (with WebSocket support)
- **AI / ML:** InsightFace (RetinaFace + ArcFace) on ONNX Runtime, OpenCV
- **Vector search:** FAISS (`IndexFlatIP` — exact inner-product on L2-normalised 512-d embeddings; see [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) for the scaling roadmap to IVF/PQ)
- **Relational DB:** PostgreSQL via SQLAlchemy 2.0
- **Validation:** Pydantic v2 + pydantic-settings
- **Concurrency:** `multiprocessing` for the camera stack with a shared-memory frame pool

---

## Setup

Use [`../docs/LOCAL_SETUP.md`](../docs/LOCAL_SETUP.md) for prerequisites,
PostgreSQL creation, virtual environments, CPU/GPU installation, `.env`
configuration, and first-account creation.

The runtime camera roster lives in PostgreSQL `camera_master` and is managed via the `/api/v1/cameras` admin API — not via env vars. `database/cameras.json` is only a one-time legacy import source when the table is empty.

> Pipeline-tuning constants (detection size, similarity threshold, FPS caps, etc.) are in [`config.py`](config.py) — see [`../docs/CONFIGURATION.md`](../docs/CONFIGURATION.md) for the full list. A `SIMILARITY_THRESHOLD` field also exists in `app/core/config.py` (Pydantic Settings) but is **currently unused** by the recognition path; the effective threshold is `SIMILARITY_THRESHOLD = 0.60` in `config.py` ([`../docs/API_REFERENCE.md`](../docs/API_REFERENCE.md) §7 notes the discrepancy).

### Database initialisation

On startup, the lifespan hook calls `init_db()` which creates any missing tables registered on `Base.metadata` and runs idempotent one-shot migrations (patient→user rename, doctor table unification — see `db/postgres.py`). Safe to re-run. For production migrations beyond bootstrap, layer Alembic on top.

### Running

```bash
python run_backend.py --no-cameras
```

- Swagger: <http://localhost:8000/docs>
- Health: <http://localhost:8000/api/v1/health>

For configured cameras, use a single process without reload:

```bash
python run_backend.py --no-reload
```

Use `python run_backend.py --help` for host, port, synthetic-camera, and
logging options. Do not combine camera workers with reload for sustained use.

---

## Project structure

```text
backend/
├── app/                                # FastAPI subsystem
│   ├── main.py                         # App entry + lifespan (init_db, pipeline boot, camera spawn)
│   ├── api/
│   │   ├── router.py                   # Aggregates routes under /api/v1
│   │   └── routes/                     # health, register, recognize, users, patients (legacy),
│   │                                   # tracking, ws, stream, history, metrics, cameras,
│   │                                   # frontdesk, frontdesk_admin
│   ├── core/                           # Settings (pydantic-settings) + logging
│   ├── db/
│   │   ├── models.py                   # User (= Patient alias), UserType, RelationType,
│   │   │                               # PatientSession, UserRelation
│   │   ├── frontdesk_models.py         # Department, Room, OPDVisit, TokenCounter, VisitStatusHistory
│   │   ├── postgres.py                 # Engine, session, init_db with idempotent migrations
│   │   └── face_db.py                  # (legacy import shim — real FaceDatabase lives in engine/)
│   ├── schemas/                        # Pydantic request/response models (user, cameras, ...)
│   └── services/
│       ├── pipeline_service.py         # Singleton detector + recognizer + FAISS
│       ├── face_service.py             # register/recognize/update_face_multi/delete
│       ├── user_service.py             # CRUD for unified users table
│       ├── relation_service.py         # User-to-user relations (guardian, parent, etc.)
│       ├── patient_service.py          # Legacy patient lookups (used by deprecated /patients)
│       ├── frontdesk_service.py        # OPD visit creation, queue, slip, status transitions
│       ├── frontdesk_admin_service.py  # CRUD for departments / rooms / doctors (lookup tables)
│       ├── token_service.py            # Per-department daily token allocation
│       ├── session_service.py          # IN/OUT session lifecycle
│       ├── presence_cache.py           # In-process cache of cross-camera presence
│       ├── detection_cache.py          # Per-camera latest detections (for MJPEG overlay)
│       ├── event_bridge.py             # Camera WS queue → FastAPI WS broadcast
│       └── ws_manager.py               # Connection manager for /ws/live
├── camera/                             # Multi-camera streaming subsystem
│   ├── manager.py                      # Spawns frame grabbers, AI workers, event processor
│   ├── registry.py                     # Live camera registry (add/remove/restart at runtime)
│   ├── frame_grabber.py                # Per-camera RTSP/USB capture process
│   ├── ai_worker.py                    # Detection + embedding worker
│   ├── pipeline_runner.py              # AI worker main loop (also reloads FAISS on mtime change)
│   ├── event_processor.py              # Aggregates events → /ws/live; emits PRESENCE snapshots
│   ├── tracker.py                      # Identity smoothing + session lifecycle hooks
│   ├── store.py                        # PostgreSQL camera-roster repository
│   ├── shared_frame_buffer.py          # SharedMemory frame pool (cross-process)
│   ├── frame_queue.py / event_queue.py # Bounded queues with drop-oldest
│   ├── ws_throttle.py                  # Per-event-type throttle for WS broadcast
│   ├── metrics.py                      # Per-camera FPS / drop counters
│   └── services/                       # alert_service, db_service, tracking_service
├── engine/                             # Stateless AI helpers shared by app + camera
│   ├── pipeline/                       # detection, alignment, embedding, matching, zoom_trigger
│   ├── database/face_db.py             # FaceDatabase (FAISS IndexFlatIP + name_map)
│   └── models/                         # InsightFace model loader
├── config.py                           # Hardware + pipeline constants (GPU, FPS, sizes, thresholds)
├── requirements.txt                    # GPU build (CUDA 12 + cuDNN 9)
├── requirements-cpu.txt                # CPU-only build
└── README.md                           # This file
```

---

## API surface

Every REST endpoint, payload shape, and WebSocket event is documented with examples in [`../docs/API_REFERENCE.md`](../docs/API_REFERENCE.md). The live Swagger UI at <http://localhost:8000/docs> reflects the current code.

Top-level groups (all under `/api/v1`):

| Group | What | Auth |
|---|---|---|
| `health` | `GET /health` liveness probe | none |
| `register` | `POST /register/multi` — multi-image enrolment for any user type | none |
| `recognize` | `POST /recognize` — one-shot identification from an uploaded frame | none |
| `users` | Full CRUD + relations (`/users/{id}/relations`) for the unified user model (PATIENT/DOCTOR/EMPLOYEE/VISITOR/RELATIVE) | none |
| `patients` | **Legacy aliases** — back-compat for older integrators; new code uses `/users/*` | none |
| `tracking` | `presence`, `find`, `cameras`, `current`, `by-camera`, `summary` | none |
| `history` | `date/{YYYY-MM-DD}`, `patient/{id}` — session history | none |
| `cameras` | Admin CRUD + `restart`, `test` (RTSP probe) | none |
| `frontdesk` | OPD `scan`, `visits` lifecycle, `search`, per-date queries | none |
| `frontdesk/admin` | CRUD for departments / rooms / doctors lookup tables | none |
| `stream` | `GET /stream/{camera_id}` MJPEG; `/snapshot` single frame | none |
| `metrics` | Pipeline + per-camera metrics (`?format=json|prom`) | none |
| WebSocket | `/api/v1/ws/live` — DETECTION + frontdesk.visit.* events | none |

> Application authentication and RBAC are controlled by `AUTH_ENABLED`. A
> production reverse proxy and network access policy must be supplied separately.

---

## Dual-storage design

1. **PostgreSQL** — relational truth. One unified `users` table (with `user_type` discriminator) covers patients, doctors, employees, visitors, relatives. Sessions, OPD visits, and the lookup tables (departments / rooms) live here too.
2. **FAISS** — flat index of L2-normalised 512-d ArcFace embeddings, persisted to `database/face_index.bin` + `database/name_map.json`. One row per (user_id, sample) pair, up to `MAX_EMBEDDINGS_PER_IDENTITY` per user.

On boot, FAISS is loaded from disk; if missing it's rebuilt from any embeddings still in the DB. A reconciliation pass drops FAISS keys for users no longer in Postgres, preventing "FAISS hit but no Postgres row" warning storms when the two stores drift.

The pipeline (detector + recognizer + FAISS) is owned by a **singleton** in [`app/services/pipeline_service.py`](app/services/pipeline_service.py). Never instantiate `FaceDatabase` or load the recognizer elsewhere — doing so creates a divergent in-memory copy and writes silently fail to propagate.

---

## Troubleshooting

General installation, database, CUDA, model-download, CORS, and process cleanup
problems are covered in [`LOCAL_SETUP.md`](../docs/LOCAL_SETUP.md#8-checks-and-troubleshooting).
Backend-specific checks:

| Symptom | Likely cause | Fix |
|---|---|---|
| `FAISS hit but no Postgres row` warnings | Stale FAISS keys | Restart the server — the startup reconciliation pass clears these |
| `psycopg2` build failure on install | Pip resolved `psycopg2` instead of `psycopg2-binary` | Force the binary variant — the pinned wheel is `psycopg2-binary` |

## See also

- [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) — full subsystem breakdown.
- [`../docs/API_REFERENCE.md`](../docs/API_REFERENCE.md) — every endpoint with examples.
- [`../docs/CONFIGURATION.md`](../docs/CONFIGURATION.md) — every env var + tuning constant.
