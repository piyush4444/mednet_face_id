# Backend Configuration Reference

The Iris backend (by Synora AI Labs) reads configuration from four places. This doc lists
every knob, what it does, where it lives, and when you'd change it.

| Source                                          | Scope                                 | Hot-reload?                  |
| ----------------------------------------------- | ------------------------------------- | ---------------------------- |
| Environment variables / `.env`                  | Per-process settings                  | No (read once at startup)    |
| `backend/config.py`                             | Pipeline tuning                       | No (Python constants)        |
| PostgreSQL `camera_master`                      | Per-camera roster                     | **Yes** — admin API          |
| Postgres tables `departments`, `doctors`, `rooms` | OPD front-desk config               | **Yes** — `/settings`        |

Front-desk config (departments, doctors, rooms, default-room mapping) is
intentionally DB-backed and managed from the **Settings → Front Desk**
page in the frontend. No file edit is needed to onboard a new
department; deactivation is soft (`is_active=False`) so historic
`opd_visits` rows stay valid. See §3.13 of [ARCHITECTURE.md](ARCHITECTURE.md).

For machine setup see [LOCAL_SETUP.md](LOCAL_SETUP.md). Launcher behavior lives
in [`run_backend.py`](../run_backend.py) and [`run_frontend.py`](../run_frontend.py).

---

## 1. Launcher reference

Use `python run_backend.py --help` and `python run_frontend.py --help` for the
complete option lists. The usual local commands are intentionally maintained in
[LOCAL_SETUP.md](LOCAL_SETUP.md) rather than repeated here.

---

## 2. Environment variables

All env vars are optional and read once at process start. Set them in
your shell or a `.env` file at the project root.

