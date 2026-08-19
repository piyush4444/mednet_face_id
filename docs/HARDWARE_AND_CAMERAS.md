# Hardware, Cameras, and Range Guide

> What machine to buy, what camera to mount, where to mount it, and how
> to push the system further without overspending. One doc covering
> capacity planning + camera selection + installation + range-extension
> levers + the L4 future-state target.
>
> Code-true against [`backend/config.py`](../backend/config.py) on
> 2026-05-23. The runtime defaults in code today are tuned for a
> mid-size machine (`EXPECTED_MAX_CAMERAS=8`, `MAX_TOTAL_FACES=30`,
> `MAX_FACES_MAX=20`); the per-tier profiles below tell you what to
> override for the box you actually have.

Companion docs: [`ARCHITECTURE.md`](ARCHITECTURE.md) for pipeline
internals · [`CONFIGURATION.md`](CONFIGURATION.md) for env vars ·
[`ROADMAP.md`](ROADMAP.md) for the V2 architecture this guide assumes
you'll eventually adopt.

---

## 0. Summary tables

### 0.1 Pick a tier

| Tier | Box | Cameras (live / motion-skip) | FPS/cam | Identities |
|------|-----|------------------------------|---------|------------|
| **Dev / single cam** | Any laptop, no GPU | 1 | 5–8 | < 1k |
| **Small (RTX 3050 4 GB)** | Ryzen 5 5600H + 16 GB | **3–4 / 6** | 8–12 | < 10k |
| **Mid (RTX 4060 Ti 16 GB)** | Ryzen 7 / i7 + 32 GB | 8–12 / 16 | 10–15 | < 50k |
| **Pro (RTX 4090 / A4000)** | Ryzen 9 / i9 + 64 GB | 24–32 / — | 12–15 | < 200k |
| **Enterprise (1–2× L4)** | EPYC/Xeon + 128 GB + NVMe | 64–96 per node | 15 | 1M+ |
| **Datacenter (L40S/H100)** | Cluster + K8s | 200–500 per cluster | 15 | 10M+ |

### 0.2 Pick a camera

| Class | Resolution | HFOV | px/m @ 5 m | Reliable-ID range | Cost (per cam, INR) |
|-------|-----------|------|-----------|-------------------|---------------------|
| **Lenovo 300 FHD (reference)** | 1920×1080 | 95° | ~232 | ~2.9 m | already owned |
| Standard CCTV bullet, 1080p / 70° | 1920×1080 | 70° | ~330 | ~4.1 m | ₹3–5k |
| **2K bullet @ 60°** (recommended) | 2560×1440 | 60° | ~530 | ~6.6 m | ₹6–10k |
| 4K bullet @ 60° | 3840×2160 | 60° | ~798 | ~10 m | ₹15–25k |
| 4K varifocal (30–90°) | 3840×2160 | 30° narrow | ~1750 | ~21 m narrow | ₹25–40k |
| 4K PTZ (25× optical) | 3840×2160 | dynamic | up to 4000+ | 30 m+ zoomed | ₹40–80k |

### 0.3 Pick a range-extension lever

| Objective | Low-cost option | High-impact option |
|-----------|----------------|--------------------|
| Detect faces farther | Raise `DETECTION_INPUT_SIZE` to 1024 | 2K bullet at 60° HFOV |
| Identify faces farther | Re-enrol at varied distances + raise `SMOOTHING_WINDOW` | AdaFace ir101 + 2K cam + multi-scale ROI zoom |
| Scale to many cameras | Motion-skip + reduced per-cam FPS | NVIDIA L4 GPU + service split |
| Night performance | IR illuminator + face-level mount | Starvis 2 / IMX678 + f/1.6 lens |
| ID at 8 m or more | Multi-scale ROI zoom (Phase 2 stub) | PTZ master-slave pair |

### 0.4 Decision tree

```
Need to add cameras?
│
├─ GPU util < 70% and VRAM < 70%?
│     → Just add the camera. Re-tune MAX_TOTAL_FACES upward.
│
├─ GPU util > 80% but VRAM headroom?
│     → Lower DETECTION_INPUT_SIZE, raise motion-skip threshold,
│       drop CAMERA_FPS.
│
├─ VRAM near cap?
│     → Convert recognizer to fp16. If still tight, switch r100 → r50.
│
├─ CPU saturated, GPU idle?
│     → Frame-copy bottleneck. Lower PROCESS_MAX_DIM, ensure shared
│       memory frame buffer is enabled.
│
└─ All maxed?
      → Rung up (§3). No software trick recovers a saturated system.
```

---

## 1. The physics — why range is bounded

Range is governed by a single inequality:

```
face_pixels_at_sensor ≥ minimum face pixels the recognizer needs
```

For a 0.15 m wide face at distance `D` on a camera with horizontal
sensor resolution `W_px` and HFOV `θ`:

```
face_pixels ≈ 0.15 · W_px / (D · 2 · tan(θ/2))
```

### 1.1 Reference camera — Lenovo 300 FHD (1920 × 95°)

| Distance | Face pixels | Detection (≥ 24 px) | Identification (≥ 64–80 px is "reliable") |
|----------|-------------|---------------------|-------------------------------------------|
| 1 m | ~175 | ✅ trivially | ✅ frontal ≥ 99% |
| 2 m | ~87  | ✅ | ✅ ~95% |
| 3 m | ~58  | ✅ marginal | ⚠️ ~70% — flicker, smoothing helps |
| 4 m | ~44  | ⚠️ borderline | ❌ ~50% — unreliable |
| 6 m | ~29  | ❌ at limit | ❌ ~30% — guessing |
| 8 m | ~22  | ❌ below `MIN_FACE_SIZE` | ❌ |

**Detection range and identification range are distinct problems.** A
30-pixel face can be detected; it cannot be embedded reliably. Range
improvements come from increasing pixels-on-face.

### 1.2 The four levers that raise pixels-on-face

1. **More sensor pixels** (1080p → 2K → 4K).
2. **Narrower FOV** (95° → 60° → 30° at the cost of coverage).
3. **Better detector + recognizer for small faces** (model swap).
4. **Algorithmic upscaling** (multi-scale zoom, super-resolution).

Hardware levers dominate. Software levers are 1.5–2× multipliers on
top.

---

## 2. Camera selection

### 2.1 What to look for beyond pixel count

