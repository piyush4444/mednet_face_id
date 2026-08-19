# Iris — Multi-Camera Face Recognition & Patient Tracking

> A product of **Synora AI Labs**.

Real-time face recognition system for hospitals and clinics. Tracks
patients across multiple cameras and runs an OPD front-desk workflow
(scan → prefill OPD form → generate token → print slip → log everything).

## Features

- **Face registration & recognition** — InsightFace YOLO detector + ArcFace
  embeddings, indexed in FAISS, identities stored in PostgreSQL.
- **Multi-camera live tracking** — per-camera worker processes, MJPEG
  streams with detection overlays, WebSocket events for entry/exit.
- **Front Desk mode** — webcam or system-camera scan, prefilled OPD form
  with department/doctor/room selection, per-department daily tokens
  (`GEN-001`, `CARD-014`, …), printable thermal-slip layout with QR code,
  live "today's queue", manual MRN/phone/name search fallback.
- **Visit history** — every token is logged with status timeline; the
  History page surfaces both face-tracking sessions and OPD visits.
- **Settings hub** — manage rooms, departments, doctors, cameras and
  registered patients from a single `/settings` page.

## Tech stack

| Layer            | Choice                                          |
| ---------------- | ----------------------------------------------- |
| Backend          | FastAPI + Uvicorn (Python 3.10+)                |
| ORM              | SQLAlchemy 2.0 + PostgreSQL                     |
| Vector search    | FAISS                                           |
| Face models      | InsightFace (YOLO detector + ArcFace 512-d)     |
| Inference        | ONNX Runtime (CPU or CUDA)                      |
| Frontend         | React 19 + Vite + Tailwind v4 + MUI             |
| Realtime         | WebSocket (`/api/v1/ws/live`)                   |

## Repository layout

```
backend/                FastAPI app + multi-camera pipeline
  app/                  HTTP layer (routes, services, models, schemas)
  camera/               Per-camera worker manager
  engine/               Detector / aligner / embedder / matcher
  config.py             Central pipeline tuning
frontend/               React SPA (Vite)
docs/                   Architecture, configuration, hardware sizing
run_backend.py          Convenience launcher (FastAPI + optional cameras)
run_frontend.py         Convenience launcher (Vite dev server)
```

In-depth design notes live in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
The full doc set has its own index at [`docs/README.md`](docs/README.md).

## Quick start

Use [`docs/LOCAL_SETUP.md`](docs/LOCAL_SETUP.md) for the complete setup,
including prerequisites, PostgreSQL, CPU/GPU dependencies, authentication,
startup modes, and troubleshooting. After setup, the two development commands
are:

```bash
python run_backend.py --no-cameras
python run_frontend.py --api-url http://127.0.0.1:8000/api/v1
```

## Environment variables

Copy `.env.example` for backend settings and `frontend/.env.example` for
browser settings. The authoritative variable reference is
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md); local-safe values are shown in
[`docs/LOCAL_SETUP.md`](docs/LOCAL_SETUP.md).

## Documentation

Doc set is organised one topic per file in [`docs/`](docs/) — see
[`docs/README.md`](docs/README.md) for the full index.

### How to read these docs

Pick the path that matches what you're trying to do. Each path is ordered — read top-to-bottom.

**🆕 New engineer onboarding (you've just cloned the repo):**

1. **This file** — features, tech stack, quick start. Get the app running locally first.
2. [`backend/README.md`](backend/README.md) + [`frontend/README.md`](frontend/README.md) — per-app dev workflow (install, run, test).
3. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — how the pieces fit: FastAPI process, camera workers, FAISS, Postgres, WS/MJPEG. Code-true and verified.
4. [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) — every env var and tuning constant. Bookmark this; you'll come back to it.
5. [`docs/CHANGELOG.md`](docs/CHANGELOG.md) — recent commits explain "why does the code look this way today." Skim the top few entries.

**🔌 Integrating against the API (external dev / second client):**

1. [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) — every endpoint, payload, error code, with curl/Python/JS examples and integration recipes.
2. The live Swagger UI at `http://<host>/docs` once the backend is running — always reflects the current code.

**📐 Sizing or buying hardware:**

1. [`docs/HARDWARE_AND_CAMERAS.md`](docs/HARDWARE_AND_CAMERAS.md) — per-tier capacity, what to buy, camera placement, range-extension levers, the NVIDIA L4 target.

**🗺️ Planning what to build next:**

1. [`docs/ROADMAP.md`](docs/ROADMAP.md) — V2 architecture, service decoupling, scalability tiers, Front Desk backlog.
2. [`docs/CHANGELOG.md`](docs/CHANGELOG.md) — to know what *already* landed.

**🏥 Sharing with a hospital partner / non-technical stakeholder:**

1. [`docs/PILOT_READINESS_BRIEF.md`](docs/PILOT_READINESS_BRIEF.md) — one-pager: what Iris does today, what the pilot will validate, the recommended camera.

### Direct links

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — full design (verified against code).
- [`docs/LOCAL_SETUP.md`](docs/LOCAL_SETUP.md) — complete local development setup.
- [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) — REST + WS + MJPEG reference with examples.
- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) — every tuning knob.
- [`docs/HARDWARE_AND_CAMERAS.md`](docs/HARDWARE_AND_CAMERAS.md) — what to buy, where to mount, per-tier capacity, L4 future-state.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — V2 architecture, service decoupling, scalability tiers, backlog.
- [`docs/PILOT_READINESS_BRIEF.md`](docs/PILOT_READINESS_BRIEF.md) — client-facing pilot summary.
- [`docs/CHANGELOG.md`](docs/CHANGELOG.md) — notable changes per commit.

## License

Proprietary — internal use only. Not for redistribution.