| Variable                  | Default                                       | Effect                                                                                                  |
| ------------------------- | --------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `START_CAMERA_SYSTEM`     | `0`                                           | `1` → spawn camera/AI/event-processor workers at FastAPI startup. `run_backend.py` sets this for you.   |
| `CAMERA_TEST_MODE`        | unset                                         | `1` → use the synthetic test simulator instead of real camera workers.                                  |
| `SYNORA_DEBUG_LOG`        | `false`                                       | `true` → root logger at DEBUG, gated `print()` blocks emit. `false` → WARN+ only.                       |
| `DATABASE_URL`            | `postgresql://postgres:postgres@localhost:5432/facedb` | SQLAlchemy connection string. Sync engine — point at your Postgres.                            |
| `BOOTSTRAP_FACILITY_NAME` | `Mednet`                                        | Facility name used by the idempotent initial seed. |
| `BOOTSTRAP_SUPERADMIN_NAME` | `Mednet Superadmin`                           | Canonical employee name for the first superadmin user. |
| `BOOTSTRAP_SUPERADMIN_USERNAME` | `superadmin`                                | Initial login username. |
| `BOOTSTRAP_SUPERADMIN_PASSWORD` | `""`                                      | Required only for `python -m backend.scripts.seed_initial`; minimum 8 characters. |
| `APP_NAME`                | `Face Recognition API`                        | Shown in `/` and OpenAPI metadata.                                                                      |
| `VERSION`                 | `1.0.0`                                       | Same.                                                                                                   |
| `DEBUG`                   | `false`                                       | When `true`, raises root log level to DEBUG (cooperates with `SYNORA_DEBUG_LOG`).                        |
| `HOST`                    | `0.0.0.0`                                     | Read by `backend.app.core.config.settings`; the run script overrides via `--host`.                       |
| `PORT`                    | `8000`                                        | Same; override via `--port`.                                                                            |
| `ALLOWED_ORIGINS`         | `["*"]`                                       | CORS allowlist. Comma-separated (`https://a.tld,https://b.tld`) or JSON-encoded list. **Tighten this in production.** |
| `AUTH_ENABLED`            | `false`                                       | Master switch for auth/RBAC enforcement. While `false`, the `/auth/*` endpoints exist but no route is guarded (demo-stable). Set `true` once the login UI ships so the Phase 2 permission guards start enforcing. |
| `SESSION_SECRET`          | `""` (empty)                                  | HMAC secret used to sign session cookies. **Required in production** — with `DEBUG=false` and an empty value, the auth layer refuses to mint/verify cookies rather than use a forgeable default. Generate with e.g. `python -c "import secrets;print(secrets.token_urlsafe(48))"`. |
| `SESSION_COOKIE_NAME`     | `iris_session`                                | Name of the HttpOnly session cookie. |
| `SESSION_COOKIE_SECURE`   | `false`                                       | `true` → cookie only sent over HTTPS. **Set `true` in production.** `false` by default so `http://localhost` dev works. |
| `SESSION_COOKIE_SAMESITE` | `lax`                                         | SameSite policy for the session + CSRF cookies. `lax` suits a same-origin SPA. |
| `SESSION_TTL_HOURS`       | `12`                                          | Session lifetime; also the cookie `max-age`. |
| `LOGIN_MAX_ATTEMPTS`      | `5`                                           | Consecutive failed logins per (username, client-IP) before lockout. |
| `LOGIN_LOCKOUT_MINUTES`   | `15`                                          | How long a locked-out (username, IP) pair must wait. |
| `GPU_DEVICE_ID`           | `0`                                           | InsightFace / ONNX device id. `-1` forces CPU.                                                          |
| `SIMILARITY_THRESHOLD`    | `0.45`                                        | Defined in `backend/app/core/config.py` (Pydantic Settings). **Currently dormant** — the FAISS recognition path reads `backend/config.py` `SIMILARITY_THRESHOLD = 0.60` directly. Setting this env var has no effect today; either ignore it or wire it through `settings` if you want runtime overrides. |
| `CLIENT_PUNCH_API_URL`    | `""` (disabled)                               | Client-HIS attendance endpoint. Empty → the punch export pusher no-ops (punches still logged locally in `person_tracking_logs` / `punch_export_queue`). |
| `CLIENT_PUNCH_API_KEY`    | `""`                                          | Credential for the punch endpoint.                                                                      |
| `CLIENT_PREREG_API_URL`   | `""` (disabled)                               | Client-HIS pre-registration endpoint. Empty → the pre-registration forwarder no-ops.                    |
| `CLIENT_PREREG_API_KEY`   | `""`                                          | Credential for the pre-registration endpoint.                                                           |
| `MEDIA_DIR`               | `database/media`                              | Local root for profile photos (gitignored — biometric PII). Served at `/media/*` by the API. |
| `MEDIA_BASE_URL`          | `""`                                          | When a dedicated media server exists, its public base URL. Empty → photo URLs resolve to the API's own `/media/*` mount. DB stores relative paths, so flipping this needs no data migration. |
| `CLIENT_API_TIMEOUT`      | `10.0`                                        | Outbound HTTP timeout. Non-secret integration identifiers live on the singleton facility row. |
| `CLIENT_API_AUTH_HEADER`  | `Authorization`                               | Header the API key is attached to. |
| `CLIENT_API_AUTH_SCHEME`  | `Bearer`                                      | Scheme prefix before the key (`Authorization: Bearer <key>`). Set empty for an `X-API-Key: <key>` style (with `AUTH_HEADER=X-API-Key`). Empty key → no auth header sent. |
| `EXPORT_POLL_INTERVAL`    | `15`                                          | Seconds between export-worker sweeps of the punch + pre-reg queues. Worker only runs when a `CLIENT_*_API_URL` is set. |
| `EXPORT_MAX_ATTEMPTS`     | `10`                                          | Dead-letter a queued row after this many failed sends (left `FAILED`; retry manually via `POST /integrations/*/retry`). |
| `EXPORT_BACKOFF_BASE`     | `30`                                          | First retry delay (seconds); doubles each attempt. |
| `EXPORT_BACKOFF_CAP`      | `3600`                                        | Maximum retry delay (seconds). |
| `EXPORT_BATCH_SIZE`       | `20`                                          | Rows processed per queue per sweep. |
| `EXPORT_STALE_SECONDS`    | `120`                                         | Reclaim a row stuck in `SENDING` (crash mid-send) back to `PENDING` after this. |
| `OPENCV_FFMPEG_CAPTURE_OPTIONS` | `rtsp_transport;udp\|stimeout;5000000\|fflags;nobuffer\|flags;low_delay\|max_delay;500000` | RTSP transport + socket timeout + low-delay decoder hints. `run_backend.py` sets this. UDP is default because TCP head-of-line blocking causes stale-frame stalls on Tailscale/WireGuard tunnels; switch to `;tcp` for straight-LAN cameras where retransmits are cheap. |

Frontend-side — Vite reads env files in this precedence: `.env.local` → `.env.production` (production builds) / `.env.development` (dev) → `.env`. The included `frontend/.env.production` uses the same-origin path `/api/v1`. Local dev uses `frontend/.env` (gitignored) or `frontend/.env.local`.