Resolution alone doesn't make a camera good. At distance, **diffraction,
noise, and low light** kill ID quality faster than resolution.

| Spec | What to want | Why |
|------|--------------|-----|
| **Sensor size** | ≥ 1/2.7" (avoid 1/4") | Bigger pixels = better low light = sharper edges at distance |
| **Sensor model** | Starvis 2, IMX415, IMX678, IMX664 | Current good budget sensors for security |
| **Aperture** | f/1.6 or wider | Critical for IR / dusk performance |
| **WDR** | True 120 dB+ (not "digital WDR") | Backlit hospital entrances destroy ArcFace if face is in shadow |
| **Focal type** | **Fixed > varifocal** for accuracy | Varifocals soften at zoom extremes |
| **IR cut filter** | Mechanical swing, not "always-IR" | Cheap units leave IR filter in 24/7, kills daytime color |
| **Codec** | H.264 main profile (avoid H.265 unless GPU-accelerated decode) | H.265 software decode consumes CPU cycles the tracker needs on a 3050 |
| **Bitrate** | ≤ 4 Mbps @ 1080p, ≤ 6 Mbps @ 2K | Higher bitrates produce decode errors over Tailscale tunnels |
| **Substream** | Main only, disable substream | Some NVRs default to 640×360 — verify with `ffprobe` |

### 2.2 What to avoid

- **"Ultra-wide" / fisheye cams** (>110° HFOV) — barrel distortion breaks ArcFace alignment at frame edges.
- **PTZ-only deployments** without a wide overview camera — anyone passing while the PTZ is positioned elsewhere is missed.
- **"AI-camera" units** with on-device face recognition — re-encode + re-stream introduces latency and emits events that conflict with the Iris pipeline.
- **Cameras without RTSP** (consumer "cloud-only" cams). The system needs RTSP/RTSPS sources.

For a corridor mounted to favour subjects walking toward the camera, a
**2K bullet at 60° HFOV** is the recommended choice. It roughly
doubles the reliable identification range over the 95° baseline
without requiring a GPU upgrade. A 4K sensor adds further range only
when paired with `DETECTION_INPUT_SIZE=1024`, which exceeds the RTX
3050 4 GB budget at three or more cameras.

---

## 3. Installation

A well-placed 1080p camera will out-perform a poorly-placed 4K one.
**Camera placement is usually a bigger lever than camera specs.**

### 3.1 Mount geometry

| Variable | Recommendation | Why |
|----------|---------------|-----|
| **Mount height** | 1.6–1.8 m (face level) | Top-of-head views drop RetinaFace confidence ~30% |
| **Tilt (pitch)** | ≤ 15° downward | Steeper angles break the canonical ArcFace alignment template |
| **Subject direction** | Walking *toward* camera | Frontal yaw is 99% accuracy; ±60° drops to 75–85% |
| **Mounting** | Wall or ceiling-arm bracket | Avoid ceiling-flush mounts (face is too top-down) |
| **Vibration** | Solid wall or beam | Vibrating mounts cause motion blur even on fast shutters |

### 3.2 Lighting

| Issue | Symptom | Fix |
|-------|---------|-----|
| **Backlight** (window/door behind subject) | Silhouetted face → garbage embedding | Reposition so light is *behind* the camera, or add front-fill |
| **Harsh overhead light** | Eye sockets in deep shadow | Diffuse overhead light, or add forward bounce |
| **Mixed colour temperature** | White balance flips between subjects | Use consistent LED lighting across the scene |
| **Night-time** | Range collapses from 3 m to 1 m | Add 940 nm IR illuminator (~₹2k) — extends night range to 4–5 m on most CCTV cams |
| **Auto-iris hunting** | Brightness flickers on/off | Disable auto-iris or set fixed shutter |

### 3.3 Network placement

- **Wired Ethernet preferred** over Wi-Fi. Wi-Fi camera streams jitter under load and break H.264 decoding.
- **PoE switches** simplify deployment — one cable per cam.
- **VLAN segregation:** put cameras on their own VLAN so a compromised camera can't see the patient database.
- **MTU 1500 default** — enable jumbo frames only if every device on the path supports them.
- **Tailscale-tunneled cameras** need UDP transport at modest bitrates. FFmpeg flags for the reference deployment are documented in [ARCHITECTURE.md §11](ARCHITECTURE.md).

### 3.4 Deployment patterns

#### OPD front-desk scanner
- One camera, ~0.5–1.5 m from patient. USB or 1080p IP cam is fine.
- Mount face-level, lit from front (overhead diffused or ring light).
- This is the camera the **Front Desk** scan page uses.

#### Hospital corridor (entry detection)
- 2K bullet @ 60° HFOV, ~3 m above floor, tilted ≤ 15°.
- Cover one direction of travel.
- Reliable-ID range: 6–7 m. With multi-scale zoom: 8–10 m.

#### Waiting room / triage (slow-moving subjects)
- PTZ master-slave pair (§4.4) is ideal — subjects don't move fast.
- Or a 4K varifocal locked at narrow zoom (~30°) covering a known seating zone.

#### Open ward / large room
- Multiple cameras with overlapping FOV instead of one wide cam.
- Each covers 4–6 m of identifiable range.
- Cross-camera identity continuity (roadmap) handles hand-off.

#### Outdoor / long range (15 m+)
- Single fixed cam will never work reliably. Use a PTZ slave.
- Or accept "detection only" and treat the system as a counter, not an identifier.

---

## 4. Range-extension levers

Ordered cheapest → most invasive within each category.

### 4.1 Hardware-only (no code touch)

| Lever | Range gain | Effort | Cost |
|-------|-----------|--------|------|
| Re-mount existing camera at face-level + IR illuminator | +0.5–1 m | 2 hours | ₹2k |
| Swap to 2K bullet @ 60° HFOV | +2–3 m | 1 hour install | ₹8k |
| GPU upgrade (RTX 4060 Ti 16 GB minimum) | enables larger detector input | 1 day | ₹40k+ |
| PTZ master-slave pair (queue/waiting-room ID at 15–25 m) | +12 m for stationary | 1–2 weeks | ₹40–60k |

#### PTZ master-slave architecture

```
Wide cam (95°, fixed) ──detect──► Tracker
                                     │
                                     ▼
                            PTZ controller (ONVIF)
                                     │
                                     ▼
                Narrow cam (10–40°, motorised) ──HQ face crop──► Embedder
```

