# Roadmap — V2 architecture, service split, and improvement backlog

> Opinionated plan for re-architecting Iris for higher camera counts,
> better stability, and lower latency. Ordered by ROI: quick wins
> first, structural rewrites last.
>
> Scope: backend pipeline, FAISS / Postgres data layer, multi-camera
> orchestration, deployment, observability, and a concrete V2 service
> split.
>
> Verified against the codebase on 2026-05-25. The user-model
> expansion (PATIENT / DOCTOR / EMPLOYEE / VISITOR / RELATIVE) shipped
> in Phases 1–8 and is captured under those headings in
> [CHANGELOG.md](CHANGELOG.md).

Companion docs: [ARCHITECTURE.md](ARCHITECTURE.md) for current
internals · [HARDWARE_AND_CAMERAS.md](HARDWARE_AND_CAMERAS.md) for
per-tier capacity and the L4 future-state.

---

## Contents

1. [Quick wins (1–2 weeks each)](#1-quick-wins)
2. [Module-level re-architecture](#2-module-level-re-architecture)
3. [Data-layer redesign](#3-data-layer-redesign)
4. [Scalability tiers (10 → 100 → 500 cameras)](#4-scalability-tiers)
5. [Observability and stability](#5-observability-and-stability)
6. [Deployment and DevOps](#6-deployment-and-devops)
7. [V2 target architecture (event-bus)](#7-v2-target-architecture)
8. [Service decoupling — concrete plan](#8-service-decoupling)
9. [Migration plan V1 → V2](#9-migration-plan-v1-v2)
10. [Risk register](#10-risk-register)
11. [Front Desk / OPD backlog](#11-front-desk--opd-backlog)
12. [Appendix — file-level TODOs](#12-appendix-file-level-todos)

---

## 1. Quick wins

Small changes, large payoff. Do these before any structural rewrite.

### 1.1 Replace `IndexFlatIP` with `IndexIVF*` once registry > 5k

**Problem.** [`backend/app/db/face_db.py`](../backend/app/db/face_db.py)
uses `faiss.IndexFlatIP`, brute-force O(N·D) per query. Fine at
hundreds of identities, painful at 50k+.

**Fix.**

1. Add `FAISS_INDEX_TYPE` to [`backend/config.py`](../backend/config.py):
   `"flat"` (default) | `"ivf"` | `"ivfpq"`.
2. In `face_db.py`, factory-build the index based on registry size:
   - `< 5k` → `IndexFlatIP`
   - `5k–500k` → `IndexIVFFlat`, `nlist = 4·sqrt(N)`
   - `> 500k` → `IndexIVFPQ`, 8-bit codes
3. Train on existing embeddings during reconciliation startup.
4. Add `nprobe` knob (start at 16, tune).
5. Wrap with `IndexIDMap2` so `user_id` keys still work.

**Expected gain:** 50× search throughput at 100k identities; sub-ms p99.

### 1.2 Move FAISS writes off the request path

`face_service.register_face()` calls `face_db.add()` then
`face_db.save()` synchronously inside the `/register` HTTP handler. A
100k-vector index serialises in ~200–500 ms — a request-path stall.

**Fix.** Add an `asyncio.Queue` of pending writes. Background task
flushes batched writes every `N` seconds or `M` pending vectors,
whichever first. Persist to a tmp file + `os.replace()` for atomic
swap. On shutdown, drain the queue.
[`backend/app/services/pipeline_service.py`](../backend/app/services/pipeline_service.py)
already owns the write lock — extend it with a flusher task.

### 1.3 GPU-batch the embedding stage across cameras

Today each per-camera process runs ArcFace one face at a time.
ArcFace is memory-bound at batch=1; batching 8–16 faces typically
gives 3–6× throughput on the same GPU.

**Fix.** A single **embedding service process** that:
1. Pulls aligned-face crops from all cameras via a shared queue.
2. Accumulates up to `BATCH_SIZE` (or `BATCH_TIMEOUT_MS`).
3. Runs one ONNX call, fans results back by request-id.

Keeps detection per-camera (already cheap), unifies the heavy stage.

### 1.4 Switch ONNX provider order and use fp16

In `_load_recognizer()` ensure providers are
`["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]`
and export the model to fp16. Typical ArcFace 1.5–2× speedup, no
measurable accuracy loss in our threshold range.

### 1.5 Frame-skip more aggressively when no tracks are active

`MOTION_SKIP_*` exists, but consider a second tier: when the tracker
reports zero active tracks for `N` seconds, drop detector to 1 fps
until motion returns. Cuts idle-camera GPU draw to near zero —
relevant for 24/7 hospital hallways.

### 1.6 Per-camera adaptive FPS

Rather than `CAMERA_FPS = 25` globally, let each camera's grabber
self-throttle when its downstream queue is full for >X frames. Already
have backpressure via small queues; making it explicit avoids the
silent-drop pattern.

### 1.7 Precompute name lookups via Postgres `LISTEN/NOTIFY`

`face_service` resolves `user_id → name` per recognition. Cache the
full `id → (name, mrn, user_type)` map in memory; invalidate on
`LISTEN user_changed`. One DB roundtrip becomes zero.

---

## 2. Module-level re-architecture

### 2.1 Split `face_service.py` into Register / Recognize / Reconcile

Current file mixes registration write-path, recognition read-path, and
DB-FAISS reconciliation. Three concerns, three lifecycles, three test
surfaces. Split:

```
services/face/
  ├── register.py      # write path, holds write lock
  ├── recognize.py     # pure read, no locks
  ├── reconcile.py     # startup + periodic GC of orphans
  └── shared.py        # alignment, embedding helpers
```

Lets `recognize.py` stay 100% lock-free and trivially horizontally
scaled.

### 2.2 Promote the camera pipeline stages into a real DAG

[`backend/camera/pipeline_runner.py`](../backend/camera/pipeline_runner.py)
hard-codes capture → detect → track → align → embed → match. Migrate
to a config-driven DAG:

```yaml
pipeline:
  - stage: capture
  - stage: motion_filter
  - stage: detect
    workers: 2
  - stage: track
  - stage: align
  - stage: embed
    workers: 1
    batch: 16
  - stage: match
  - stage: emit
```

Lets ops re-tune workers per stage without code changes, and lets us
slot in new stages (mask detection, age estimation, gait) without
restructuring.

### 2.3 Make `EventProcessor` a pluggable sink

[`backend/camera/event_processor.py`](../backend/camera/event_processor.py)
currently writes attendance + presence + WebSocket in one place.
Replace with a sink registry:

```python
EventProcessor.register_sink(AttendanceSink(...))
EventProcessor.register_sink(PresenceSink(...))
EventProcessor.register_sink(WebSocketSink(...))
EventProcessor.register_sink(KafkaSink(...))   # future
```

Each sink owns its own retry, batching, error budget. Failure of one
(e.g. WebSocket disconnect storm) cannot starve the others.

### 2.4 Detach the camera system from the FastAPI process

Today `START_CAMERA_SYSTEM=1` runs camera workers as children of
uvicorn. Convenient for dev, dangerous for prod:

- An OOM in a camera worker can bring down the API.
- Reloading the API (deploy) tears down all cameras.
- `--workers 4` on uvicorn would spawn four camera systems.

**Fix.** Make camera-manager a **separate systemd unit** (or
container) that talks to the API over the existing event bridge. This
is already mostly the design — see §8 for the concrete split.

### 2.5 Replace per-camera processes with a worker pool

One process per camera scales linearly until ~30 cameras, then
context-switching dominates. Move to:

- **N grabber threads** (I/O bound, 1 per camera is fine).
- **M GPU worker processes**, where `M = num GPUs × 2`.
- Shared frame ring buffer (already partially built in
  [`backend/camera/shared_frame_buffer.py`](../backend/camera/shared_frame_buffer.py)).

Targets 8 GPUs handling 200 cameras at 5 fps each.

---

## 3. Data-layer redesign

### 3.1 Postgres → Postgres + Redis (hot-state)

Hot reads — current presence per user, last-seen camera, active
sessions — hit Postgres on every camera frame today
([`presence_cache.py`](../backend/app/services/presence_cache.py) is
in-process only).

Move to Redis:

```
user:{id}:presence  → {floor, camera, ts}    TTL 5m, refreshed by sightings
user:{id}:session   → session_id              while INSIDE
camera:{id}:roster  → SET of present user_ids
floor:{n}:roster    → SET of present user_ids
```

Postgres remains source-of-truth for identity + history. Redis is the
real-time projection. Survives multi-process API workers cleanly.

### 3.2 Time-series store for sightings

At 50 cameras × 5 fps × 5 faces/frame ≈ 3 M sightings/day. Postgres
handles this for a week, not a year.

**Fix.** Send sightings to **TimescaleDB** (Postgres extension;
minimal code change) or **ClickHouse** (heavier lift, much cheaper at
scale). Use continuous aggregates for "user X visit history".

### 3.3 Embedding versioning

FAISS keys are `user_id`, no model-version tagging. The day you
retrain ArcFace, the index becomes a black box.

**Fix.** Schema:

```sql
face_embeddings (
  user_id INT,
  model_version TEXT,
  embedding VECTOR(512),
  source_image_id INT,
  captured_at TIMESTAMP,
  PRIMARY KEY (user_id, model_version, source_image_id)
)
```

Use **pgvector**, or keep FAISS but tag the binary index with model
version. Rebuild offline, hot-swap atomically.

### 3.4 Soft delete + audit log

`User` (formerly `Patient`) gained `is_active` in the user-model
expansion but there's no `deleted_at` / audit trail. For HIPAA /
medical-records compliance, deletes must be reversible and logged. Add
`deleted_at` + `audit_log` with `(actor, action, entity, entity_id,
before, after, ts)`.

---

## 4. Scalability tiers

| Scale | Cameras | Identities | Topology |
|-------|---------|------------|----------|
| **Now** | 1–8 | < 5k | Single host, FlatIP, sync writes |
| **Tier 1** | 8–32 | < 50k | Single host + dedicated GPU box, IVF index, batched embed, Redis presence |
| **Tier 2** | 32–128 | < 500k | API cluster (3+ nodes) behind LB, dedicated inference node(s), IVFPQ, Timescale sightings, Kafka event bus |
| **Tier 3** | 128–500 | 1M+ | Region-sharded by site/floor, per-region FAISS shard, cross-region async replication, GPU autoscaler (K8s + KEDA) |

### Stepwise plan

1. **Tier 1 (next 2 months)**
   - Implement §1.1, §1.2, §1.3, §3.1.
   - Deploy camera-manager as separate service (§2.4 / §8).
   - Add Prometheus metrics from [`backend/camera/metrics.py`](../backend/camera/metrics.py).

2. **Tier 2 (3–6 months)**
   - Introduce Kafka (or NATS / Redis Streams) between camera-manager and API for sightings.
   - API becomes stateless; scale horizontally.
   - Move sightings to Timescale (§3.2).
   - Embedding service becomes its own gRPC service (§1.3 promoted).

3. **Tier 3 (6–12 months)**
   - Region-shard FAISS by site_id; query fan-out + merge.
   - Move to K8s; GPU pods with NVIDIA device plugin; KEDA autoscale on queue depth.
   - Multi-tenant isolation: namespace per hospital.

---

## 5. Observability and stability

### 5.1 Structured logging

Replace ad-hoc `print()` and the bespoke `SYNORA_DEBUG_LOG` toggle
with **structlog** + JSON output. Required fields per record:

```
ts, level, service, camera_id, frame_id, stage, latency_ms, error
```

### 5.2 Per-stage Prometheus metrics

[`backend/camera/metrics.py`](../backend/camera/metrics.py) already
exists. Make it export:

- `pipeline_stage_latency_seconds{stage,camera}` (histogram)
- `pipeline_queue_depth{queue}` (gauge)
- `pipeline_dropped_frames_total{camera,reason}` (counter)
- `faiss_search_seconds` (histogram)
- `faiss_index_size` (gauge)
- `recognition_confidence` (histogram)
- `gpu_utilization` (gauge, via `pynvml`)

### 5.3 Distributed tracing

OpenTelemetry spans across grabber → detect → embed → match → emit,
correlated by `frame_id`. Catches single-frame regressions invisible
to aggregate metrics.

### 5.4 Health probes

- `/healthz` (liveness): process is up.
- `/readyz` (readiness): models loaded, FAISS loaded, DB reachable.
- `/metrics` (Prometheus).

`/api/v1/health` exists but doesn't check FAISS or model-load state —
fix.

### 5.5 Circuit breakers

Wrap Postgres and downstream sinks with a breaker (e.g. `pybreaker`).
Today a slow Postgres can stall the whole event pipeline.

### 5.6 Chaos drills (monthly)

Kill one camera worker, kill Postgres for 30 s, fill the FAISS index
path's disk to 95%. The system should degrade visibly, never silently.

---

## 6. Deployment and DevOps

### 6.1 Dockerise per service

```
docker/
  api.Dockerfile
  camera-manager.Dockerfile
  embedding-service.Dockerfile
  worker-base.Dockerfile          # shared CUDA + insightface base layer
```

### 6.2 GPU base-image discipline

Pin the CUDA + cuDNN + ONNX Runtime trio explicitly. Today
[`backend/requirements.txt`](../backend/requirements.txt) implies
versions but doesn't pin the runtime stack.

### 6.3 Configuration via env + Vault

`config.py` is a Python file — any change is a deploy. Move runtime
tunables (`SIMILARITY_THRESHOLD`, queue sizes, motion-skip) to env
vars via `pydantic-settings`. Secrets (`DATABASE_URL`) into Vault /
SOPS.

### 6.4 CI gates

- `pytest` unit + integration.
- `mypy` on `backend/app/`.
- `ruff` lint.
- Recognition regression suite: a frozen set of 200 face crops with
  expected identity; CI fails if recognition rate drops > 1%.

### 6.5 Blue/green model deploys

Treat the ONNX model as an artifact. Version it. Deploy two indices
(old + new), shadow-route 5% of traffic to new, compare confidence
distributions, then promote.

---

## 7. V2 target architecture

```
                          ┌───────────────────────────────┐
                          │       Cameras (RTSP / SDK)    │
                          └──────────────┬────────────────┘
                                         │
                          ┌──────────────▼────────────────┐
                          │   Edge Grabber Pods (per site)│
                          │   - decode, motion-skip       │
                          │   - publish raw frames + meta │
                          └──────────────┬────────────────┘
                                         │   Kafka (frames topic)
                                         │
                          ┌──────────────▼────────────────┐
                          │       GPU Inference Tier      │
                          │   detect → align → embed      │
                          │   (autoscaled, batched)       │
                          └──────────────┬────────────────┘
                                         │   Kafka (embeddings topic)
                                         │
                          ┌──────────────▼────────────────┐
                          │    Matching Service           │
                          │   FAISS shards (by site)      │
                          │   smoothing window per track  │
                          └──────────────┬────────────────┘
                                         │   Kafka (sightings topic)
                                         │
        ┌────────────────────┬───────────┴───────────┬──────────────────┐
        │                    │                       │                  │
   ┌────▼─────┐        ┌─────▼─────┐         ┌───────▼──────┐    ┌──────▼─────┐
   │Presence  │        │Attendance │         │Alerting /    │    │Analytics   │
   │ Service  │        │ Service   │         │Notification  │    │Sink        │
   │(Redis)   │        │(Postgres) │         │              │    │(Timescale/ │
   └────┬─────┘        └─────┬─────┘         └──────────────┘    │ ClickHouse)│
        │                    │                                   └────────────┘
        └────────┬───────────┘
                 │
         ┌───────▼────────┐
         │   Public API   │
         │  REST + WS     │
         │  (stateless)   │
         └────────────────┘
```

### Why this shape

- **Decoupling.** Cameras can fail without taking inference down. Inference can scale without touching cameras. API can deploy without dropping a frame.
- **Backpressure is explicit.** Kafka topics smooth bursty traffic; no more in-process queue tuning.
- **Replayability.** Sightings are an append-only log. Bug in presence logic? Replay last hour from Kafka into a fresh service.
- **Multi-tenant ready.** Topic-per-site or partition-per-site; matching service shards FAISS the same way.
- **Cost shaping.** GPU tier autoscales on Kafka lag, not camera count.

### Cost-conscious variant

Swap Kafka for **Redis Streams** + consumer groups. Loses some
durability guarantees, gains operational simplicity. Fine up to ~100
cameras. **The service-decoupling plan in §8 takes this variant** as
the concrete first step.

---

## 8. Service decoupling — concrete plan

**Status:** design-only. No code yet. The user-model expansion ships
in the current monolith; this plan is the first concrete split.

### 8.1 What exists today

The FastAPI process is the parent of the camera subsystem **only when
`START_CAMERA_SYSTEM=1` is set** — see
[`backend/app/main.py`](../backend/app/main.py). Default is **off** so
`uvicorn --reload` doesn't fork-bomb.

When the env var is set,
[`backend/camera/manager.py`](../backend/camera/manager.py) spawns:

```
uvicorn (FastAPI)
  ├── EventProcessor (1)                       # event_processor.py
  ├── AIWorker × NUM_AI_WORKERS (default 1)    # ai_worker.py
  ├── TestSimulator (only if CAMERA_TEST_MODE=1)
  └── CameraWorker × N                          # one per active camera_master row
        (owned by Registry, joined separately on shutdown)
```

### 8.2 IPC primitives in use

| Mechanism | Where | What it carries |
|-----------|-------|-----------------|
| `multiprocessing.Queue(maxsize=40)` | `frame_queue` | Frame *handles* (not pixels — see SharedMemory below) |
| `multiprocessing.Queue(maxsize=100)` | `event_queue` | Detection / entry-exit events from AI worker → EventProcessor |
| `multiprocessing.Queue(maxsize=200)` | `ws_queue` | EventProcessor → WS bridge in FastAPI |
| `multiprocessing.Queue(maxsize=1)` | `stream_queues[cam_id]` | Latest raw BGR frame for MJPEG (drop-old) |
| `Manager().dict()` | `stream_versions`, `viewer_counts`, `metrics_dict` | Cross-process gauges |
| **`SharedMemory` ring buffer** | `FrameBufferPool` ([`shared_frame_buffer.py`](../backend/camera/shared_frame_buffer.py)) | **Actual frame pixels — zero-copy across stages** |

The Redis cost concern only applies to **cross-host** streaming —
within a host we already use SharedMemory, not pickled queues.

### 8.3 FAISS coordination today

- API process holds the write lock ([`pipeline_service.py`](../backend/app/services/pipeline_service.py)`.face_db_write_lock`).
- Writes are debounced 1 s by `schedule_save()` in [`face_db.py`](../backend/engine/database/face_db.py).
- AI workers in child processes detect a new index by **mtime polling** in [`pipeline_runner.py`](../backend/camera/pipeline_runner.py) (`_current_index_mtime` / `_maybe_reload_database`).
- Save ordering is load-bearing: name map is renamed first, then index, so the mtime tick never observes vectors whose ID isn't in the map yet.

Functional today — the question for the split is whether mtime polling
survives a network filesystem.

### 8.4 Why decouple

The shared-process model breaks when we want:

1. **Multiple uvicorn workers** (`--workers 4` / gunicorn). Each worker would re-spawn the camera stack and stream every camera N×.
2. **Restart the API without dropping frames.** Today a reload kills every camera process; RTSP reconnect dance starts over (5–15 s per camera).
3. **Different hosts for API and pipeline** — API on a cheap CPU box, pipeline on a GPU box. Currently impossible because `multiprocessing.Queue` and `Manager()` require a shared parent.
4. **Horizontal scaling of the API** behind a load balancer — even without splitting the pipeline, multi-instance API needs out-of-process WebSocket fan-out.

These map to the same fix: replace the in-process queue/Manager mesh
with a network-addressable bus.

### 8.5 Target shape

```
┌──────────────────────────────┐         ┌──────────────────────────────┐
│  pipeline-service            │         │  api-service                 │
│  (GPU box)                   │         │  (FastAPI, can be multi-inst)│
│                              │         │                              │
│  Registry → CameraWorker × N │         │  HTTP routes                 │
│  AIWorker × M                │         │  WebSocket gateway (fan-out) │
│  EventProcessor              │         │  /register, /recognize       │
│  Bus publisher               │         │  /stream/{cam} proxy         │
│  /healthz, /metrics          │         │  Bus subscriber              │
└──────────┬───────────────────┘         └───────────┬──────────────────┘
           │                                         │
           │       ┌───────────────────┐             │
           └──────▶│  Redis (Streams + │◀───────────┘
                   │  Pub/Sub)         │
                   └─────────┬─────────┘
                             │
            ┌────────────────┴────────────────┐
            │                                 │
       ┌────▼────────┐                ┌───────▼──────┐
       │ PostgreSQL  │                │ FAISS index  │
       │ (shared)    │                │ on shared FS │
       └─────────────┘                │ + cmd:refresh│
                                      └──────────────┘
```

### 8.6 Bus choice

| Option | Throughput | Ops cost | Replay | Verdict |
|--------|-----------|----------|--------|---------|
| **Redis 7 (Streams + Pub/Sub)** | 100k msg/s | Low — common dep | Last-N via XADD MAXLEN | **Recommended** |
| Redis Streams + Consumer Groups | same | same + group bookkeeping | Per-consumer ack | Use for entry/exit events specifically |
| NATS JetStream | Higher | One more service | Yes | Overkill at our scale |
| Kafka | Industrial | Heavy | Yes | Skip until Tier 3 (≥ 128 cameras) |
| RabbitMQ | Medium | Medium | Limited | No advantage here |

**Decision: Redis 7.** Cheap, dual-mode (Pub/Sub fire-and-forget,
Streams for durable replay), already familiar to most ops teams.
Re-evaluate at Tier 2 when sustained event rate > 5k/s.

### 8.7 Channel topology

| Channel | Type | Direction | Payload | Retention | Notes |
|---------|------|-----------|---------|-----------|-------|
| `events:detection` | Stream | pipeline → api | `{ts, camera_id, frame_id, faces:[{user_id, name, bbox, confidence, track_id, user_type}]}` | `MAXLEN ~ 5000` | Drives WS broadcast + detection cache |
| `events:entry_exit` | Stream + Consumer Group | pipeline → api | `{ts, type:ENTRY/EXIT/UPDATE, user_id, camera_id, floor, session_id?}` | `MAXLEN ~ 50000` | Group `api-attendance` acks; replayable |
| `events:camera_state` | Pub/Sub | pipeline → api | `{ts, camera_id, state:CONNECTED/DISCONNECTED/ERROR, msg}` | ephemeral | Plus periodic heartbeat |
| `cmd:refresh_faces` | Pub/Sub | api → pipeline | `{user_id?, op:add/del/full}` | ephemeral | **Replaces mtime poll** |
| `cmd:camera_control` | Pub/Sub | api → pipeline | `{op:add/remove/restart, camera_id, payload?}` | ephemeral | Replaces Registry RPC |
| `stream:<cam>:frame` | **NOT in Redis** — see §8.9 | — | — | — | Streaming has its own path |
| `metrics:pipeline` | Stream | pipeline → api | `{ts, queue_depths, fps_per_cam, gpu_util}` | `MAXLEN ~ 1000` | Replaces `Manager().dict()` metrics |

Schema via pydantic models in `backend/app/schemas/bus_*.py`, shared
by both services. Version envelope mandatory:

```python
class BusEnvelope(BaseModel):
    schema_version: int = 1
    event_id: str        # uuid4 for dedup
    ts: float            # epoch seconds, IST tz at consume
    payload: dict
```

Bumping `schema_version` is a deploy event, not a runtime variable.

### 8.8 What moves where

**pipeline-service** (new entrypoint: `python -m backend.pipeline.service`)

- All of [`backend/camera/`](../backend/camera/)
- All of [`backend/engine/`](../backend/engine/)
- [`backend/app/services/pipeline_service.py`](../backend/app/services/pipeline_service.py) (model singletons)
- New: `backend/pipeline/bus.py` — Redis publisher
- New: `backend/pipeline/control.py` — subscribes to `cmd:*` channels
- New: thin FastAPI surface on **port 9000** for `/healthz`, `/metrics`, and the streaming endpoints. **Not** the public API.

**api-service** (current uvicorn target)

- All of [`backend/app/api/`](../backend/app/api/) routes
- [`backend/app/services/face_service.py`](../backend/app/services/face_service.py) (registration / recognition write path)
- [`backend/app/services/event_bridge.py`](../backend/app/services/event_bridge.py) rewritten as a Redis subscriber feeding `ws_manager`
- WebSocket fan-out via Redis Pub/Sub so multiple API replicas all reach all connected clients
- `/api/v1/stream/{camera_id}` becomes an HTTP reverse-proxy to pipeline-service (§8.9)

**Shared**

- **PostgreSQL** — connection pool per service.
- **FAISS index file** — see §8.10.
- **Pydantic bus schemas** — package as `backend/shared/schemas/`.

### 8.9 Streaming MJPEG bypasses Redis

8 cameras × 8 FPS × ~30 KB JPEG = **1.9 MB/s** through Redis. Redis
can do that, but forces a **double encode** (JPEG on pipeline, decode
+ re-encode on API for overlay) **unless** detection metadata is
pushed in lockstep, which is fragile.

Keep `/api/v1/stream/{camera_id}` on **pipeline-service**, with
api-service proxying via HTTP redirect or `httpx.AsyncClient` stream.
Pipeline already has the raw frame in shared memory; overlay logic
stays where the detections live; no JPEG round-trip through Redis.

Pipeline-service must accept browser-facing connections — mitigate
with auth (§8.12) and put both services behind the same nginx so the
client sees one origin.

### 8.10 FAISS across the boundary

Three options:

**Option A — shared file + `cmd:refresh` push (recommended start)**

- API holds the write lock, writes to a shared volume (NFS / EFS / Ceph / single-host bind-mount).
- After save, API publishes `cmd:refresh_faces`.
- Pipeline subscribes; on message, calls `face_db.load()` immediately.
- **Drop the mtime poll** — push gives sub-100 ms propagation vs the current ≤1 s poll lag.

Caveats: NFS file locks (`flock`) are unreliable on some
implementations. Guard with **advisory locks via Redis** (`SET nx ex`
lockfile). Single-writer assumption holds: only api-service writes;
pipeline reads only. Atomic save already exists (rename name-map
first, then index) — keep it; survives reader-during-write.

**Option B — gRPC facade on pipeline-service**

API does not touch the file; calls `pipeline.Register(image_bytes) →
user_id` and `pipeline.Search(embedding) → matches` over gRPC. Pure
separation, no shared filesystem. Cost: registration latency rises by
one network hop; pipeline must be available for the API to function
(couples liveness).

**Option C — pgvector / external vector DB**

Move embeddings into Postgres `vector` column (pgvector extension) or
Qdrant/Weaviate. Both services query the same DB; no FAISS file at
all. Cost: largest rewrite; pgvector at 100k rows is ~10 ms vs FAISS
flat at <1 ms; matches the V2 endgame (§3.3).

**Verdict:** start with Option A, defer C. Migrate to C at Tier 2
scale (50k+ identities).

### 8.11 WebSocket fan-out across replicas

A multi-instance api-service has a fan-out problem: a client connected
to replica B must receive an event consumed by replica A.

**Solution:** every api-service replica subscribes to
`events:detection` and `events:entry_exit` directly. No intra-service
broadcast needed — Redis is the broadcast plane. `ws_manager.py`
becomes a pure local connection registry; the subscriber loop fans
out to whoever is connected. **No sticky sessions required.**

### 8.12 Auth and security on the bus

Today: zero auth. Acceptable on a single host. **Unacceptable** the
moment Redis is exposed across hosts.

Minimum bar:

| Surface | Mechanism |
|---------|-----------|
| Redis | `requirepass` + ACL per service (`pipeline-rw`, `api-rw`) + TLS if cross-host |
| Inter-service HTTP (stream proxy, gRPC if Option B) | Shared-secret header `X-Synora-Token` from env |
| Public HTTP (the API itself) | Out of scope — separate auth ticket |
| Postgres | Per-service role with least privilege; pipeline gets RW on `sightings`, RO on `users` |

Bus envelopes don't need to be signed if the network is trusted
(single VPC). If not, sign with HMAC-SHA256 + shared secret.

### 8.13 EventBus interface

A shim that keeps in-process queues for dev and switches to Redis for
prod without changing call sites.

```python
# backend/shared/bus.py
class EventBus(Protocol):
    async def publish(self, channel: str, payload: dict) -> None: ...
    async def subscribe(self, channel: str, handler: Callable) -> None: ...
    async def xadd(self, stream: str, payload: dict, maxlen: int) -> str: ...
    async def xread(self, stream: str, last_id: str = "$") -> AsyncIterator[Tuple[str, dict]]: ...

class InProcessBus(EventBus):  # default for dev
    ...

class RedisBus(EventBus):
    def __init__(self, url: str, namespace: str = "synora"):
        self._redis = redis.asyncio.from_url(url, decode_responses=True)
```

Selection by env: `SYNORA_BUS=inproc` (default) | `redis://...`.

Replacing the mtime poll:

```python
# pipeline side
async for _, msg in bus.subscribe("cmd:refresh_faces"):
    face_db.reload()
    logger.info("FAISS reloaded after %s", msg.get("op"))

# api side, after register_face success
await bus.publish("cmd:refresh_faces", {"op": "add", "user_id": uid})
```

Distributed FAISS write lock via Redis:

```python
async with bus.redis.lock("synora:faiss:write", timeout=10, blocking_timeout=5):
    face_db.add(uid, embedding)
    face_db.save()  # atomic rename inside
    await bus.publish("cmd:refresh_faces", {"op": "add", "user_id": uid})
```

This **replaces** the in-process `face_db_write_lock` for cross-service
safety. Keep the in-process lock too — defense in depth.

### 8.14 Front Desk as the first decoupled service

Of all the components, Front Desk / OPD intake is the cleanest
extraction target. It already isolates itself from the tracking
pipeline:

| Property | Front Desk | Tracking pipeline |
|----------|-----------|-------------------|
| Mutable in-memory state | None (stateless over Postgres) | Per-camera workers + FAISS in-process |
| External dependencies | Postgres, `recognize_faces` engine call | Postgres, FAISS, OpenCV, camera sockets |
| Latency budget | ~200 ms (operator click) | ~50 ms per frame (real-time) |
| Failure domain | Self-contained — a Front Desk crash never affects tracking | Crash drops cameras until restart |
| Tables touched | `departments`, `rooms`, `opd_visits`, `token_counters`, `visit_status_history` | `users`, `patient_sessions` |
| Touches FAISS? | Read-only via `recognize_faces` | Read + write (registrations + matches) |

**Migration sketch (~1 sprint):**

1. Lift [`backend/app/services/frontdesk*.py`](../backend/app/services/) + [`backend/app/api/routes/frontdesk*.py`](../backend/app/api/routes/) + [`backend/app/db/frontdesk_models.py`](../backend/app/db/frontdesk_models.py) into a separate FastAPI app (`frontdesk-svc`) with its own Dockerfile.
2. Keep `User` model identical in both services (read-only on front-desk side) until the data-layer redesign (§3).
3. Replace the in-process `recognize_faces` call with `POST /recognize` to the recognizer service over HTTP. Same JSON shape, same FAISS index.
4. WebSocket bridge stays in the main app; front-desk publishes `frontdesk.visit.*` events via a thin internal webhook (or `EventBus.publish` once §8.13 lands).

Risk is low: front-desk has all I/O mediated by service functions —
no clever shortcuts into FAISS or process-shared state to unwind.

---

## 9. Migration plan V1 → V2

> Strangler-fig, no big-bang rewrite. Each step independently
> shippable and revertible.

### Phase 0 — Foundations (1 week)
- Wire Prometheus + structured logs to both code paths now (cheap, needed regardless).
- Pin tunables to env vars (similarity threshold, queue sizes).
- Build the regression test suite (§6.4).

### Phase A — Abstract the bus (1 week)
- Introduce `EventBus` + `InProcessBus` (existing queues, wrapped).
- Replace direct queue access in `event_bridge.py`, `event_processor.py`, `pipeline_service.py` with bus calls.
- No behaviour change. Tests should pass unchanged.

### Phase B — Redis-backed bus, dual-run (1 week)
- Add `RedisBus`. Run `SYNORA_BUS=redis` in dev with a local Redis container.
- Smoke: events flow, WS still receives, latency < 50 ms p95 added vs in-process baseline.
- Keep `InProcessBus` as default; opt-in only.

### Phase C — Split the processes (2 weeks)
- New entrypoint `backend/pipeline/service.py` boots only the camera stack. Drop `START_CAMERA_SYSTEM` from FastAPI.
- Two systemd units (or two containers) on the **same host** to start.
- Validate: API restart no longer kills camera workers; camera worker crash no longer kills API; metrics still flow.

### Phase D — Cross-host (1 week)
- Move pipeline-service to GPU box, api-service to CPU box.
- Shared volume for FAISS (NFS / SMB / Ceph) — measure save+reload latency before/after.
- Switch `cmd:refresh_faces` push on; remove mtime poll.

### Phase E — Containerise (1 week)
- `Dockerfile.api`, `Dockerfile.pipeline`, `docker-compose.yml`.
- CUDA base image only on pipeline image (saves ~3 GB on API image).
- CI builds + smoke-test compose stack on every PR.

### Phase F — Horizontal API (2 weeks)
- nginx in front, ≥ 2 api-service replicas.
- WebSocket fan-out via Redis (§8.11).
- Validate sticky sessions are not required.

### Phase G — FAISS upgrade (1 week)
- Implement IVF/IVFPQ factory (§1.1).
- Async write batching (§1.2).
- Add embedding versioning column (§3.3).

### Phase H — Hot-state to Redis (2 weeks)
- Stand up Redis presence projection.
- Dual-write presence (Postgres + Redis) for one week.
- Cut reads over to Redis; keep Postgres for cold history.

### Phase I — Embedding as a service (3 weeks)
- Extract embedding into its own process exposing gRPC.
- Implement micro-batching.
- Camera workers call it instead of in-process.

### Phase J — Sightings to Timescale (2 weeks)
- New consumer writes to Timescale.
- Backfill last 30 days from Postgres.
- Switch reads.

### Phase K — Horizontal sharding (4–8 weeks)
- Stateless API behind a load balancer (already in place by Phase F).
- FAISS sharded by site; matching service does fan-out.
- K8s deployment with GPU pods + KEDA.

**Total runway:** ~9 weeks part-time, ~5 weeks full-time to reach
Tier-2 scale (Phases 0–F + G). Tier-3 adds another ~6–10 weeks.

---

## 10. Risk register

| Risk | Where | Mitigation |
|------|-------|-----------|
| FAISS write blocks request thread | [`face_db.py`](../backend/app/db/face_db.py) | §1.2 async writer |
| Single point of failure: one host runs API + cameras | [`main.py`](../backend/app/main.py) | §2.4 / §8 split services |
| `--workers > 1` would spawn duplicate camera systems | uvicorn deploys | §2.4 + drop `START_CAMERA_SYSTEM` |
| Postgres becomes hot-path bottleneck | presence reads per frame | §3.1 Redis projection |
| FAISS index file corruption on crash mid-save | `face_db.save()` | atomic tmp+rename (already in place), periodic backup |
| Model upgrade invalidates index silently | embedding versioning absent | §3.3 |
| Camera worker leak takes down API | shared process tree | §8 split |
| Silent frame drops under load | tiny queues, no metric | §5.2 dropped-frames counter |
| Audit / soft-delete gap | no `deleted_at` or audit log | §3.4 |
| Threshold drift after model retrain | hard-coded `0.60` | env var + per-camera override + canary deploys |
| Redis becomes a SPOF | new bus dependency | Redis Sentinel or managed Redis (Elasticache, Upstash) before production cutover |
| FAISS shared-FS lock confusion under NFS | §8.10 Option A | Redis distributed lock; document single-writer rule |
| Bus message loss (Pub/Sub is at-most-once) | §8.6 | Streams + consumer groups for entry_exit; Pub/Sub fine for transient detection broadcasts |
| Schema drift between services | shared types | Versioned `backend/shared/schemas/`; CI check |
| WebSocket fan-out floods Redis | replica scaling | `MAXLEN` on streams; throttle in [`ws_throttle.py`](../backend/camera/ws_throttle.py) |
| Inter-service auth missing today | open Redis / open HTTP | Phase D includes shared-secret header + Redis ACLs |
| Cross-host clock skew breaks event ordering | NTP discipline | NTP mandatory; envelopes carry monotonic event_id |
| Pipeline restart loses in-flight events | bus replay | Streams replay from last consumed id; consumer groups handle this naturally |

---

## 11. Front Desk / OPD backlog

The OPD intake feature shipped in May 2026. Items deliberately
deferred:

| Item | Why deferred | Effort |
|------|--------------|--------|
| **Auth on `/frontdesk/admin/*`** | Repo assumes deployment behind reverse-proxy / VPN. Add OAuth or API-key middleware. | 1–2 d |
| **ESC/POS direct thermal printing** | V1 uses `window.print()` + print CSS. Direct ESC/POS gives exact 80 mm layout and faster cuts; needs a desktop helper (`python-escpos`) or Electron shim. | 3–4 d |
| **SLA reports (wait time, consult time)** | `VisitStatusHistory` already captures every transition; need only the aggregation UI. | 2–3 d |
| **Cross-reference visit ↔ presence** | Join `opd_visits.room` with `PatientSession.current_camera` to verify "did the patient actually reach the assigned room?" | 3–4 d |
| **Multi-day visit search / global filter** | History page joins per-date today; global "find this token" needs a dedicated index on `(token_number)` and a search route. | 1–2 d |
| **Department-scoped queue boards** | Read-only TV view filtering by department / floor — reuses `frontdesk.visit.*` WS events. Useful for waiting rooms. | 2–3 d |
| **Doctor self-service consult flow** | Doctor in-room view that pulls the next `WAITING` token, advances to `IN_CONSULT`, takes notes, marks `DONE`. Needs role-based auth. | 1 wk |
| **Default departments seeded on `init_db`** | First-boot UX: pre-seed "General OPD" + "Room 1" so the admin doesn't start at a literal blank slate. | 30 min |
| **Backfill MRN / face from manual search** | When `search_patients` returns a hit but the user has no FAISS embedding, offer a one-click "register face from this frame" path. | 0.5 d |

---

## 12. Appendix — file-level TODOs

- [`backend/app/db/face_db.py`](../backend/app/db/face_db.py) — IVF factory, atomic save, version tag.
- [`backend/app/services/face_service.py`](../backend/app/services/face_service.py) — split into register / recognize / reconcile.
- [`backend/app/services/pipeline_service.py`](../backend/app/services/pipeline_service.py) — own the async-write flusher.
- [`backend/app/services/presence_cache.py`](../backend/app/services/presence_cache.py) — back with Redis.
- [`backend/camera/manager.py`](../backend/camera/manager.py) — separate-process deploy mode.
- [`backend/camera/ai_worker.py`](../backend/camera/ai_worker.py) — talk to embedding service over gRPC.
- [`backend/camera/event_processor.py`](../backend/camera/event_processor.py) — pluggable sink registry.
- [`backend/camera/metrics.py`](../backend/camera/metrics.py) — Prometheus exposition.
- [`backend/config.py`](../backend/config.py) — runtime tunables to env vars.
- [`backend/engine/pipeline/embedding.py`](../backend/engine/pipeline/embedding.py) — fp16 + TensorRT provider, batch API.
- [`backend/engine/pipeline/matching.py`](../backend/engine/pipeline/matching.py) — accept sharded FAISS handle.

---

## Decisions locked in (no longer open)

- ✅ **Bus = Redis 7** (Streams + Pub/Sub).
- ✅ **FAISS = §8.10 Option A** — shared file + `cmd:refresh_faces` push, Redis distributed write lock.
- ✅ **Streaming bypasses Redis** — pipeline-service serves MJPEG directly, api-service proxies.
- ✅ **WebSocket fan-out via Redis** — every api replica subscribes; no sticky sessions.
- ✅ **Migration starts with Phase 0** (observability) before any structural change.
- ✅ **Front Desk** is the first decoupled service in Phase C.

Open: choice of Redis hosting (self-managed vs managed) — operational,
not architectural.

---

_Verified against the codebase on 2026-05-25._