| Variable                  | Default                                       | Effect                                                                                                  |
| ------------------------- | --------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `VITE_API_URL`            | `http://127.0.0.1:8000/api/v1`                | Base URL the SPA hits for REST + WS. **Baked into the bundle at build time** — not read at runtime. Override per env file or via `run_frontend.py --api-url`. |
| `VITE_ENABLE_LOGGING`     | `false` in both included env files. | Master switch for verbose `console.log` output. WARN/ERROR always pass through. |

---

## 3. Camera configuration

The runtime camera roster lives in PostgreSQL **`camera_master`** and is
mutated through the admin UI / API. There is no in-code list. On a clean
installation, startup imports `database/cameras.json` once when that legacy
file is present. A durable `data_migrations` marker prevents re-import if the
database roster is later emptied.

### Adding cameras

* **Recommended:** open **Live View** → **Cameras** → **+ Add Camera**.
  Use **Test connection** before saving so a wrong URL doesn't reach the
  worker pool.
* **Direct API:**
  ```bash
  curl -X POST http://localhost:8000/api/v1/cameras \
       -H 'Content-Type: application/json' \
       -d '{"name":"Lobby","type":"rtsp","source":"rtsp://10.0.0.42:554/stream1","floor":"floor_1","role":"entry"}'
  ```

### Schema

The API and worker-facing representation of each database row is:

```jsonc
{
  "camera_id": "cam_abc12345",     // server-assigned, immutable
  "name":      "Lobby Entrance",   // shown on dashboard tile
  "type":      "rtsp",             // "rtsp" | "usb" (USB legacy-only)
  "source":    "rtsp://...",       // RTSP URL or USB device index (int, legacy)
  "floor":     "floor_1",          // free-form slug, lowercase + underscore
  "role":      "entry",            // "entry" | "exit" | "inside"
  "active":    true,               // false → worker not spawned
  "created_at":"2026-04-25T15:25:59+00:00",
  "updated_at":"2026-04-25T15:25:59+00:00"
}
```

### What triggers a worker restart

| Field changed         | Worker restarts?                                                 |
| --------------------- | ---------------------------------------------------------------- |
| `source`, `type`      | **Yes** — old process killed, new one spawned with the new URL.  |
| `active` (true↔false) | **Yes** — start or stop the worker.                              |
| `name`, `floor`, `role` | No — metadata-only edit; live MJPEG keeps streaming.            |

### Role changes are live

Camera roles and the strict-exit-mode gate use a manager-backed shared map.
The registry updates it immediately after each committed database mutation.
Changing a camera's `role`, or adding the **first** `role: exit` camera,
takes effect without a worker or backend restart. See `ARCHITECTURE.md` §3.12.

---

## 4. Pipeline tuning (`backend/config.py`)

These are Python constants — change them in source and restart the
backend. The defaults are tuned for one CUDA-capable GPU box with 4–8
cameras.

### Hardware scaling

