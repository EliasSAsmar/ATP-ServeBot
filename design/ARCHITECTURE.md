# ARCHITECTURE

## 1. The two tiers

```
┌─────────────────────────────── EDGE (browser, real-time) ───────────────────────────────┐
│                                                                                          │
│  Camera ──▶ <video>                                                                      │
│               │                                                                          │
│               ├──▶ MediaPipe Pose Landmarker (WASM/GPU-in-browser) ──▶ 2D landmarks      │
│               │           │                                                              │
│               │           ├──▶ three.js/Canvas skeleton OVERLAY (live, ~30fps)           │
│               │           └──▶ Serve auto-detect heuristic (wrist vel + arm elevation)   │
│               │                     │ (serve detected → mark contact_ts)                 │
│               │                     ▼                                                    │
│               └──▶ MediaRecorder ── ring buffer ──▶ CLIP (webm/mp4, ~2-4s)               │
│                                                        │                                 │
│                                                        ▼                                 │
│                                          1. GET presigned PUT URL                        │
│                                          2. PUT clip ──────────────────┐                 │
│                                          3. POST /serves (job)         │                 │
│                                          4. POLL GET /serves/{id}      │                 │
│                                          6. GET presigned GLB URL ─────┼──▶ three.js      │
│                                                                        │    mesh render   │
└────────────────────────────────────────────────────────────────────┼─┼─────────────────┘
                                                                       │ │
                                              S3 (clips + meshes) ◀────┘ │  presigned URLs
                                                       ▲                 │
                                                       │                 ▼
┌──────────────────────────── HEAVY TIER (EC2 g5.xlarge, A10G 24GB) ────────────────────────┐
│  FastAPI (uvicorn)                                                                        │
│    /uploads     → mint presigned PUT                                                       │
│    /serves      → create Job, enqueue                                                      │
│    /serves/{id} → job status + result                                                      │
│                                                                                            │
│  Worker (single-process async queue in v1):                                                │
│    1. download clip from S3                                                                │
│    2. decode frames (ffmpeg/pyav)                                                          │
│    3. SAM 3  → per-frame player masks (segment + track)                                    │
│    4. refine contact keyframe within window around edge contact_ts                         │
│    5. SAM 3D Body → mesh (GLB) + ~70 3D keypoints on contact frame                         │
│    6. One-Euro filter keypoints                                                            │
│    7. compute metrics (v1: elbow angle)                                                    │
│    8. rule-based tip engine                                                                │
│    9. upload GLB to S3, write result to Job store                                          │
│                                                                                            │
│  PyTorch + CUDA · models resident in VRAM while instance is up                             │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

## 2. Why the GPU is never in the live loop (locked)

The live overlay must be **smooth, private, and always available**, and the heavy models are **too slow and too expensive** to run per-frame:

1. **Latency.** SAM 3 tracking + SAM 3D Body reconstruction take **seconds** per clip. A 30fps overlay has a **~33ms** budget per frame. These are three orders of magnitude apart. Putting the GPU in the live path would destroy the overlay.
2. **Cost & availability.** The A10G instance runs on-demand/spot and is **started and stopped around use**. The live overlay must work the instant the camera opens — before any instance is contacted, and even if the instance is down. MediaPipe on-device guarantees that.
3. **Privacy & bandwidth.** Streaming raw video to the cloud continuously is wasteful and invasive. We upload **one short clip per serve**, only after a serve is detected.
4. **Failure isolation.** If the cloud is unreachable, the app still films and overlays; analysis simply fails gracefully and can be retried. The two tiers fail independently.

**Consequence for implementers:** the live tier has **no hard dependency** on the heavy tier being reachable. Serve detection, overlay, capture, and clip buffering all work offline. Only the post-serve *analysis* requires the cloud.

## 3. Data flow — one serve, end to end

| # | Actor | Action | Doc ref |
|---|---|---|---|
| 1 | Edge | Camera frames → MediaPipe → 2D landmarks → overlay + serve-detect heuristic. | `UI.md`, `METRICS.md §serve-detect` |
| 2 | Edge | On serve detected, finalize a clip from the `MediaRecorder` ring buffer and compute `contact_timestamp_ms` relative to clip start. | `MODELS.md §keyframe` |
| 3 | Edge → Cloud | `POST /uploads` → receive presigned S3 PUT URL + `object_key`. | `API_CONTRACT.md` |
| 4 | Edge → S3 | `PUT` the clip bytes directly to S3 (no proxy through EC2). | `INFRA.md §upload` |
| 5 | Edge → Cloud | `POST /serves` with `object_key`, `handedness`, `contact_timestamp_ms`, clip metadata → receive `job_id`, `status="queued"`. | `API_CONTRACT.md` |
| 6 | Cloud worker | Download clip → decode → **SAM 3** masks → refine contact keyframe → **SAM 3D Body** → One-Euro filter → **elbow angle** → tip → upload GLB to S3. | `MODELS.md`, `METRICS.md` |
| 7 | Edge → Cloud | Poll `GET /serves/{job_id}` until `status="succeeded"` (or `failed`). | `API_CONTRACT.md §polling` |
| 8 | Edge → S3 | `GET` the GLB via the presigned URL in the result; render with three.js; show metric + tip. | `UI.md §post-serve` |

## 4. Async job model (locked)

Result delivery is **async job + client polling**, chosen for robustness:

- Inference is multi-second even on a warm instance; a synchronous request would hold an HTTP connection open for the entire pipeline.
- Polling degrades gracefully if the instance is briefly busy.
- **Operational note (per product owner):** in normal use the EC2 instance is already running when the app is used (the owner starts/stops it manually), so **cold start is not a design concern for v1**. The polling model still applies; it just converges quickly. `GET /health` lets the client confirm the instance is up before offering "Analyze."

Queue in v1 is a **single in-process async worker** (one job at a time is acceptable for a walking skeleton — one user, one instance). The API contract does not assume concurrency; `job_id` + status polling leaves room to swap in a real queue later without contract changes.

## 5. Component responsibilities (crisp boundaries)

- **React app** owns: camera, MediaPipe, overlay rendering, serve detection, clip capture, upload orchestration, polling, three.js mesh rendering, all UI copy/framing.
- **FastAPI** owns: presigned URL minting, job lifecycle, and the inference pipeline. It is **stateless per request** except for the in-memory/lightweight job store.
- **S3** owns: clip bytes (inbound) and GLB meshes (outbound). All large-blob transport is client↔S3 direct via presigned URLs; **EC2 never proxies video bytes for upload**.
- **Models** live in VRAM on the instance; they are loaded once at process start (see `MODELS.md §lifecycle`).

## 6. What is deliberately simple in v1 (and the seam left for later)

| Simplification | Seam preserved for phase-2 |
|---|---|
| One job at a time, in-process queue | `job_id`/polling contract supports a real queue (SQS/Redis) with no client change. |
| One keyframe (contact) | Result schema uses a `keyframes[]`-shaped payload so more frames (toss, trophy, follow-through) drop in later. |
| One metric (elbow angle) | `metrics` is a keyed object; adding metrics is additive. |
| Single static API key | Header-based auth can be swapped for JWT without changing endpoint shapes. |
| No racket/ball | Contact is pose-derived; a reserved `contact_confidence` field flags the estimate quality. |
