# OVERVIEW

> **Phase:** Design. This document set is a specification handed to an implementer. No production code lives here.

## 1. What we are building

A **live-camera tennis serve analysis app**. A player points a phone/laptop camera at themselves, serves, and the app:

1. **Live tier (on-device, real-time):** draws a 2D skeleton overlay on the live video at ~30fps and auto-detects when a serve happens, capturing a short clip.
2. **Heavy tier (cloud, after the serve):** reconstructs an **inferred 3D body mesh** at the moment of ball contact, computes biomechanical metrics, and returns templated, rule-based coaching tips.

The user experience is: *serve → watch the live skeleton → seconds later, see a rotatable 3D model of yourself at contact plus one or more numbers and a coaching tip.*

## 2. V1 goal — the walking skeleton

V1 is **not** a feature-complete product. It exists to **prove the pipeline end-to-end** with the thinnest possible vertical slice:

> One serve → captured on device → uploaded to S3 → SAM 3 masks the player → SAM 3D Body runs on **one** keyframe (ball contact) → compute **one** metric (**elbow angle**) → return → render the 3D mesh + that one number + one rule-based tip in the UI.

Everything beyond this slice is **additive** and explicitly deferred. See `MILESTONE_V1.md` for the ordered build spec and acceptance criteria.

## 3. Explicit non-goals (v1)

These are out of scope. Do not build them; do not let them leak into the API contract or data model except as reserved, nullable fields.

| Non-goal | Why / when |
|---|---|
| **Clinically accurate measurement** | 3D is single-camera and **inferred**, not measured. It is a *visualization and directional-feedback* feature, never a diagnostic instrument. This framing is a hard product constraint — see §5. |
| **Racket & ball tracking** | Deferred to phase-2 ("thickening"). No racket/ball detection, no toss-vs-racket geometry that requires the ball. |
| **Multi-camera / true 3D triangulation** | Single camera only in v1. |
| **LLM-generated coaching at runtime** | Coaching is a **rule-based threshold engine** over computed metrics with templated tips. No LLM in the runtime path in v1. |
| **User accounts / multi-user auth** | v1 uses a single static API key. No sign-up, no per-user data isolation. |
| **Real-time GPU inference** | GPU is **never** in the live loop. All heavy inference is post-serve, async. |
| **Persistent history / progress tracking / social** | No serve library, trends, or sharing in v1. |
| **Full metric suite** | Only **elbow angle** is implemented in v1. All other metrics are **specified but stubbed** (see `METRICS.md`). |
| **Autoscaling / fleet** | Single EC2 g5.xlarge, manually or user-triggered start/stop. |

## 4. Glossary

| Term | Meaning |
|---|---|
| **Edge / Live tier** | The React web app running in the player's browser. Runs MediaPipe on-device. Never touches the GPU. |
| **Heavy tier / Cloud** | FastAPI service on a single AWS EC2 **g5.xlarge** (NVIDIA A10G, 24GB VRAM) running PyTorch+CUDA. |
| **MediaPipe Pose Landmarker** | Google MediaPipe Tasks model that produces **2D (+ pseudo-depth) pose landmarks** on-device, used for the live overlay and the serve-detect heuristic. **Not** the source of the 3D mesh. |
| **SAM 3** | Meta's `facebookresearch/sam3` — Segment Anything Model 3. Used to **segment and track** the player through the clip, producing per-frame masks. |
| **SAM 3D Body** | Meta's `facebook/sam-3d-body-dinov3` — single-image human **3D mesh + ~70 3D keypoints** estimator. Runs on the **contact keyframe** only in v1. |
| **Keyframe** | A single selected frame of the clip. In v1 the only keyframe is **contact**. |
| **Contact** | The moment the racket meets the ball — the apex/extension instant of the serve. Approximated on-device, refined in the cloud (racket/ball are not tracked, so this is a pose-derived estimate). |
| **Serve auto-detect** | On-device heuristic (wrist vertical velocity + arm elevation) that decides a serve occurred and marks its approximate contact timestamp. |
| **One-Euro filter** | A speed-adaptive low-pass filter applied to keypoints to reduce jitter without lag. |
| **GLB** | Binary glTF 3D asset format. The returned mesh is a `.glb` rendered in the browser with three.js. |
| **Clip** | The short captured video (from `MediaRecorder`) of a single serve, uploaded to S3. |
| **Job** | A server-side unit of work: analyze one uploaded clip. Identified by `job_id`. Async; client polls for status. |
| **Metric** | A single computed number derived from 3D keypoints (e.g. elbow angle in degrees). |
| **Tip** | A short templated coaching string emitted when a metric crosses a rule-based threshold. |
| **Handedness** | Which arm serves (`"right"` or `"left"`). **Client-supplied.** Determines which side's joints the metrics use. |

## 5. The "inferred, not measured" product stance

This is a **UX feature, not a disclaimer buried in a footer.** Every surface that shows 3D data must communicate that the reconstruction is an AI estimate from a single camera:

- The 3D view is labeled (e.g. "AI 3D estimate").
- Metrics are shown with directional/qualitative framing, not clinical precision (e.g. "~118°", coaching band, not "118.4° ± 0.1").
- Copy never implies medical, injury, or clinical authority.

Implementers must preserve this framing in UI copy (`UI.md`) and in any user-facing metric formatting.

## 6. Document map

| Doc | Purpose |
|---|---|
| `OVERVIEW.md` | This file — goal, non-goals, glossary, product stance. |
| `ARCHITECTURE.md` | Edge/cloud split, data flow, why GPU is out of the live loop. |
| `API_CONTRACT.md` | **The authoritative interface.** Every endpoint, exact request/response JSON. |
| `MODELS.md` | SAM 3 + SAM 3D Body integration: prompts, checkpoints, tensor shapes, keyframe selection. |
| `METRICS.md` | Metric formulas (elbow angle implemented; rest stubbed) + rule-based tip thresholds. |
| `UI.md` | Screens, states, live-overlay and post-serve views. |
| `INFRA.md` | EC2 setup, S3 presigned upload, on-demand/auto-stop, spot notes. |
| `MILESTONE_V1.md` | The walking skeleton as an ordered build spec with per-step acceptance criteria. |