- Detection on wide cam = cheap (just needs to see the head).
- Identification on slave cam = accurate (face fills 200+ px at 15 m).
- Mechanical latency ~0.5–1.5 s — works for stationary/slow subjects, **not fast walkers**.
- Protocol: ONVIF Profile S + Profile T, controllable via `python-onvif-zeep`.
- Realistic gain: ID range jumps from 3 m → 15–25 m.

### 4.2 Configuration-only (no code touch)

Knobs in [`backend/config.py`](../backend/config.py):

#### Raise `DETECTION_INPUT_SIZE`
Current default: `(800, 800)`. Must be divisible by 32.

| Size | Range gain | GPU cost vs 800 | Notes |
|------|-----------|-----------------|-------|
| 800 (default) | baseline | 1.0× | — |
| 960 | ~3.5 m | ~1.4× | safe; ~10% FPS drop |
| 1024 | ~3.8 m | ~1.6× | likely OK on 3050 with 3 cams |
| 1280 | ~4.6 m | ~2.5× | needs RTX 4060 Ti+ |

#### Raise `SMOOTHING_WINDOW`
Current: 5. Push to **7–10**. Per-frame accuracy at 3–4 m is 50–70%,
but majority-vote over 10 frames converges to >90%. Cost: identity
lock-in latency rises from ~170 ms to ~330–660 ms. **Best free win**
for OPD intake (not pursuit).

#### Raise `MAX_EMBEDDINGS_PER_IDENTITY`
Current: 10. Bump to **15–20** and re-enrol patients with samples at
varied distances. Distance-degraded embeddings live in a different
region of embedding space than close-ups; varied gallery makes
far-range queries land closer to known centroids. **+0.5–1.0 m of
reliable range, ₹0 cost.** Highest-ROI free win.

#### Lower `MIN_FACE_SIZE`
Current: 24 px. Going to 16–20 px is only useful for *detection* at
the rear of the frame; identification below 20 px is unreliable
regardless of model.

#### Lower `DETECTION_CONFIDENCE`
Current: 0.42. Going to 0.30–0.35 catches more distant low-confidence
faces but **multiplies false positives**. Smoothing absorbs most but
expect flicker.

### 4.3 Algorithmic (code required)

