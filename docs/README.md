# Iris documentation index

This directory is one doc per topic. Start at the doc that matches
what you need.

## What's here

| Doc | What's in it | When to read |
|-----|--------------|--------------|
| [LOCAL_SETUP.md](LOCAL_SETUP.md) | PostgreSQL, Python CPU/GPU environment, backend/frontend startup, local authentication, and troubleshooting. | You're running the application on a development machine. |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Code-true engineering reference: components, data flow, AI/ML pipeline, tracking, communication layer, frontend architecture, design decisions. | You're working on the code and need to know how the pieces fit together today. |
| [API_REFERENCE.md](API_REFERENCE.md) | Production-grade REST + WS + MJPEG reference: every endpoint, every payload shape, curl/Python/JS examples, error table, integration recipes. | You're integrating an external system against the backend or building a custom client. |
| [CONFIGURATION.md](CONFIGURATION.md) | Every env var, every pipeline-tuning constant in `backend/config.py`, logging, CORS, camera-roster format. | You're changing how the system runs without changing the code. |
| [ATTENDANCE_INTEGRATION.md](ATTENDANCE_INTEGRATION.md) | Recognition decision rules, Mednet outbox delivery, retry/audit behavior, and rollout controls. | You're configuring or operating employee attendance punches. |
| [HARDWARE_AND_CAMERAS.md](HARDWARE_AND_CAMERAS.md) | What machine to buy, what camera to mount, where to mount it, range-extension levers, the L4 future-state target, faces-per-frame capacity, network/storage sizing. | You're sizing a deployment or picking hardware. |
| [PILOT_READINESS_BRIEF.md](PILOT_READINESS_BRIEF.md) | Client-facing summary of what Iris does today, what the pilot will validate, and the recommended Hikvision camera. Intentionally non-technical. | You're sharing a one-pager with a hospital partner or non-engineering stakeholder. |
| [ROADMAP.md](ROADMAP.md) | V2 architecture, the concrete service-decoupling plan (Redis bus, FAISS across the boundary, WS fan-out), scalability tiers, migration plan, risk register, Front Desk backlog, file-level TODOs. | You're planning what to build next. |
| [CHANGELOG.md](CHANGELOG.md) | Notable changes per commit, newest first. Trivial UI tweaks one-liner; medium/major changes carry file refs and rationale. The user-model rewrite (Phases 1–8) is folded in here. | You're investigating why a column / endpoint / behaviour looks the way it does, or catching up on what shipped recently. |

## What's not here

- **Short quick start** lives in the repo root [`README.md`](../README.md); use [`LOCAL_SETUP.md`](LOCAL_SETUP.md) for the complete local workflow.

## Reading paths

- **New engineer onboarding** → root `README.md` → `LOCAL_SETUP.md` → `backend/README.md` + `frontend/README.md` → `ARCHITECTURE.md` → `CONFIGURATION.md`.
- **External dev integrating the API** → `API_REFERENCE.md` (also live OpenAPI at `/docs`).
- **Hardware procurement** → `HARDWARE_AND_CAMERAS.md`.
- **Sharing with a hospital partner** → `PILOT_READINESS_BRIEF.md`.
- **Planning the next milestone** → `ROADMAP.md`.

## Document ownership

To avoid repeated instructions drifting apart:

- `LOCAL_SETUP.md` owns installation and local startup.
- `CONFIGURATION.md` owns environment variables and runtime tuning.
- `API_REFERENCE.md` owns endpoint contracts and integration examples.
- `ARCHITECTURE.md` owns component boundaries and data flow.
- `backend/README.md` and `frontend/README.md` keep only component-specific
  behavior and commands.
- `CHANGELOG.md` is historical; entries may mention components that were later
  removed and are not current operating instructions.

_Last reorganised: 2026-05-25. Each doc carries its own
"verified-against-code" date in its footer._
