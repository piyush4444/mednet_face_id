# Changelog

Notable changes per commit, newest first. Trivial UI relabels are
one-liners; medium and major changes carry context, file refs, and
rationale.

---

## 2026-08-19

### `<unreleased>` — refactor(identity): canonical users, global RBAC, single facility

- Human authentication now attaches optional credentials, one role, and permission overrides directly to the canonical `users` identity; recognized employees/doctors no longer receive an unrelated login record.
- Kiosks use separate `service_accounts`, keeping machine principals out of the human registry.
- Accounts & RBAC grants access to an existing employee/doctor. Role and permission enforcement now resolves from the same database catalog for HTTP, WebSocket, `/auth/me`, and management policy checks.
- Removed facility CRUD/switching and the dormant Facility Roles frontend. `facility_master` is a singleton internal anchor created with Mednet and the first superadmin by `python -m backend.scripts.seed_initial`.

### `<unreleased>` — refactor(core): enforce the singleton Mednet facility

- Removed the Facilities page, topbar facility selector, and facility choices from locations, cameras, kiosks, and setup flows. Internal foreign keys remain for relational and client-HIS compatibility.
- Replaced `/facilities` CRUD with read-only `GET /facility`; camera/location/kiosk services resolve the singleton automatically.
- Added the idempotent `backend.scripts.seed_initial` bootstrap using environment-configured facility and superadmin values.

### `<unreleased>` — chore(admin): shelve the Facility Roles management surface

- Removed Facility Roles from sidebar navigation and the active React route; `/mappings` now follows the standard unknown-route redirect to Live View.
- Unmounted `/api/v1/mappings` from the FastAPI router. The page, API client, route module, and mapping service remain in source for future work.
- Kept `person_facility`, version history, and internal mapping services active because kiosk punches, pre-registration, exports, and tracking logs depend on them.

---

## 2026-08-18

### `<unreleased>` — refactor(camera): move runtime roster from JSON to PostgreSQL

- `camera_master` now stores the complete capture contract (`code`, source type/URL, floor, role, active state, facility/location, and timestamps); stable camera IDs keep streams and historical tracking references compatible.
- `backend/camera/store.py` preserves the existing dictionary API while using short-lived SQLAlchemy transactions. A `data_migrations` marker guards the one-time `database/cameras.json` import, so deleting every DB camera cannot resurrect the legacy roster.
- Camera roles now propagate through a manager-backed shared map updated by `CameraRegistry`, replacing JSON mtime polling without adding database queries to the detection hot path.

---

## 2026-07-14

### `<unreleased>` — feat(admin): per-facility mapping management (PERSON_VISIT_MAPPING)
Admin UI + API for the centralized-identity model — what a person *is* per facility, their per-facility MRN, and visit counter.

- **Backend** ([mapping_service.py](../backend/app/services/mapping_service.py), [routes/mappings.py](../backend/app/api/routes/mappings.py)): `/api/v1/mappings` CRUD (guarded by `users.write`). Validates person_type ∈ UserType, visitor_subtype ∈ VisitorSubtype, rejects duplicate (person, facility) and duplicate per-facility MRN. List joins person + facility names.
- **Frontend** ([Mappings.jsx](../frontend/src/pages/Mappings.jsx)): "Facility Roles" page (People group) — filter by facility/type, inline edit of role/subtype/MRN/active, and an add-mapping modal with a person search (maps an existing person to another facility — the multi-facility payoff). Nav gated by `users.write`.

### `<unreleased>` — feat(kiosk): consolidated Kiosk admin section — central device config + logs
One admin "Kiosk" section for everything kiosk: central device registry/config and all kiosk logs. Gated by a new admin-tier `kiosk.manage` permission.

- **Central config** ([kiosk_devices](../backend/app/db/facility_models.py) table): register each kiosk (serial, name, facility, mode IN/OUT/AUTO, camera source, dup-window override, linked device login, last-seen). `GET /kiosk/config?serial=` lets a device pull its config on boot (device-side wiring is the next step). Scan updates `last_seen_at`.
- **Admin API** ([kiosk_admin.py](../backend/app/api/routes/kiosk_admin.py), `/kiosk-admin/*`, `kiosk.manage`): devices CRUD, `POST /devices/{id}/account` provisions + links a minimal kiosk login ([auth_service.create_kiosk_account](../backend/app/services/auth_service.py), shared with the CLI `--kiosk`), and read-only log lists `GET /activity|/preregs|/exports` (person + facility names joined). Retry reuses `/integrations/*`.
- **Admin page** ([Kiosk.jsx](../frontend/src/pages/Kiosk.jsx), sidebar → Kiosk): tabs Overview (KPI cards), Devices (register/edit/disable + provision login), Activity (who punched, IN/OUT), Pre-registrations (token + status + retry), Attendance exports (txn id + status + retry).
- New permission `kiosk.manage` (admin default). API_REFERENCE §2 map + §22.

### `<unreleased>` — feat(kiosk): device principal + IP-camera source (B2B restructure, Phase 4b)
Completes P4: the unattended kiosk now works under `AUTH_ENABLED`, and can use a registered IP/system camera instead of a webcam.

- **Kiosk device login**: the kiosk signs in once as a dedicated **kiosk device account** — a `staff` login revoked down to exactly `{kiosk.operate, streams.view}` (no PII reads, no front-desk). Provision: `python -m backend.scripts.create_account --kiosk --username kiosk-gate-1` (extends [create_account.py](../backend/scripts/create_account.py); `auth_service.create_account` gained optional `granted`/`revoked`). Kiosk frontend now sends the session cookie + `X-CSRF-Token` on mutations, and shows a login screen on any 401. New `GET /kiosk/setup-options` (kiosk.operate) feeds the setup pickers so the device account needs no admin-tier `/facilities` or `/cameras` reads.
- **IP / system camera source**: kiosk setup offers "this device's webcam" or a registered camera from the roster. System-camera path reuses the main pipeline — MJPEG display (`/stream/{id}`) + `/stream/{id}/snapshot` posted to `/kiosk/scan` (the Front Desk pattern) — so browsers never touch RTSP. Frontend-only; no backend capture code.
- Docs: API_REFERENCE.md §2 (device principal), §22 (setup-options + camera source).