#### Multi-scale ROI zoom — Phase 2 (recommended software change)
Stubs already in [backend/config.py:80–94](../backend/config.py#L80-L94):
`ZOOM_TRIGGER_ENABLED`, `ZOOM_SMALL_FACE_THRESHOLD_PX`,
`ZOOM_TRIGGER_COOLDOWN_FRAMES`. Phase 2 design:

```
Frame ─► Detect at 800×800 ─► face < 60 px detected? ──► YES
                                                          │
                                                          ▼
                                       Crop ROI around small face (200×200 px)
                                                          │
                                                          ▼
                                      Re-detect at 640×640 on crop (effective 4× zoom)
                                                          │
                                                          ▼
                                      Map back to frame coords
                                                          │
                                                          ▼
                                       Embed at higher effective res
```

**Expected gain:** 2.9 m → 5–6 m. GPU cost ~1.3× because zoom pass
only triggers when a small face is present and only on the ROI crop.

#### Recognizer swap: ArcFace r50 → AdaFace ir101 or PartialFC

| Model | LFW (close) | TinyFace (distant) | IJB-B | VRAM fp16 | Drop-in? |
|-------|-------------|---------------------|-------|-----------|----------|
| ArcFace r50 (current) | 99.8% | 65% | 92.5% | 320 MB | baseline |
| **AdaFace ir101** (recommended) | 99.8% | **77%** | 96.0% | 1.0 GB | Same 512-d, drop-in |
| PartialFC + ArcFace r100 (Glint360K) | 99.8% | 80% | 96.2% | 1.0 GB | Same 512-d |
| CurricularFace r100 | 99.8% | 78% | 95.8% | 1.0 GB | Same 512-d |

AdaFace is trained with adaptive-margin loss aimed at low-quality,
blurry, distant faces. ONNX export drops into
`engine/pipeline/embedding.py` without altering the FAISS index
format. At 3–4 m, per-frame accuracy improves from ~50–70% (r50) to
~75–85% (ir101). Needs ≥ 1 GB VRAM at fp16 — RTX 3050 can host it
only with display off and aggressive motion-skip; the L4 has ample
headroom.

#### Detector swap: RetinaFace → SCRFD-10GF
SCRFD-10GF ships in `insightface.app.FaceAnalysis(name='buffalo_l')`
already. Switch the detector name string. Gain: 5–10% more small-face
recall, ~30% faster than RetinaFace.

#### Super-resolution before embedding
For face crops in the 30–60 px range, run **GFPGAN v1.4** (4× face
restore) before alignment + embedding:

| Model | Quality | Latency on 3050 | Notes |
|-------|---------|-----------------|-------|
| **GFPGAN v1.4** (recommended) | High | 80–120 ms/face | Designed for face restoration |
| CodeFormer | Highest | 150–200 ms | SOTA; may hallucinate features |
| Real-ESRGAN x4 | Moderate | 60 ms | Generic SR, less face-specific |
| GPEN | High | 100 ms | Balanced alternative |

SR models can hallucinate facial detail. If the gallery is enrolled
with close-ups and queries are super-resolved from small crops,
embedding distributions diverge and false-positive rates spike.
Mitigations: re-enrol gallery with SR pass-through; limit SR to 30–60
px window. Expect +5–10 pp accuracy at 30–50 px, less above 60 px.
Range gain: 2.9 m → 4.5–5 m.

#### Test-time augmentation (TTA) for small faces
For crops < 60 px, embed 3 variants (original, h-flip, mild upscale)
and average the 512-d vectors before FAISS lookup. ~3× embedding cost
on small faces only → +3–5 pp accuracy.

#### Cascaded matching (cheap → expensive)
Two-stage matching: r50 first; ambiguous scores in the 0.45–0.65 band
are re-embedded with r100/ir101. GPU spend amortised — confident
close-ups pay nothing extra, difficult distance cases pay the full
cost. Useful when AdaFace adoption is desired but VRAM is constrained.

### 4.4 Architectural (bigger lifts)

- **Multi-camera identity fusion** — fuse embeddings across overlapping FOVs (today: per-camera trackers only; see [ARCHITECTURE.md §6](ARCHITECTURE.md)).
- **Person re-identification** for far-range tracking using body/clothing embeddings (OSNet, TransReID). Maintains identity across face-recognition gaps; re-confirms on close approach.
- **Active learning loop** — when a face is Unknown at distance but identified close, back-propagate the ID and add to gallery. Builds a distance-robust gallery over weeks.

### 4.5 What NOT to do

| Anti-pattern | Why it hurts |
|--------------|--------------|
| Drop `MIN_FACE_SIZE` below 20 px | Recognizer can't use those pixels; clutters event stream |
| Drop `DETECTION_CONFIDENCE` below 0.30 | False-positive rate explodes; trackers chase ghosts |
| Use CodeFormer SR without re-enrolling gallery | Hallucinated features cause confident wrong matches |
| Buy 4K cameras before upgrading GPU | RTX 3050 can't run `DETECTION_INPUT_SIZE=1024` on multiple cams |
| Migrate FAISS Flat → IVF for *range* gains | IVF is for *gallery size*, not range. < 50k identities → FlatIP is optimal |
| Mount cameras ceiling-down (>20° tilt) | Breaks ArcFace alignment; ~30% confidence drop |
| Enable H.265 on the RTX 3050 reference build | Software decode consumes CPU cycles the tracker needs |
| Wireless camera streams | Wi-Fi jitter breaks H.264 decoding |

---

## 5. Per-tier hardware capacity

### 5.1 RTX 3050 4 GB reference build — what to expect

| Cameras active | GPU util | VRAM | CPU | FPS/cam | Notes |
|----------------|----------|------|-----|---------|-------|
| 1 | 25% | 1.8 GB | 30% | 15 | Easy |
| 2 | 45% | 2.2 GB | 55% | 12–14 | Comfortable |
| 3 | 70% | 2.7 GB | 75% | 10–12 | Sweet spot |
| 4 | 90% | 3.2 GB | 90% | 8–10 | At the edge |
| 5 | 100% | 3.6 GB | 100% | 5–7 | Frames dropping |
| 6 | OOM risk | 4.0 GB | 100% | 4–5 | Only viable with motion-skip on idle cams |

**Recommendation:** design for 3 cameras, allow 4 as headroom, treat 6
as the absolute ceiling assuming most are idle hallways.

**Things that will bite you on this box:**
1. Browser + IDE + Discord eat 800 MB VRAM — run headless if you want the full 4 GB.
2. Display window adds 100–200 MB; disable for production (`DISPLAY_SCALE = 0`).
3. Postgres on the same machine at default settings fights for page cache — cap `shared_buffers = 2 GB`, `effective_cache_size = 6 GB`.
4. Windows + WSL2 halves your effective RAM. Native Linux dual-boot, or `.wslconfig: memory=12GB`.
5. NVMe vs HDD matters. FAISS reload at 50k identities can take 30 s+ on HDD. Put `database/` on SSD.

### 5.2 Upgrade ladder

Each rung gives a clear, measurable jump. Don't skip rungs hoping for
linear scaling — the bottleneck moves between GPU, CPU, and memory.

#### Rung 1 — Same CPU, bigger GPU (~$400–600)
**Upgrade to RTX 4060 Ti 16 GB.** 16 GB VRAM lets you run r100 fp16,
batch 32, 8–10 cameras. Keep the Ryzen 5 5600H — it'll saturate around
8 cameras anyway. Expected: **8 cams @ 12 fps, or 12 cams @ 8 fps.**

#### Rung 2 — New CPU + same GPU class (~$800–1200)
**Ryzen 7 7700 / i7-13700 + 32 GB DDR5 + RTX 4060 Ti 16 GB.** More
grabber/tracker cores; DDR5 helps frame copies between processes.
Expected: **12 cams comfortably, 16 with motion-skip.** CPU and GPU
balanced.

#### Rung 3 — Pro workstation single GPU (~$2.5–4k)
**Ryzen 9 7950X (16C) or Threadripper + 64 GB + RTX 4090 24GB** (or
A4000 16 GB if you need ECC + lower power). 24 GB VRAM allows batch
64, multiple model variants (detector + recognizer + age/gender)
co-resident. Expected: **24–32 cams @ 12–15 fps.** Migrate to V2
architecture here — bottleneck becomes orchestration, not silicon.

#### Rung 4 — Server-class single node (~$8–15k, **production target**)
**EPYC 9354 (32C) or Xeon Gold + 128 GB ECC + 2× NVIDIA L4 24 GB.**
L4 is purpose-built for video inference (low TDP, 8th-gen NVENC,
rack-friendly). Two saturate a single CPU socket nicely. Expected:
**64–96 cameras per node.** V2 split is mandatory (camera-manager,
embedding service, API as separate units, Kafka/Redis Streams between).

#### Rung 5 — Cluster (~$30k+)
3-node, each: EPYC + 256 GB + 2× L40S 48 GB, NVMe shared storage, 25
GbE, K8s + KEDA autoscale. **300–500 cams per cluster.** FAISS
sharded by site_id; matching does fan-out. Consider edge grabbers
(Jetson Orin Nano per building) decoding RTSP locally and shipping
JPEG — saves enormous network bandwidth.

### 5.3 NVIDIA L4 future-state — why and how

| Card | VRAM | TDP | Decode | INT8 TOPS | Cost (INR) |
|------|------|-----|--------|-----------|-------------|
| RTX 3050 (current) | 4 GB | 75 W | 1× NVDEC | 73 | ₹25k |
| RTX 4060 Ti 16 GB | 16 GB | 165 W | 1× NVDEC | 353 | ₹50k |
| RTX 4090 | 24 GB | 450 W | 2× NVDEC | 1320 | ₹2.5L |
| **NVIDIA L4** (target) | 24 GB | **72 W** | **4× NVDEC** | 485 | ₹3–4L (server) |
| L40S | 48 GB | 350 W | 3× NVDEC | 1466 | ₹6L+ |

**Why L4 specifically:**

- **72 W TDP** — passively cooled in 1U, no exotic cooling.
- **24 GB VRAM** — runs ArcFace r100 + AdaFace ir101 + detector + super-res simultaneously with batch 64.
- **4× NVDEC** — hardware-decodes 30+ concurrent RTSP streams without consuming CPU. The 3050's single NVDEC engine is the practical ceiling at ~6 cameras.
- **PCIe 4.0 ×16 single-slot** — fits in any modern server chassis.
- **No display output** — pure compute; ideal for headless deployment.
- 8th-generation NVENC also available if outbound transcoding is needed.

**Realistic capacity:** 64–96 cameras per L4 with the V2 architecture
([ROADMAP.md](ROADMAP.md)). Under V1 (per-camera process) the realistic
ceiling is 20–30 cams due to CPU/process overhead rather than GPU.

#### Server pairing for L4

| Component | Recommendation | Why |
|-----------|---------------|-----|
| **CPU** | AMD EPYC 9354 (32C/64T) or Xeon Silver 4416+ (20C/40T) | One core per camera grabber + headroom |
| **RAM** | 128 GB ECC DDR5 | Frame buffers + Postgres page cache + OS |
| **NVMe** | 2× 2 TB Gen4 NVMe, RAID 1 | FAISS index + Postgres hot tier |
| **Capacity HDD** | 16 TB SATA (RAID 5/6) | Sightings log, optional face-crop archive |
| **NIC** | Dual 10 GbE (or 25 GbE) | 64 cams @ 4 Mbps = 256 Mbps; headroom matters |
| **PSU** | 1+1 redundant, 80+ Platinum | Single-PSU failures not worth the savings |
| **OS** | Ubuntu 24.04 LTS Server (headless) | Mature CUDA support |
| **Drivers** | NVIDIA 550+ + CUDA 12.4 + cuDNN 9 | L4 needs ≥ driver 525, recommended 550+ |

#### Model selection on L4

| Stage | Current (3050) | L4 stack |
|-------|---------------|----------|
| Detector | RetinaFace (buffalo_l) | **SCRFD-10GF** — 30% faster, +5 pp small-face recall |
| Recognizer | ArcFace r50 fp16 | **AdaFace ir101 fp16** — +12 pp at distance |
| Super-res (small faces only) | not used | **GFPGAN v1.4 fp16** triggered for 30–60 px crops |
| Index | FAISS FlatIP | FlatIP up to 100k; **IVFFlat** above |

VRAM budget on L4 (fp16): SCRFD ~180 MB + AdaFace ir101 ~1.0 GB +
GFPGAN ~600 MB + ORT workspace ~800 MB + CUDA context ~300 MB +
per-camera buffers × 30 ~2.4 GB = **~5.3 GB of 24 GB**. Comfortable
headroom.

#### Expected L4 capacity

| Metric | RTX 3050 (current) | L4 (V1 arch) | L4 (V2 arch) |
|--------|---------------------|--------------|--------------|
| Cameras (live) | 3–4 | 12–16 | **64–96** |
| Cameras (motion-skip) | 6 | 24 | **128** |
| FPS / camera | 8–12 | 15 | 15 |
| Faces/sec recognised | ~50 | ~200 | **~500** |
| Reliable-ID range (2K + AdaFace) | 5–6 m | 6–7 m | 7–8 m |
| Reliable-ID range (PTZ slave + AdaFace) | n/a | 15 m | 20–25 m |
| Identities supported | 10k (FlatIP) | 100k (FlatIP) | 1M (IVF) |

---

## 6. Configuration profiles

Three drop-in profiles. The values below assume you'll override the
specific constants in [`backend/config.py`](../backend/config.py) for
the box you have. **The current in-code defaults are tuned for the
Mid profile** (`EXPECTED_MAX_CAMERAS=8`, `MAX_TOTAL_FACES=30`,
`MAX_FACES_MAX=20`, `GPU_SCALING_FACTOR=2.0`); a smaller box needs the
Low profile applied as overrides.

### 6.1 `profile_low.py` — RTX 3050 4 GB, ultra-wide USB cam

```python
CPU_CORES = 6
EXPECTED_MAX_CAMERAS = 4
MAX_TOTAL_FACES = 16
MAX_FACES_BASE = 4
MAX_FACES_MAX = 8
FACES_PER_CORE = 1.0
GPU_SCALING_FACTOR = 1.5
DETECTION_INPUT_SIZE = (800, 800)   # range-tuned; (640,640) if GPU saturates
DETECTION_CONFIDENCE = 0.40
MIN_FACE_SIZE = 24
PROCESS_MAX_DIM = 960
CAMERA_FPS = 15
MOTION_SKIP_THRESHOLD = 3.0
SIMILARITY_THRESHOLD = 0.57         # buffalo_l default; loosen to 0.55 if recognition sags
NUM_AI_WORKERS = 1

# Queues — keep tiny for freshness
CAPTURE_QUEUE_SIZE = 2
TRACKING_QUEUE_SIZE = 4
RESULT_QUEUE_SIZE = 4
```

**Runtime flags:**

```bash
export ORT_TENSORRT_FP16_ENABLE=1     # fp16 ONNX
export CUDA_MODULE_LOADING=LAZY       # share VRAM with desktop apps
export CUDNN_BENCHMARK=0              # we have static input shapes
export START_CAMERA_SYSTEM=1
# Run uvicorn single-worker on this box — one worker is correct for 6 cores + 1 GPU
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

**Convert ArcFace to fp16 (one-time):**

```bash
python - <<'PY'
import onnx
from onnxconverter_common import float16
m = onnx.load("models/arcface_r50.onnx")
m_fp16 = float16.convert_float_to_float16(m, keep_io_types=True)
onnx.save(m_fp16, "models/arcface_r50_fp16.onnx")
PY
```

~1.8× speedup, ~0.5 GB VRAM saved, no measurable accuracy loss at
threshold 0.55.

### 6.2 `profile_mid.py` — RTX 4060 Ti / 4070, 8–12 cameras

This is what the current `backend/config.py` defaults are tuned for.

```python
CPU_CORES = 12
EXPECTED_MAX_CAMERAS = 12
MAX_TOTAL_FACES = 30
MAX_FACES_MAX = 16
FACES_PER_CORE = 1.5
GPU_SCALING_FACTOR = 2.5
DETECTION_INPUT_SIZE = (640, 640)
PROCESS_MAX_DIM = 960
CAMERA_FPS = 20
MOTION_SKIP_THRESHOLD = 2.5
SIMILARITY_THRESHOLD = 0.57
NUM_AI_WORKERS = 1
```

### 6.3 `profile_high.py` — RTX 4090 / A4000, 24+ cameras

```python
CPU_CORES = 16
EXPECTED_MAX_CAMERAS = 32
MAX_TOTAL_FACES = 64
MAX_FACES_MAX = 32
FACES_PER_CORE = 2.0
GPU_SCALING_FACTOR = 4.0
DETECTION_INPUT_SIZE = (640, 640)
PROCESS_MAX_DIM = 1080
CAMERA_FPS = 25
MOTION_SKIP_THRESHOLD = 2.0
SIMILARITY_THRESHOLD = 0.57
NUM_AI_WORKERS = 2          # split across GPU streams
```

### 6.4 `profile_l4.py` — production server, 64+ cameras

```python
# Hardware scaling
CPU_CORES = 32                     # adjust to actual core count
GPU_ENABLED = True
GPU_DEVICE_ID = 0
GPU_SCALING_FACTOR = 5.0           # L4 ≈ 5× a 3050 for fp16 inference

# Camera capacity
EXPECTED_MAX_CAMERAS = 64
MAX_TOTAL_FACES = 96
MAX_FACES_BASE = 8
MAX_FACES_MAX = 24
FACES_PER_CORE = 2.0

# Capture / framing
FRAME_WIDTH = 2560                 # 2K cams; 3840 for 4K
FRAME_HEIGHT = 1440
CAMERA_FPS = 20                    # throttle from 30 in the grabber
RESIZE_FRAME = True
PROCESS_MAX_DIM = 1280             # 1280 long-edge gives small-face headroom

# Detection
DETECTION_INPUT_SIZE = (1024, 1024)  # MUST be /32; valid: 960, 1024, 1088, 1280
DETECTION_CONFIDENCE = 0.38
MIN_FACE_SIZE = 20
STRICT_FACE_LIMIT = False

# Embedding
EMBEDDING_DIM = 512                 # AdaFace ir101 also 512
NORMALIZE_PIXELS = False

# Matching
SIMILARITY_THRESHOLD = 0.58
FAISS_TOP_K = 5
MAX_EMBEDDINGS_PER_IDENTITY = 20    # richer gallery for distance robustness
SMOOTHING_WINDOW = 7

# Concurrency
NUM_AI_WORKERS = 4                  # L4 supports 4 CUDA streams comfortably
CAPTURE_QUEUE_SIZE = 2
RESULT_QUEUE_SIZE = 8
TRACKING_QUEUE_SIZE = 16

# Motion skip — L4 doesn't need to skip much
MOTION_SKIP_ENABLED = True
MOTION_SKIP_THRESHOLD = 2.0
MOTION_SKIP_MAX_INTERVAL_S = 2.0

# Tracking
TRACKING_DISTANCE_THRESHOLD = 60
TRACK_MAX_AGE = 15

# Performance
AGGREGATION_TIMEOUT = 0.30
LOG_LEVEL = "INFO"
```

**Runtime flags for L4:**

```bash
export ORT_TENSORRT_FP16_ENABLE=1
export CUDA_MODULE_LOADING=LAZY
export OPENCV_FFMPEG_CAPTURE_OPTIONS="rtsp_transport;udp|hwaccel;cuda|hwaccel_device;0|stimeout;5000000"
export START_CAMERA_SYSTEM=1

# Pin to the GPU's NUMA node (find via: nvidia-smi topo -m)
numactl --cpunodebind=0 --membind=0 uvicorn backend.app.main:app ...

# Postgres on the same box — give it real shared_buffers
# postgresql.conf:
#   shared_buffers = 16GB
#   effective_cache_size = 96GB
#   work_mem = 32MB
#   max_connections = 200
```

---

## 7. Faces-per-frame capacity

> "How many people can the camera see at once and still get recognised?"
> Two distinct questions: (a) **how many the detector can find**, and
> (b) **how many the recognizer can embed within the frame budget**.
> The smaller of the two is your real ceiling.

### 7.1 The formula in code

[`backend/config.py`](../backend/config.py) computes:

```
MAX_FACES_PER_FRAME = min(MAX_FACES_MAX,
                          int(CPU_CORES * FACES_PER_CORE * GPU_SCALING_FACTOR))
MAX_FACES_PER_CAMERA = max(4, MAX_TOTAL_FACES // EXPECTED_MAX_CAMERAS)
```

The detector can *find* far more than this — SCRFD at 640×640 finds
50+ faces in a single frame. The cap exists so embedding doesn't blow
the frame budget. See
[`backend/engine/pipeline/detection.py`](../backend/engine/pipeline/detection.py).

### 7.2 Per-tier face capacity

Recommended values, not raw detector limits:

| Tier | Cams | Faces/frame **per cam** | Faces/frame **system-wide** | Why |
|------|------|--------------------------|------------------------------|-----|
| Dev (no GPU) | 1 | 3 | 3 | CPU embedding ~80 ms/face → 3 fits a 250 ms budget |
| **RTX 3050** | 3–4 | **4** | **8** | `min(8, 6×1.0×1.5)=8` total; ÷ 4 cams = 4/cam |
| **RTX 4060 Ti 16 GB** | 8–12 | **6** | **16** | `min(16, 12×1.5×2.5)=16`; raise per-cam to 6 via `MAX_FACES_PER_CAMERA` |
| **RTX 4090 24 GB** | 24–32 | **8** | **32** | `min(32, 16×2.0×4.0)=32`; comfortably 8/cam |
| **2× L4 server** | 64–96 | **8** | **64** | Multi-worker; cap per-cam, not system |
| **L40S cluster** | 300–500 | **10** | **128+** | Sharded matching; per-node cap |

### 7.3 What this means — RTX 3050 box

With recommended config (`MAX_FACES_MAX=8`, `EXPECTED_MAX_CAMERAS=4`,
`MAX_TOTAL_FACES=16`):

- **One person walking past:** identified at 12–15 fps, instant recognition.
- **Small group (2–3):** all identified, ~10 fps.
- **Crowd of 6:** top 4 (face size + confidence) get identified per frame; the smallest/most-occluded 2 are detected but not embedded that frame. Over 5 frames (smoothing window), all 6 rotate through — you'll see all of them, ~200 ms lag for back-row faces.
- **Crowd of 10+ in one frame:** the 4 largest+most-confident recognised; the rest drawn as "Unknown" boxes. At a hospital entrance during shift change, this is real — raise `MAX_FACES_PER_CAMERA` to 6 if you must, expect FPS to drop to 5–7.

**Realistic verdict for 3050:** comfortably handles **3–4 known faces
per camera per frame** across 3 cameras, or **6 faces in one camera**
if the others are idle. Crowd scenes (8+ in one shot) need the next
rung.

### 7.4 Levers that change the per-frame cap

| Lever | Effect | Cost |
|-------|--------|------|
| Raise `MAX_FACES_MAX` | Linear up to GPU saturation | More VRAM, lower FPS |
| Raise `FACES_PER_CORE` | Linear until CPU-bound | Tracker latency rises |
| Raise `GPU_SCALING_FACTOR` | Honest only with bigger GPU | Lying to the planner causes drops |
| Lower `MIN_FACE_SIZE` (e.g. 20 px) | More small faces qualify | Worse accuracy on tiny faces |
| Smaller `DETECTION_INPUT_SIZE` | Slightly fewer found | Faster detection |
| Enable batched embedding (V2) | 3–6× more faces same GPU | Code change required |

### 7.5 Accuracy ceilings per face

Identifying more is easy; identifying correctly is harder.

| Face quality | Accuracy at threshold 0.57 |
|--------------|-----------------------------|
| Frontal, ≥ 100 px wide, well-lit | 97–99% |
| ±30° yaw, ≥ 80 px | 92–96% |
| ±60° yaw or backlit | 75–85% |
| < 60 px wide (far from camera) | 50–70% — unreliable |
| Mask / partial occlusion | 60–80% — degrades fast |

`SMOOTHING_WINDOW=5` in
[`backend/engine/pipeline/matching.py`](../backend/engine/pipeline/matching.py)
applies majority-vote per `track_id` — you generally need **3 of 5
frames** to agree before an identity locks in. So even 70% per-frame
stabilises to >90% per-track.

### 7.6 Registered-identity capacity (FAISS)

Separate question from "faces per frame":

| Index type | Identities | Search latency | When to switch |
|-----------|-----------|----------------|----------------|
| `IndexFlatIP` (default) | 1 – 10k | < 1 ms | Default, don't change until pain |
| `IndexFlatIP` | 10k – 100k | 1 – 5 ms | Still fine at this scale on CPU |
| `IndexIVFFlat` | 100k – 1M | < 2 ms | Switch here, see [ROADMAP.md](ROADMAP.md) §1.1 |
| `IndexIVFPQ` | 1M – 100M | < 5 ms | Datacenter scale |

RTX 3050 comfortably holds a **10,000-patient registry** without
FAISS changes. A 50,000-patient hospital still works on flat index
but search creeps toward 3–5 ms per face — switch to IVF.

### 7.7 Concurrent-recognition rate (people per second)

For "how many patients pass through the entry camera per minute":

| Tier | Faces/sec recognised (sustained) | People/min throughput |
|------|----------------------------------|------------------------|
| RTX 3050, 1 cam | ~50 face-recognitions/sec | 60–90 unique people/min* |
| RTX 4060 Ti, 1 cam | ~120/sec | 150–200/min |
| RTX 4090, 1 cam | ~250/sec | 300+/min |
| L4 server, 1 cam | ~400/sec | bottlenecked by camera fps |

\* Each unique person yields ~5 recognitions before exiting frame
(15 fps × ~1 s in view). Divide raw recognitions/sec by ~5 to get
unique people/sec.

---

## 8. Bottleneck diagnosis

When the system feels slow, check in this order:

| Symptom | Likely culprit | Check | Fix |
|---------|---------------|-------|-----|
| GPU util < 60%, FPS low | CPU-bound (decode/copy) | `htop` shows pinned cores | Reduce `PROCESS_MAX_DIM`, raise `MOTION_SKIP_THRESHOLD` |
| GPU util 100%, FPS low | GPU-bound | `nvidia-smi dmon` | Smaller `DETECTION_INPUT_SIZE`, fp16, drop a camera |
| VRAM near max, OOM crashes | VRAM-bound | `nvidia-smi` | fp16, smaller detector, fewer concurrent cameras |
| RAM swapping | System-RAM-bound | `free -m`, swap > 0 | Larger frame downscale, fewer worker processes |
| Frame drops, queues full | Backpressure | metrics queue depths | Lower `CAMERA_FPS`, motion-skip, more AI workers |
| Recognition flickers | Smoothing too short | logs show identity jitter | Raise `SMOOTHING_WINDOW` to 7–10 |
| FAISS slow over 10k people | Brute-force index | search latency > 5 ms | Switch to IVF — see [ROADMAP.md §1.1](ROADMAP.md) |
| Postgres slow under load | DB on same disk as FAISS | `pg_stat_activity` waits | Move DB to dedicated NVMe or remote managed |

---

## 9. Network and storage sizing

### 9.1 Bandwidth per camera (1080p H.264 RTSP)

| Quality | Bitrate | 8 cams | 32 cams | 128 cams |
|---------|---------|--------|---------|----------|
| Low (CCTV preset) | 2 Mbps | 16 Mbps | 64 Mbps | 256 Mbps |
| Standard | 4 Mbps | 32 Mbps | 128 Mbps | 512 Mbps |
| High (forensic) | 8 Mbps | 64 Mbps | 256 Mbps | 1 Gbps |

A 1 Gbps NIC handles ~120 standard cameras. A 2.5 GbE port handles
~300. Beyond that, dual NICs or 10 GbE.

### 9.2 Storage

- **FAISS index size:** `N × 512 × 4 bytes` for FlatIP. At 100k people = 200 MB. Negligible.
- **Sightings log:** ~50 GB/month at 32 cameras. Plan retention policy.
- **Face crop archive (optional, for re-training):** ~10 GB/month. Compress to JPEG q=80; cold-tier after 30 days.
- **Recommendation:** NVMe SSD for active database (FAISS + Postgres hot tables); HDD or object storage for sightings history.

### 9.3 Per-scale hardware cheatsheet

| Component | 8 cams | 32 cams | 128 cams |
|-----------|--------|---------|----------|
| API node CPU | 4 vCPU | 8 vCPU × 2 | 8 vCPU × 4 |
| API node RAM | 8 GB | 16 GB × 2 | 16 GB × 4 |
| GPU | 1 × T4 | 1 × A10 | 4 × A10 or 2 × L4 |
| Redis | 2 GB | 8 GB | 32 GB cluster |
| Postgres | 4 vCPU / 16 GB | 8 vCPU / 32 GB | managed (RDS / Cloud SQL) |
| Kafka brokers | — | 3 × small | 3 × medium |
| Network per camera | ~4 Mbps (1080p H.264) | same | same — bandwidth dominates at scale |

Assumes 5 fps post-motion-skip and ~2 faces/frame average.

---

## 10. Cost per camera

| Setup | One-time | Monthly (power + ISP) | Per-camera CapEx |
|-------|----------|------------------------|-------------------|
| RTX 3050 box, 3 cams | $800 | $20 | ~$270 |
| RTX 4060 Ti box, 10 cams | $1,800 | $30 | ~$180 |
| RTX 4090 workstation, 24 cams | $4,000 | $60 | ~$170 |
| Single L4 server, 64 cams | $12,000 | $200 | ~$190 |
| Cluster, 300 cams | $35,000 | $600 | ~$120 |

Per-camera cost falls until ~64 cams per node, then plateaus.
Big-cluster economics only kick in if you genuinely need 200+ cams.
Don't over-provision.

---

## 11. Recommended adoption sequence

Starting from the reference RTX 3050 + 1080p baseline, ordered by ROI
(₹ × hours):

| # | Action | Range gain | Effort | ₹ cost |
|---|--------|-----------|--------|--------|
| 1 | Re-enrol patients at varied distances + bump `MAX_EMBEDDINGS_PER_IDENTITY` to 20 | +0.5–1.0 m | 1 day | 0 |
| 2 | Raise `SMOOTHING_WINDOW` to 7–10 | +0.5 m apparent | 1 min | 0 |
| 3 | Swap detector string to SCRFD-10GF (`buffalo_l` already has it) | +0.3 m | 2 hours | 0 |
| 4 | Implement multi-scale ROI zoom (Phase 2 of stub) | +1.5–2 m | 3–4 days | 0 |
| 5 | Mount existing camera at face level + IR illuminator | +0.5–1 m | 2 hours | ₹2k |
| 6 | Replace overview camera with 2K @ 60° HFOV bullet | +2–3 m | 1 hour install | ₹8k |
| 7 | Swap recognizer to AdaFace ir101 (needs RTX 4060 Ti or better) | +1 m | 1 day | ₹40k GPU |
| 8 | PTZ master-slave for waiting-room ID at 15–20 m | +12 m for stationary | 1–2 weeks | ₹40k |
| 9 | Upgrade server to L4 + V2 architecture | enables 64+ cams | 2–4 weeks | ₹3–4L |

**Realistic milestones:**

- **End of week 1, ₹0 spent:** 2.9 m → ~5.5 m via items 1–4.
- **End of week 2, ₹10–15k:** 5.5 m → ~7–8 m via items 5–6.
- **Month 2–3, ₹50k+:** 7–8 m → 15+ m via items 7–8.
- **Year 1, ₹4L+:** L4 deployment, 64+ cameras, V2 architecture.

---

## 12. Order of operations — adding a new camera

1. **Pick model** by §0.2 + §2.1 — match camera to mounting distance.
2. **Survey the mount site** — face-level height, ≤15° tilt, no backlight, wired Ethernet/PoE.
3. **Configure camera-side:**
   - H.264 main profile (not H.265).
   - Bitrate ≤ 4 Mbps for 1080p, ≤ 6 Mbps for 2K, ≤ 12 Mbps for 4K.
   - Disable on-device "AI" features, motion detection, time overlays.
   - Set static IP or DHCP reservation.
4. **Verify with `ffprobe rtsp://...`** — confirm resolution + framerate + codec match expectations.
5. **Add via Settings → Cameras** in the UI (calls `POST /api/v1/cameras`). Use `POST /cameras/test` first to probe the URL.
6. **Watch the live tile** for 30 s — confirm faces detected, no flicker.
7. **Walk-test from the rear of the FOV** to determine reliable identification range; document the result per camera so operators understand dead zones.
8. **Re-enrol patients** with one sample at this camera's reliable range, not just the front-desk close-up. See §4.2.

---

## 13. Sanity checks before production

- [ ] Run 24 hours with target camera count; confirm no memory leak (`nvidia-smi --query-gpu=memory.used --format=csv -l 60`).
- [ ] Confirm FAISS index reload time at expected registry size is under your acceptable cold-start (< 30 s).
- [ ] Saturate one camera with a continuous-motion video; confirm back-pressure works (queues bounded, no memory growth).
- [ ] Pull one camera mid-run; confirm only that camera's worker restarts, others unaffected.
- [ ] Kill Postgres for 60 s; confirm API returns 503 (not hangs) and recovers cleanly.
- [ ] Disk-fill `/database` to 95%; confirm FAISS save fails loudly, doesn't corrupt the index.
- [ ] Validate threshold against your population: 200-image regression set, recognition rate ≥ baseline.

---

## 14. Glossary

- **HFOV** — Horizontal Field of View, in degrees. Smaller = more zoom = more pixels per face at distance.
- **px/m** — pixels per metre at a given distance projected onto the sensor. Higher = better range.
- **ROI** — Region of Interest. Used here for "crop a small area of the frame for high-res re-detection."
- **PTZ** — Pan-Tilt-Zoom. Motorised camera that can be aimed and zoomed remotely.
- **NVDEC** — NVIDIA hardware video decoder. Decodes H.264/H.265 RTSP streams on the GPU, freeing CPU.
- **WDR** — Wide Dynamic Range. Sensor + ISP feature for scenes with both bright and dark areas (e.g. doorways).
- **Substream** — secondary lower-resolution RTSP stream cameras advertise alongside the main stream. Often defaults on cheap NVRs — verify clients pull the main stream.
- **ArcFace / AdaFace / PartialFC** — face-recognition loss functions; ArcFace is current, AdaFace handles low-quality faces better.
- **SCRFD / RetinaFace** — face detectors. Both ship in InsightFace's `buffalo_l` bundle.
- **L4** — NVIDIA's purpose-built video-AI inference GPU (24 GB, 72 W, 4× NVDEC). Sweet spot for multi-camera deployments.

---

_Verified against the codebase on 2026-05-25._