| Constant            | Default | Meaning                                                                       |
| ------------------- | ------- | ----------------------------------------------------------------------------- |
| `CPU_CORES`         | `6`     | Hint for face-cap derivation. Bump on bigger hosts.                           |
| `GPU_ENABLED`       | `True`  | Switches `GPU_SCALING_FACTOR` between 2.0 and 1.0.                             |
| `MAX_FACES_BASE`    | `6`     | Lower bound for `MAX_FACES_PER_FRAME`.                                         |
| `MAX_FACES_MAX`     | `20`    | Upper bound (don't process more than this even on a beast).                    |
| `FACES_PER_CORE`    | `1.5`   | Scaling factor: `MAX_FACES_PER_FRAME = min(MAX, CPU_CORES * THIS * GPU_FACTOR)` |

### Camera / capture

| Constant            | Default | Meaning                                                                        |
| ------------------- | ------- | ------------------------------------------------------------------------------ |
| `FRAME_WIDTH`       | `1920`  | Captured resolution (informational; some cameras ignore).                       |
| `FRAME_HEIGHT`      | `1080`  | Same.                                                                          |
| `CAMERA_FPS`        | `30`    | Target capture rate per camera.                                                |
| `RESIZE_FRAME`      | `True`  | Downscale before pipeline. Big win for queue pickle size.                      |
| `PROCESS_MAX_DIM`   | `960`   | Long-edge cap when `RESIZE_FRAME=True`. Aspect ratio preserved.                |
| `EXPECTED_MAX_CAMERAS` | `8`  | Pre-sizing constant for `MAX_FACES_PER_CAMERA`. Overshoot is benign.           |

### Concurrency

| Constant            | Default | Meaning                                                                        |
| ------------------- | ------- | ------------------------------------------------------------------------------ |
| `NUM_AI_WORKERS`    | `1`     | Number of AI worker processes. Increasing scales inference throughput.          |

> **Don't touch unless you measured the bottleneck.** Identity smoothing
> lives in `EventProcessor` (single-process), so multiple AI workers
> don't fragment tracker state — but they do increase GPU contention
> if you only have one card.

### Detection

| Constant                | Default      | Meaning                                                                       |
| ----------------------- | ------------ | ----------------------------------------------------------------------------- |
| `DETECTION_CONFIDENCE`  | `0.42`       | Drop faces below this confidence. Lowered from 0.5 to catch distant faces.    |
| `DETECTION_INPUT_SIZE`  | `(800, 800)` | Resize for the detector. Larger = more accurate, slower. Raised from 640.     |
| `MIN_FACE_SIZE`         | `24`         | Pixels (post-960-resize). Lowered from 40 → ~2.9 m range on 95° FOV cam.      |
| `MAX_TOTAL_FACES`       | `30`         | Hard cap on faces processed per frame across all cameras.                     |
| `MAX_FACES_PER_CAMERA`  | derived      | `max(4, MAX_TOTAL_FACES // EXPECTED_MAX_CAMERAS)`.                            |
| `STRICT_FACE_LIMIT`     | `False`      | When `False`, always sort + slice to top-N. When `True`, only cap on overflow. |

### Matching (FAISS)

| Constant                       | Default | Meaning                                                                       |
| ------------------------------ | ------- | ----------------------------------------------------------------------------- |
| `SIMILARITY_THRESHOLD`         | `0.60`  | Cosine cutoff for "known". Lower = more permissive.                           |
| `FAISS_TOP_K`                  | `3`     | Top-k neighbours; aggregated by max score per identity.                       |
| `MAX_EMBEDDINGS_PER_IDENTITY`  | `10`    | Cap per person. Beyond ~10 there's diminishing accuracy return.               |
| `SMOOTHING_WINDOW`             | `5`     | Majority-vote frames. `5` ≈ 170 ms at 30 FPS. `1` disables smoothing.         |

### Performance / tracking

| Constant                       | Default | Meaning                                                                       |
| ------------------------------ | ------- | ----------------------------------------------------------------------------- |
| `MOTION_SKIP_ENABLED`          | `True`  | Skip detector on idle scenes (cheap 64×64 grayscale diff).                    |
| `MOTION_SKIP_THRESHOLD`        | `2.5`   | Mean-abs-diff cutoff. ~1% pixel change.                                       |
| `MOTION_SKIP_MAX_INTERVAL_S`   | `2.0`   | Force a pipeline run every N seconds even when idle (so trackers tick).        |
| `TRACKING_DISTANCE_THRESHOLD`  | `50`    | Pixel distance for centroid match.                                            |
| `TRACK_MAX_AGE`                | `10`    | Frames before an unseen track is dropped.                                     |
| `AGGREGATION_TIMEOUT`          | `0.3`   | Max seconds to wait for all faces in a frame.                                 |

### Database paths

Anchored to project root automatically — no edit needed unless you move
the FAISS / name-map files:

* `DATABASE_DIR`      — `database/`
* `FAISS_INDEX_PATH`  — `database/face_index.bin`
* `NAME_MAP_PATH`     — `database/name_map.json`
* `FACE_IMAGES_DIR`   — `database/faces/`

Camera configuration is relational data in PostgreSQL, not in this directory.

---

## 5. Logging

### How it works

* Configured once by `backend/app/core/logging.py::setup_logging()`,
  called from the FastAPI lifespan.
* Format: `YYYY-MM-DD HH:MM:SS | LEVEL   | logger_name | message`.
* Level selection (highest wins):
  1. `SYNORA_DEBUG_LOG=false` → root WARNING **(default)**
  2. `DEBUG=true` env → root DEBUG
  3. otherwise → root INFO
* `uvicorn.access` and `multipart` loggers are pinned to WARNING so
  hot-path noise stays out of the way.
* A custom filter drops benign `asyncio.CancelledError` tracebacks from
  uvicorn (those fire whenever a browser closes an MJPEG `<img>`).

### `SYNORA_DEBUG_LOG`

The master verbose switch. Read at process start:

* `false` (default) — root logger at WARNING. Most `print()` blocks are
  gated by `if ENABLE_LOGGING:` and stay silent.
* `true` — root logger at DEBUG. Gated `print()` blocks emit (`[ROLE]`,
  `[TRACK]`, `[GLOBAL]`, AI worker face counts, etc).

WARN and ERROR always pass through regardless of this flag — real
problems stay visible.

### What you'll see at default level

* Camera/AI worker lifecycle: `[STARTED]`, `[STOPPED]`, `[GRABBER]
  opening...`, `[GRABBER] opened OK`.
* Health transitions: `[CAMERA DISCONNECTED]`, `[RECONNECTED DETECTED]`,
  `[STREAM VERSION]`.
* Patient state: `[ENTRY]`, `[EXIT]`, `[GLOBAL EXIT]`, `[HARD EXIT]`,
  `[ENTRY ALLOWED]`, `[ENTRY BLOCKED]`.
* Database transitions: `[DB ENTRY]`, `[DB EXIT]` (the per-detection
  `[DB UPDATE]` heartbeat is silenced — see commit history).
* Alerts: `[ALERT]`.
* Errors: every `[* ERROR]` line.

### What's silent by default (use `--debug-log` to see)

* `[ROLE]` (per detection event)
* `[TRACK]` / `[GLOBAL]` (per camera per event)
* `[AI-N] Faces detected` (per inference)

### Why a mix of `print()` and `logger.*`?

`print()` writes from multiprocessing children directly to stdout,
which is the same console you watch. `logging` would require calling
`setup_logging()` at the top of each child's `run()` since logger
config doesn't propagate across `multiprocessing.Process`. The current
codebase prints the rare lifecycle events from children and logs the
rest from the FastAPI parent — pragmatic, not pretty. If you want full
log-level discipline across children, that's a separate refactor.

### Frontend logging

* Master switch in [`frontend/src/config.js`](../frontend/src/config.js)
  via `VITE_ENABLE_LOGGING`.
* `console.warn` / `console.error` always pass through.
* Default: `true` in dev, flip to `false` for prod builds via env.

---

## 6. CORS

Default `ALLOWED_ORIGINS = ["*"]` (set in `backend/app/core/config.py`).

For production behind a reverse proxy, set:

```bash
ALLOWED_ORIGINS='["https://synora.example.com"]'
```

The middleware also accepts credentials and all headers/methods; see
[`backend/app/main.py`](../backend/app/main.py).

> With `AUTH_ENABLED=true`, `ALLOWED_ORIGINS` **must not** contain `*` — browsers refuse credentialed (cookie) requests to a wildcard origin, and the backend logs a loud error at boot if it detects this. Name the exact public origin(s) instead.

---

## 6b. Auth, rate limits & audit

- **Auth/RBAC** is off by default (`AUTH_ENABLED=false`). See the env-var table (§2) for `SESSION_SECRET` and cookie settings, and `API_REFERENCE.md` §2 for the role/permission model.
- **Rate limiting** is not bundled. Configure it in your hosting or reverse-proxy layer. App-level login lockout (`LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCKOUT_MINUTES`) remains available.
- **Audit log**: security-relevant actions are recorded to the `audit_log` table and readable via `GET /api/v1/audit` (`audit.read`). No configuration required.
- **Security headers** must be configured in the selected hosting or reverse-proxy layer.

---

## 7. Topology summary

```
┌────────────────────────────────────────────────────────────────────┐
│  FastAPI process (main)                                            │
│  ├─ uvicorn ASGI app                                               │
│  ├─ MJPEG streamer  ──────────►  /api/v1/stream/{id}               │
│  ├─ WebSocket bridge  ────────►  /api/v1/ws/live                   │
│  ├─ REST (incl. /cameras admin)                                    │
│  ├─ FAISS index (in-memory)                                        │
│  ├─ Postgres SQLAlchemy session                                    │
│  └─ CameraRegistry (worker lifecycle owner)                        │
│       │                                                            │
│       ├─► CameraWorker × N    (one process per camera)             │
│       │     └─ cv2.VideoCapture, frame queue producer              │
│       │                                                            │
│       ├─► AIWorker × NUM_AI_WORKERS                                │
│       │     └─ detect → embed → match (FAISS)                      │
│       │                                                            │
│       └─► EventProcessor × 1                                       │
│             └─ tracker smoothing, session writes, WS broadcast     │
└────────────────────────────────────────────────────────────────────┘
```

For the full architecture see
[`ARCHITECTURE.md`](ARCHITECTURE.md).