### `<unreleased>` — feat(admin): locations admin — LOCATION_MASTER CRUD (B2B restructure, Phase 4a)
The hierarchical location model from Phase 1 gets its API + admin UI (the first slice of the admin-console phase; the auth-rbac merge is separate).

- **Backend** ([location_service.py](../backend/app/services/location_service.py), [routes/locations.py](../backend/app/api/routes/locations.py)): `/api/v1/locations` CRUD. Server-side invariants — parent must be same-facility, parent chain kept acyclic (cycle detection on create/re-parent), `location_type` ∈ FLOOR/CORRIDOR/ROOM/GATE/WARD/OTHER. Soft delete.
- **Frontend** ([Locations.jsx](../frontend/src/pages/Locations.jsx)): Settings → Locations — facility picker, depth-indented tree, add form with parent + type selectors, activate/deactivate. API_REFERENCE.md §24.

### `<unreleased>` — feat(integrations): outbound punch + pre-registration delivery to client HIS (B2B restructure, Phase 3)
Delivers queued staff punches and patient pre-registrations to the B2B partner's API. Dormant until the client URL/keys are set — nothing is sent to an unconfigured endpoint.

- **HTTP layer** ([client_api.py](../backend/app/services/client_api.py)): stdlib `urllib` (no new dependency), fail-closed enabled-checks, configurable auth (`CLIENT_API_AUTH_HEADER`/`_SCHEME` — Bearer default, X-API-Key-style supported), punch sent as the client's array-of-one, pre-reg response parsed for `preRegnId`/`tokenNo`/`queueSetupID`; a 2xx with `success:false` is treated as failure.
- **Export worker** ([export_worker.py](../backend/app/services/export_worker.py)): one daemon thread (started/stopped in the lifespan) drains `punch_export_queue` + retryable `pre_registration_log` rows. Claim-based (`status→SENDING`, atomic rowcount UPDATE) so no double-send across worker/inline/instances; exponential backoff (`EXPORT_BACKOFF_BASE`→`_CAP`), dead-letter after `EXPORT_MAX_ATTEMPTS`, stale-`SENDING` reclaim after `EXPORT_STALE_SECONDS`.
- **Inline pre-reg push**: `POST /kiosk/prereg` now pushes to the HIS synchronously when enabled and returns the real `tokenNo`; on failure the row is left `FAILED` with a retry time for the worker. New `attempts`/`next_retry_at` columns + `SENDING` status on `pre_registration_log` (idempotent boot migration).
- **Ops surface** ([routes/integrations.py](../backend/app/api/routes/integrations.py)): `GET /integrations/status` (enabled flags + queue counts), `POST /integrations/flush`, `POST /integrations/{exports,preregs}/{id}/retry` (reschedule a dead-letter). Gate behind the admin permission when feat/auth-rbac lands.
- **Env**: `CLIENT_API_AUTH_HEADER/SCHEME`, `EXPORT_POLL_INTERVAL/MAX_ATTEMPTS/BACKOFF_BASE/BACKOFF_CAP/BATCH_SIZE/STALE_SECONDS`. CONFIGURATION.md §2, API_REFERENCE.md §23, ARCHITECTURE.md §3.14.

### `<unreleased>` — feat(kiosk): entry-gate kiosk — scan loop, punches, pre-registration (B2B restructure, Phase 2)
Separate-URL kiosk frontend + backend. The kiosk cycles: face scan → punch → greeting → back to scan.

- **Backend** ([routes/kiosk.py](../backend/app/api/routes/kiosk.py), [kiosk_service.py](../backend/app/services/kiosk_service.py)): `POST /kiosk/scan` (largest-face recognition → punch: mapping get-or-create, `KIOSK_DUPLICATE_WINDOW` dedup (default 120 s), `visit_count` increment on IN, tracking-log row; EMPLOYEE/DOCTOR punches enqueue a client-shaped `punch_export_queue` row with `biometricIDX` idempotency key); `POST /kiosk/prereg` (registry write-back + client-shaped payload stored PENDING in `pre_registration_log`); `GET /kiosk/mrn/new` (unique random MRN for the form's Generate button). Response separates `faces_detected` from `matched` so the UI only shows "visit the front desk" for unknown *faces*, not empty frames. Input hardening mirrors `/register/multi` (size cap, magic-byte sniffing, enum validation); kiosk responses expose a deliberate subset of the registry, and these routes need gating when feat/auth-rbac lands (kiosk device principal).
- **Frontend** ([kiosk.html](../frontend/kiosk.html), [src/kiosk/](../frontend/src/kiosk/)): second Vite entry (`build.rollupOptions.input`) → own URL/bundle (~5 kB gzipped, no router/MUI/WS). One-time device setup (camera via enumerateDevices, facility, IN/OUT mode, kiosk serial → localStorage), continuous scan loop (~1.5 s cadence, paused during greetings), welcome/goodbye panels with punch time + visit number, patient buttons (pre-register / visiting someone else), prefilled pre-registration form with MRN Generate and Cash/General token type, unknown-face front-desk message after 2 consecutive unknown scans. Double-click ⚙ to reconfigure.
- **Env**: `KIOSK_DUPLICATE_WINDOW` (CONFIGURATION.md §2). API_REFERENCE.md §22, ARCHITECTURE.md §3.14.

### `<unreleased>` — feat(core): multi-facility data model + facilities admin (B2B restructure, Phase 1)
First phase of the client/B2B-partner restructure (centralized identity, punch IN/OUT attendance, patient pre-registration into the client HIS). Schema + admin surface only — the kiosk, punch pusher and pre-registration forwarder land in later phases.

- **New tables** ([backend/app/db/facility_models.py](../backend/app/db/facility_models.py)): `facility_master` (sites + client-HIS identifiers: facilityGuid, companyID, `integration_config` JSONB), `location_master` (hierarchical in-facility places, schema only), `person_visit_mapping` (person × facility — role/MRN/visit-counter are now **per facility**, one row per pair), `person_tracking_logs` (append-only IN/OUT punches + TRACKER_IN/TRACKER_OUT zone sightings), `punch_export_queue` (idempotent outbound punch deliveries, `biometric_idx` unique), `pre_registration_log` (client `preRegnId`/`tokenNo` + raw payloads). See ARCHITECTURE.md §3.14.
- **Registry columns on `users`**: prefix, first/middle/last name, whatsapp, email, city/state/country/pin, national id (type+number, UID=Aadhaar/ABHA/…), next-of-kin trio, photo_url, and a unique `person_guid`. Person ids stay integers — the client punch contract requires numeric `biometricUID`, so FAISS keys are untouched.
- **Boot migration** (`init_db()`): idempotent column adds, `person_guid` + name-split backfill, seeds `Main Facility (MAIN)` and one mapping per existing user with `visit_count`/`last_visit_at` backfilled from `patient_sessions`.
- **Facilities admin**: `/api/v1/facilities` CRUD ([routes/facilities.py](../backend/app/api/routes/facilities.py), [facility_service.py](../backend/app/services/facility_service.py), soft delete) + Settings → Facilities panel ([Facilities.jsx](../frontend/src/pages/Facilities.jsx)). API_REFERENCE.md §21.
- **Env**: `CLIENT_PUNCH_API_URL/KEY`, `CLIENT_PREREG_API_URL/KEY`, `CLIENT_API_TIMEOUT` (empty = integration disabled); per-facility non-secret values live on the facility row. CONFIGURATION.md §2, `.env.example`.
- **Profile photos at registration**: the first face-bearing image of `POST /register/multi` (and `update-face/multi`) is saved as the user's DP ([media_service.py](../backend/app/services/media_service.py)) and returned/served as `photo_url`. Security posture: image is **re-encoded** to a clean JPEG (EXIF / embedded payloads discarded), filename is the random `person_guid` (no id enumeration), write-then-rename (no torn files), `database/media/` gitignored (biometric PII). Served at `/media/*` (FastAPI StaticFiles) until a media server exists — then set `MEDIA_BASE_URL` and resolved URLs move with zero data migration (DB stores relative paths). Like all routes today `/media` is unauthenticated; the feat/auth-rbac merge must gate it. New env: `MEDIA_DIR`, `MEDIA_BASE_URL`. New `users.person_guid` now defaults to `uuid4()` on insert.

## 2026-07-11

### `<unreleased>` — ops: GPU (L4 VPS) deployment — runtime auto-detect, CUDA lib path, manual deploy trigger
Production moves from the CPU office box to a GPU (NVIDIA L4) VPS. Changes across deploy + CI:

- **Runtime auto-detect**: `deploy/setup.sh` and `deploy/scripts/update.sh` now default `SYNORA_RUNTIME` by probing `nvidia-smi` (`gpu` if it runs cleanly, else `cpu`). Explicit `SYNORA_RUNTIME=cpu|gpu` still overrides. Fixes the trap where the CI runner's `sudo synora-update` silently installed CPU deps on a GPU host.
- **CUDA/cuDNN library path (Linux)**: the pip `nvidia-*-cu12` wheels live in `site-packages/nvidia/*/lib`, which `ld.so` doesn't search; the in-code shim in `engine/pipeline/embedding.py` only covers Windows. setup.sh (and synora-update on dep changes) now writes a systemd drop-in `synora-backend.service.d/gpu.conf` setting `LD_LIBRARY_PATH` to the wheel lib dirs; removed when the runtime flips back to CPU.
- **Manual-only deploy**: `.github/workflows/deploy.yml` now triggers solely on `workflow_dispatch` — Actions → Deploy → "Run workflow". The office-server-era auto-deploy after CI (`workflow_run`) is removed; pushing to `main` runs CI but never ships. DEPLOY.md runner examples renamed `synora-office` → `synora-vps`.
- **CI**: new `backend-gpu-deps` job resolves `backend/requirements.txt` with `pip install --dry-run` so a broken GPU pin fails CI instead of mid-deploy (hosted runners have no GPU; runtime health stays with the deploy health check).
- Docs: DEPLOY.md §3.2/§9.2/§9.3, deploy/README.md updated; `requirements.txt` CUDA-wheel comment corrected (was "Windows via pip" — the wheels serve Linux too).

### `<unreleased>` — fix(deploy): two GPU-install traps found on the first L4 VPS bring-up
- **onnxruntime-gpu pinned `<1.23`**: 1.27 links `libcudart.so.13` (CUDA 13) and fails to import against the pinned `nvidia-*-cu12` wheels. 1.22.0 verified on the production L4.
- **CPU-wheel collision repaired**: insightface depends on the CPU `onnxruntime` package; pip installs it beside `onnxruntime-gpu` and clobbers the shared module files, silently downgrading inference to CPU (`Applied providers: ['CPUExecutionProvider']`). setup.sh and synora-update now uninstall the CPU wheel and force-reinstall the GPU wheel (`--no-deps`) after any GPU dependency install.
- Also from the same bring-up: setup.sh re-runs now `ALTER ROLE` the Postgres password to match the freshly written `.env`, rsync excludes `venv/`, and root is allowed to run setup.sh (fresh VPS images).

### `<unreleased>` — fix(camera): live-reload camera roles so entry sessions work without a restart
Root cause of "user detected on entry cam but no session/history created": [backend/camera/utils/camera_roles.py](../backend/camera/utils/camera_roles.py) built its role map **once at process boot**, so any camera added or re-roled via the admin API after boot resolved to the default role `inside` in the EventProcessor — `[ENTRY BLOCKED]`, no `patient_sessions` row, empty history. `HAS_EXIT_CAMERA` had the same boot-time-snapshot problem (documented as a known limitation in ARCHITECTURE.md §3.12, now lifted).

- `camera_roles.py` rewritten: role map rebuilds when `cameras.json` mtime changes (stat throttled to 1 s on the hot path) — the same reload pattern the AI workers use for the FAISS index. New `has_exit_camera()` replaces the `HAS_EXIT_CAMERA` module constant in [event_processor.py](../backend/camera/event_processor.py). On stat/read failure the last good map is kept.
- Role edits via `PATCH /cameras/{id}` now take effect within ~1 s, no restart; registry docstring, ARCHITECTURE.md §3.12, and CONFIGURATION.md updated to match.

---

## 2026-05-25

### `<unreleased>` — docs: full code-truth pass across every README + doc file
End-to-end verification sweep of every `.md` file in the workspace. Findings + fixes:

- **`backend/README.md`** — rewritten. Was missing entire route groups (`frontdesk`, `frontdesk_admin`), services (`relation_service`, `user_service`, `frontdesk_service`, `frontdesk_admin_service`, `token_service`), the `db/frontdesk_models.py` model file, and the engine `pipeline/{alignment,matching,zoom_trigger}.py`. Still referenced the now-deleted `backend/ARCHITECTURE.md`. WS path corrected to `/api/v1/ws/live`. API section now defers to `docs/API_REFERENCE.md` instead of duplicating an out-of-date list.
- **`deploy/setup.sh` — two real bugs fixed:**
  - `SYNORA_BRANCH` default was `master` but the repo's branch is `main`; a fresh `setup.sh` would fail at `git clone --branch master`.
  - §8 wrote a `frontend/.env` with `VITE_API_URL="/api/v1"` (dead code since `74721e1` made the committed `.env.production` win build-time precedence). Removed the write to stop confusing operators.
- **`docs/CONFIGURATION.md`** — `SIMILARITY_THRESHOLD` env-var description claimed it "overrides `backend/config.py` value if set" — it doesn't; `settings.SIMILARITY_THRESHOLD` has zero callers. Reworded to say it's dormant. Camera-add path updated to "Settings → Cameras" (the legacy `/cameras` URL now redirects). Frontend env section rewritten for the layered `.env.production` / `.env` / `.env.local` precedence.
- **`docs/ARCHITECTURE.md`** — five stale line-number citations in §3.2 through §3.6 (NORMALIZE_PIXELS, EMBEDDING_DIM, MAX_EMBEDDINGS_PER_IDENTITY, SMOOTHING_WINDOW, DETECTION_CONFIDENCE) repaired against current `backend/config.py`. §3.5 `SIMILARITY_THRESHOLD` claim rewritten: the "0.45 for /recognize" line was wrong because the Pydantic value is never read; the actual code uses `0.60` everywhere.
- **`deploy/README.md`** — `SYNORA_BRANCH` default table fixed (`master` → `main`); filesystem map updated to show `frontend/.env.production` (tracked) vs. `frontend/.env` (gitignored).
- **`docs/{ARCHITECTURE,HARDWARE_AND_CAMERAS,ROADMAP}.md`** verified-on dates bumped to today.

### `<unreleased>` — docs+ops: unified DEPLOY.md, all docs under `/docs`, first auto-deploy via self-hosted runner
First end-to-end production bring-up of the office server. Doc reorg + new CI/CD pipeline:

- **All project docs now live under `docs/`.** `DEPLOY.md` moved from the repo root to `docs/DEPLOY.md` (`git mv`, history preserved). Root keeps only `README.md`; app-local READMEs stay beside their components.
- **Deploy docs collapsed to one file.** `deploy/BACKUPS.md`, `deploy/MONITORING.md`, and `deploy/CICD.md` (~660 lines combined) deleted; their content folded into `docs/DEPLOY.md` §7–9 alongside the existing install / ops / decommission sections.
- **`backend/ARCHITECTURE.md` deleted** — 1493-line orphan dated "April 2026", no inbound references, fully superseded by `docs/ARCHITECTURE.md` + `docs/API_REFERENCE.md`.
- **New `DEPLOY.md` §11 troubleshooting/FAQ** captured every issue hit during the live bring-up: bundle baked with the loopback URL, updater not yet installed, recurring `.env` clobber, credential handling, runner permissions, queued jobs, and local-edit conflicts.
- **New Appendix C — "Deploying a similar project using this pattern"** documents the reusable stack (Tailscale → nginx → FastAPI + Postgres + static React on one host), the per-project substitutions, a 14-step bring-up checklist, and decisions worth keeping vs. reconsidering at scale.
- **First GitHub Actions self-hosted runner registered** on `synoraserver` as `synora-office`. CI on GitHub-hosted Ubuntu; on success, the Deploy workflow runs `sudo /usr/local/sbin/synora-update` on the host. `NOPASSWD` sudo scoped to the single binary; runner runs as a non-root operator user.
- **Cross-refs updated**: root `README.md`, `docs/README.md`, `docs/ARCHITECTURE.md`, `deploy/README.md`, `deploy/setup.sh`, and `.github/workflows/deploy.yml` all point at the new doc paths.

### `74721e1` — build(frontend): pin production API URL via tracked `.env.production`
Server's `frontend/.env` was tracked in git, so every `sudo synora-update` reset the API URL back to `127.0.0.1:8000` (the dev default) and the deployed bundle came up "Offline". Manual `tee` fixes on the host didn't survive the next pull.

- `frontend/.env.production` is now tracked with the public Funnel URL. Vite reads it automatically during `npm run build` (no env vars to remember on the host).
- `frontend/.env` is `git rm --cached`'d and added to `frontend/.gitignore` — the server's local copy is preserved on disk and survives future pulls.
- Root `.gitignore` updated: the old `!frontend/.env` exception swapped for `!frontend/.env.production`. `frontend/.env.example` updated to document the new layered-env precedence (`.env.local` → `.env.production` → `.env`).
- Server bring-up after this lands: one `sudo synora-update` cycle picks up the new `.env.production` automatically; no manual `.env` editing on the host from here on.

### `<unreleased>` — docs: production API reference
New [`docs/API_REFERENCE.md`](API_REFERENCE.md) — single-file, code-verified integration reference for external developers wiring against the backend. Twenty sections covering conventions, auth/CORS posture, error model, the unified user data model, every REST endpoint (health, registration, recognition, users + relations, legacy patients alias, tracking, history, cameras admin, front-desk scan + visits + admin, MJPEG stream + snapshot, WebSocket events, metrics), curl/Python/JS examples, three integration recipes (onboard a patient end-to-end, kiosk loop, WS subscribe), and production-deployment notes (auth at the proxy, `SIMILARITY_THRESHOLD` tuning, `START_CAMERA_SYSTEM`, FAISS save semantics, MJPEG bandwidth).

Two manual code-truth passes during authoring caught: `POST /register/multi` does not accept `mrn` (auto-generated server-side for PATIENT), `recognize` omits `user_id` on Unknown faces (vs. returning `null`), `PUT /users/{id}` routes legacy-demographic updates through `face_service` so a name change propagates to FAISS labels (PATCH does not), `/tracking/presence` `last_seen` is a Unix-epoch float not an ISO string, and `WS /api/v1/ws/live` is the correct path (the parent router carries the `/api/v1` prefix). `docs/README.md` index gains a row + an "External dev integrating the API" reading path.

---

## 2026-05-23

### `0bad133` — ui(frontdesk): move "Others in frame" to the left column
Operator feedback after `76c4d10` shipped: the strip was tucked inside the OPD form where it competed with the form fields, and the left column's white space below Manual Search was unused.

- `OthersInFrame` is now a stand-alone card in the left column, between Manual Search and the empty space below it.
- Outer style upgraded to match the other cards (`bg-card rounded-2xl shadow-md border`) and the row list height raised from `max-h-44` → `max-h-80` since the column has the room.
- `OPDForm` and `NonPatientCard` no longer receive candidate props; `scanResult.candidates` is read directly on the page where the swap handler also lives.

### `76c4d10` — feat(frontdesk): queue-aware scan with one-click candidate swap
Real-world OPD desks have 3–5 people stacked in the camera frame at once. The previous scan picked the highest-confidence face, which often was someone behind the person actually at the counter — operators had to re-scan or manually search.

- **[`backend/app/api/routes/frontdesk.py`](../backend/app/api/routes/frontdesk.py) `/frontdesk/scan`** re-ranked: identified faces now sort by **bbox area** (largest = closest to camera = person at the counter) with recognition confidence as a tie-break. The auto-picked primary remains the top-level `patient` / `confidence` (backwards-compatible). New response fields:
  - `candidates[]` — every identified face, primary first, each enriched with the full `_patient_summary` so the frontend can swap without a re-scan. Carries `bbox_area`, `confidence`, and `is_primary`.
  - `unrecognised_count` — detected-but-Unknown faces in the same frame, surfaced so the operator knows the camera saw more people than were recognised.
  - Candidate users are batch-fetched in one query (no N+1 when the frame has 5 people).
- **[`frontend/src/pages/FrontDesk.jsx`](../frontend/src/pages/FrontDesk.jsx)** new `OthersInFrame` sub-component. Shows every non-primary candidate with type badge, MRN/role/specialty/purpose subtitle, and recognition %. One click swaps `scanResult.patient` + `scanResult.confidence` in place via a new `handlePickCandidate` on the page; toast confirms the swap. The `candidates` list stays intact across swaps so the operator can flip between people freely. (Initially placed inside OPDForm/NonPatientCard; relocated to the left column in `0bad133`.)

Edge cases covered: single face → strip hidden; all-Unknown → no candidates list shown (matched=false, existing behaviour); mix → known faces clickable + "+N unknown" tag; non-PATIENT picked (e.g. a doctor walking past the queue) → renders the existing `NonPatientCard`, swap still available from the left-column strip.

### `b506d49` — chore: refresh runtime FAISS state + Synora logo asset
`database/face_index.bin` + `database/name_map.json` rolled to the current dev-DB state (kept under version control per team-sharing preference — other team members clone and have a working registry without re-enrolling). `frontend/src/assets/Synora Logo.png` updated.

### `0e44994` — docs: consolidate /docs into 7 topic-focused files + add CHANGELOG
Merged 9 docs in `docs/` down to 7. New:
- [`HARDWARE_AND_CAMERAS.md`](HARDWARE_AND_CAMERAS.md) — merges `HARDWARE_SIZING_GUIDE` + `CAMERA_AND_RANGE_GUIDE` + the `FUTURE_IMPROVEMENTS` Appendix B hardware cheatsheet into one "what to buy, where to mount it, how to scale" doc.
- [`ROADMAP.md`](ROADMAP.md) — merges `FUTURE_IMPROVEMENTS` + `SERVICE_DECOUPLING_PLAN` (now §8 within the roadmap) into one V2 + backlog doc.
- [`CHANGELOG.md`](CHANGELOG.md) — this doc. Per-commit log, newest first; trivial UI tweaks one-liner, medium/major changes get file refs + rationale.
- [`README.md`](README.md) — new 1-page docs index.

Renamed `SYSTEM_ARCHITECTURE.md` → `ARCHITECTURE.md` via `git mv` (history preserved); §16 trimmed to reference `ROADMAP.md`. Deleted: `CAMERA_AND_RANGE_GUIDE.md`, `FUTURE_IMPROVEMENTS.md`, `HARDWARE_SIZING_GUIDE.md`, `SERVICE_DECOUPLING_PLAN.md`, `USER_MODEL_EXPANSION_PLAN.md` (content folded into the merged docs and the per-phase entries above).

Code-truth pass while reorganising:
- `ARCHITECTURE.md` §1 / §3.10 / §3.13 / §9 updated for the user-model expansion: `Patient` → `User` table, `UserRelation` documented, `Doctor` table noted as merged into `users` with `user_type='DOCTOR'`, `/users/*` listed as canonical with `/patients/*` as deprecated alias, OPD-token user-type guard called out.
- `ARCHITECTURE.md` §17 appendix: `DETECTION_CONFIDENCE` corrected `0.4` → `0.42` to match `backend/config.py`.
- `HARDWARE_AND_CAMERAS.md` flags that actual `backend/config.py` defaults are tuned for the **Mid** tier (`EXPECTED_MAX_CAMERAS=8`, `MAX_TOTAL_FACES=30`, `MAX_FACES_MAX=20`, `GPU_SCALING_FACTOR=2.0`); the "small box profile" snippets in the old guide are manual overrides, not in-code defaults.
- Cross-refs in `README.md`, `DEPLOY.md`, `backend/app/db/models.py`, `backend/app/db/frontdesk_models.py`, `backend/app/services/user_service.py`, and the comment in `backend/camera/event_processor.py:41` updated to the new doc names.

Also added `scripts/md_to_docx.py` — minimal Markdown → .docx converter (python-docx based) used to produce the pilot brief docx.

### `0b0af75` — feat: user-model expansion phases 3–8 (UI + remaining backend)
Phases 1 (`e48d4f7`), 1b (`8dab1b5`), and 2 (`c670f7f`) shipped earlier. This commit lands the user-type-aware UI and the backend extensions that feed it — Register / Manage Users / Front Desk / Profile / Dashboard / History all become type-aware, and the legacy `/patients/*` callers move to `/users/*`.

- `Register.jsx` gains a user-type selector with conditional sub-forms; relatives pre-link patients via a debounced `/users?type=PATIENT&q=…` picker and POST to `/users/{new_id}/relations` after creation.
- `register.py` `/register/multi` accepts `user_type` + role / specialty / staff_department / opd_department_id / opd_room_id / purpose / note; `face_service` skips MRN allocation for non-PATIENT (single insert, no retry).
- `Patients.jsx` (filename preserved to avoid churning imports) becomes Manage Users: filter tabs (All / Patient / Doctor / Employee / Visitor / Relative), free-text search, type badges per row, type-aware edit modal, inline relations panel; submit switched `PUT` → `PATCH` so the extended field set lands via `update_user`.
- `frontdesk.py` `_patient_summary` carries `user_type` + type-specific fields; `/search` candidates include `user_type`. `FrontDesk.jsx` branches `OPDForm` for patients vs. `NonPatientCard` (badge + chips + "View profile" link, no token) for doctors / employees / visitors / relatives; manual-search rows get a type badge.
- `PatientProfileCard.jsx` header shows type badge + inactive pill; demographics branch per `user_type` so non-patient profiles drop empty Age/DOB/Department. `PatientProfile.jsx` adds a `RelationsPanel` between identity and OPD history (clickable partner links with relation chip + note); backend `_serialize_patient` extends with the new fields and `GET /patients/{id}` embeds `relations`.
- `pipeline_runner.py` `_resolve_identity()` returns `(name, user_type)` with caching; `tracker.py` keeps `user_type` sticky alongside identity so flicker frames don't drop the type. Dashboard "Detected" sidebar shows a small type chip for non-patients. History page gains a per-type filter strip with counts (empty buckets hidden), per-row badges, and a `filtered/total` counter when a filter is active.
- `PatientProfile.jsx` fetch switched from `/patients/{id}` to `/users/{id}` with a field-alias shim (`status`/`floor`/`camera_id`); `routes/patients.py` annotated deprecated and kept as a safety net for external integrators.
- Tailwind v4 lint touch-ups (no behaviour change): `bg-gradient-to-r` → `bg-linear-to-r` in the profile-card header, `break-words` → `wrap-break-word` on `Field`, `DatePicker` import added on `History.jsx` for the planned date-jump shortcut.

### `44b61c5` — fix(db): drop opd_visits FK before retargeting doctor_id
Phase 1b doctor-unification migration UPDATEd `opd_visits.doctor_id` to the new `users.id` **before** dropping the FK that still pointed at `doctors(id)`. PostgreSQL validated the UPDATE against the old FK target and aborted with `psycopg2.errors.ForeignKeyViolation: Key (doctor_id)=(15) is not present in table "doctors"` on first boot after Phase 1b.

Fix: reorder the migration in [`backend/app/db/postgres.py`](../backend/app/db/postgres.py) — insert doctor rows into `users`, drop the old FK (discovered via `information_schema`), retarget `opd_visits.doctor_id`, add the new FK pointing at `users(id)`, drop the legacy `doctors` table. Block remains idempotent via `inspector.has_table("doctors")` — runs once on first boot after upgrade, skipped on every boot after.

Note: this commit also inadvertently swept in the staged `git mv`/`git rm` operations for the doc consolidation (renames + deletions of the five merged docs). The reverts are mechanical; full rationale is in `0e44994` above.

---

## 2026-05-22

### `ded4195` — docs(pilot brief): expand validated list + trim L4 roadmap row
Pilot brief Capability table grew to mention user management + session/visit tracking history. Already-validated list expanded with multi-cam load (2–3 USB + 3–4 IP cams), 8–12 people per frame, and lighting variation bullets. Removed the "Higher-capacity server (NVIDIA L4 GPU)" roadmap row that read like marketing rather than a concrete next step.

### `193a946` — docs(pilot brief): drop deployment plumbing + reframe audience line
Removed the self-hosted / Tailscale deployment plumbing from the brief (out of scope for a partner read), dropped the "Audience:" line and "plain-language" framing, replaced "stakeholders" wording with a more inclusive non-tech-team phrasing.

### `202d603` — docs: client-facing pilot readiness brief
New `docs/PILOT_READINESS_BRIEF.md` — one-pager for sharing with a hospital partner. Sections: what Iris does today, what has been validated end-to-end vs what needs the pilot environment to exercise, the three-sentence pilot bring-up plan, the recommended Hikvision DS-2CD2T43G2-4I (6 mm) camera, installation guidance, and how to add the camera to Iris. Intentionally non-technical. Companion `scripts/md_to_docx.py` (minimal Markdown → .docx converter, python-docx based) produces the `.docx` next to it.

### `d10c97d` — ui: relabel "RTSP camera" → "IP camera"
Camera admin labels switched from technical ("RTSP") to operator-friendly ("IP camera").

### `c670f7f` — feat(api): user model expansion — Phase 2 (services + /api/v1/users)
New service layer + HTTP surface for the unified user model.
- New `backend/app/services/user_service.py`: `list_users(user_type, q, is_active, limit)`, `get_user`, `create_user` with `_validate_required_by_type` (PATIENT requires mrn, DOCTOR requires opd_department_id, EMPLOYEE requires role), `update_user`, `soft_delete_user`, `user_to_dict`.
- New `backend/app/services/relation_service.py`: `list_relations` with both-sides JOIN returning partner name/type, `add_relation` (self-link / duplicate / valid-type guards), `remove_relation`.
- Extended `backend/app/api/routes/users.py` with the full CRUD surface plus `/users/{id}/relations` POST/DELETE and the legacy `/users/{id}/update-face/multi`.
- Pydantic schemas in `backend/app/schemas/user.py`: `UserCreate` / `UserUpdate` / `UserResponse` / `UserDetailResponse` / `UserRelationCreate` / `UserRelationResponse`; `PatientBase`/`Update`/`Response` retained as aliases.

### `8dab1b5` — feat(db): user model expansion — Phase 1b (doctor table unification)
Removed the standalone `Doctor` model. `OPDVisit.patient_id` and `OPDVisit.doctor_id` now both FK to `users.id` with explicit `foreign_keys=` on the relationships. `init_db()` extended with a doctor-migration block that copies each row into `users` with `user_type='DOCTOR'`, retargets every `opd_visits.doctor_id` to the new id, swaps the FK from `doctors(id)` to `users(id)`, and drops the legacy `doctors` table. Block is idempotent (skipped when `doctors` no longer exists).

### `944b280` — fix(db): pull frontdesk_models into models so camera workers can flush
Phase 1 boot exposed an FK error in the camera-worker process: `Foreign key associated with column 'users.opd_room_id' could not find table 'rooms'`. Camera workers only import `models.py`, not `frontdesk_models.py`, so the `rooms` / `departments` tables weren't registered on `Base.metadata`. Fix: side-effect import `from backend.app.db import frontdesk_models` at the bottom of `models.py`, and the SQLAlchemy alias `Patient = User` so legacy import sites keep working.

### `e48d4f7` — feat(db): user model expansion — Phase 1 (schema + ORM)
- `backend/app/db/models.py`: `Patient` class renamed to `User`; new `UserType` enum (PATIENT / DOCTOR / EMPLOYEE / VISITOR / RELATIVE); new `RelationType` enum (SPOUSE / PARENT / CHILD / SIBLING / GUARDIAN / OTHER); new columns on `users` (`user_type`, `role`, `specialty`, `staff_department`, `opd_department_id`, `opd_room_id`, `purpose`, `note`, `is_active`); new `UserRelation` model. `Patient = User` module-level alias preserves backward compatibility.
- `backend/app/db/postgres.py`: `init_db()` extended with pre-`create_all` rename `patients → users` (PostgreSQL preserves FK constraints by OID across `RENAME TABLE` — `patient_sessions.patient_id` and `opd_visits.patient_id` follow the rename without intervention) and a post-`create_all` column-patch block that adds the new columns on existing installations, drops the legacy `NOT NULL` on `mrn`, and adds the supporting indexes.

### `2021226` — docs: user model expansion plan + Manage Users label
Original `USER_MODEL_EXPANSION_PLAN.md` design doc (now removed in favour of these changelog entries) + the precursor UI relabel "Manage Patients" → "Manage Users" in `frontend/src/pages/Settings.jsx`.

### `a920ec9` — chore: remove SERVER_HARDWARE_SPEC + fix broken doc links + untrack .claude
Workspace audit. `SERVER_HARDWARE_SPEC.md` removed (content absorbed into the camera/hardware guides). Broken doc cross-links repaired across `docs/`. `.claude/` directory untracked (per-user agent settings, not a project artefact).

### `ea588eb` — docs: procurement-ready hardware spec for 8-10 cam L4 pilot
Short-lived standalone `SERVER_HARDWARE_SPEC.md` for a procurement conversation. Superseded by `a920ec9` which folded it into the camera/hardware guides.

### `674df5b` — docs: production-tone rewrite of deployment + ops guides
Major rewrite of `DEPLOY.md`, `deploy/{BACKUPS,MONITORING,CICD,README}.md`, and the camera-range guide. Reduced casual phrasing, tightened scope per file, fixed §3.8 drift on `SYSTEM_ARCHITECTURE.md` (broadcaster section).

### `304cf61` — chore: rebrand product as Iris (by Synora AI Labs)
Product name in user-facing copy is now **Iris**; **Synora** is retained intentionally in infra namespaces (`/opt/synora/`, env vars, systemd units, repo URL slug) where it denotes the company-level deployment rather than the product. Touched: page titles, READMEs, docs front-matter, app meta tags.

### `525d839` — feat: 10-sample enrollment + camera/floor in search popover
- `MAX_SAMPLES` raised from 5 → 10 in the enrolment flow; the last 5 sample slots prompt the operator for distance-enrolled angles (not just close-ups). Backed by the range research in the camera/hardware guides — varied gallery improves distance recognition more than any model swap.
- HeaderSearch popover now surfaces the matched user's currently-detected camera and floor, not just identity, so the operator gets a "where are they right now" answer in one click.

### `9c5a3cc` — feat(stream): fan-out broadcaster so multiple clients can view a camera
Replaced the MJPEG single-consumer guard with a per-camera fan-out broadcaster. Multiple dashboards (or one dashboard + one front-desk terminal) can now view the same camera concurrently without one consumer starving another. Implementation: per-camera ring buffer + subscriber-list pattern in the streaming endpoint; back-pressure handled by drop-oldest on slow subscribers so a stuck client cannot stall the source feed.

### `68f6000` — docs: camera selection, installation & range-extension guide
Initial version of `CAMERA_AND_RANGE_GUIDE.md` (now folded into `HARDWARE_AND_CAMERAS.md`). Covered the camera selection matrix, mount geometry / lighting / network placement, range-extension levers grouped by category, and the NVIDIA L4 future-state profile.

---

## 2026-05-21

### `1499a6a` — docs: fix drift against current code + cross-link deploy/ guides
Doc drift pass — `SIMILARITY_THRESHOLD` value corrected, `DATABASE_URL` default fixed, `ALLOWED_ORIGINS` documents the comma-separated form. Added cross-links from `DEPLOY.md` into `deploy/{BACKUPS,MONITORING,CICD}.md` so operators don't have to dig.

### `08c641e` — ops: cleanup abandoned cloud configs + add backups/monitoring/CI-CD guides
Removed abandoned cloud-deploy artefacts (`Dockerfile`, `render.yaml`, `vercel.json`) that had been carried since stage-9 experiments but never wired into the live deployment. Added new operational guides:
- `deploy/BACKUPS.md` — daily Postgres dump + faces directory sync, restore drills, off-host copy via SSH-trusted backup user.
- `deploy/MONITORING.md` — Telegram sink for systemd OnFailure, external uptime check on a second tailnet node, host-metric watchdog.
- `deploy/CICD.md` — three-tier strategy (pre-merge checks / auto-deploy on merge / `update.sh` for ad-hoc pushes), rollback procedure.
- Helper scripts in `deploy/scripts/`: `backup.sh`, `healthcheck.sh`, `send-telegram.sh`, `update.sh`, `uptimepoke.sh`.

---

## 2026-05-20

### `a0ecb1f` — deploy: accept local repo path to skip SSH dance for synora user
`deploy/setup.sh` accepts `SYNORA_REPO_LOCAL_PATH=/path/to/clone` and rsyncs from that existing clone instead of `git clone`-ing as the `synora` system user. Removes the need to issue an SSH key to the unprivileged service account just to land code on the host.

### `8fc34bc` — deploy test_2 : synoraserver
Deploy iteration on the synora server host.

### `eba7c62` — feat: HF Spaces deploy config (Dockerfile + .dockerignore + README front matter)
Hugging Face Spaces deployment target — Dockerfile, `.dockerignore`, and the YAML front-matter HF needs in the README. Later abandoned (privacy concern: HF Spaces are public; this is a hospital-data project), but the artefacts persisted until cloud-config cleanup in `08c641e`.

### `d8271ad`, `5c425ee` — deploy test_1
Deploy iterations.

---

## 2026-05-18

### `343f5d9` — Delete .claude directory
Untracked the per-user Claude Code settings directory.

### `b421244` — github ready
Repo prep for the first private-org publication: README cleanup, `.gitignore` audit, secret scan, removal of one-off prototype files. This is the baseline that the changelog above builds on.

---

## Earlier

### `a37fb67` — front-desk added (2026-05-15)
Initial Front Desk / OPD workflow: face-scan → pre-fill OPD form → generate per-department daily token → print thermal slip with QR code → today's queue with status transitions. New tables: `departments`, `rooms`, `doctors` (later unified into `users`), `opd_visits`, `token_counters`, `visit_status_history`. New routes under `/api/v1/frontdesk/*` and `/api/v1/frontdesk/admin/*`.

### `9f3bf88` — checkpoint (2026-05-14)
Pre-front-desk checkpoint. Tracking pipeline + recognition + multi-camera dashboard + history page were stable here.

### `d749016` — temp (2026-04-29)
Mid-development checkpoint. The `(800,800)` detection input + `MIN_FACE_SIZE=24` + `DETECTION_CONFIDENCE=0.42` tuning trio for the Lenovo 300 FHD (95° HFOV) ultra-wide USB cam was applied around this point — it extended useful detection range from ~1.7 m to ~2.9 m.

### `e9bf069` / `06e9af7` — doc updates (2026-04-26)
Doc-only iterations on the early architecture and hardware guides.

### `stage 1` … `stage 10` (earlier — see `git log --grep "stage "`)
The original incremental development from a single-camera proof-of-concept (stage 1) through multi-camera streaming, FAISS dual-storage, the camera-manager process model, the per-camera process layout, the React frontend, and the WebSocket bridge (stage 10). Each "stage N" commit is a substantive milestone; refer to `git log --oneline` for the boundaries. The currently-shipped architecture is described in [`ARCHITECTURE.md`](ARCHITECTURE.md) — the stage commits are useful as a study path, not a source of truth.

---

_Maintained as commits land. Trivial UI / copy tweaks get one bullet;
medium and major changes get a paragraph with file refs._
