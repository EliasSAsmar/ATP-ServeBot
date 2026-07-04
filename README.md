# 🎾 ATP-ServeBot

**Live-camera tennis serve analysis.** Point a camera at yourself, serve, and get a real-time skeleton overlay plus an AI-reconstructed **3D model of your body at the moment of contact** — with biomechanical metrics and rule-based coaching tips.

> **Status:** 🟡 v1 in progress — building the end-to-end *walking skeleton* (one serve → 3D reconstruction → one metric → one tip). See [`design/MILESTONE_V1.md`](design/MILESTONE_V1.md).

---

## What it does

| Tier | Runs | Job |
|---|---|---|
| **Live (edge)** | In the browser, ~30fps | 2D skeleton overlay + automatic serve detection + clip capture. No GPU, no network required. |
| **Heavy (cloud)** | On-demand GPU (AWS g5.xlarge / A10G) | Segments the player, reconstructs an inferred 3D mesh at contact, computes metrics, returns templated coaching. |

The design decision at the core: **the GPU is never in the real-time loop.** The live experience is instant and works offline; heavy inference happens per-serve, asynchronously, on a GPU that's only running when needed.

> ⚠️ **The 3D reconstruction is inferred from a single camera — an AI estimate, not a clinical measurement.** This is a coaching/visualization aid, framed that way throughout the UX.

## Architecture

```
EDGE (browser, real-time)                     HEAVY TIER (EC2 g5.xlarge, A10G 24GB)
  camera ─▶ MediaPipe Pose ─▶ overlay           FastAPI  ── presigned upload / job API
                  │                                │
                  ├─ serve auto-detect             worker:
                  └─ MediaRecorder ─▶ clip          SAM 3 (segment+track player)
                        │                           → refine contact keyframe
        presigned PUT ──┼──▶  S3  ◀── GLB mesh      → SAM 3D Body (mesh + 70 keypoints)
        poll job  ──────┘                           → elbow angle → rule-based tip
        three.js ◀── presigned GLB                  → upload GLB
```

Full write-up: [`design/ARCHITECTURE.md`](design/ARCHITECTURE.md).

## Tech stack

- **Edge:** React · MediaPipe Tasks (Pose Landmarker) · MediaRecorder · three.js
- **Cloud:** FastAPI · PyTorch + CUDA · Meta **SAM 3** (segment/track) · Meta **SAM 3D Body** (`sam-3d-body-dinov3`, single-image mesh + 70 keypoints) · One-Euro filter
- **Infra:** AWS EC2 g5.xlarge (on-demand/spot, auto-stop) · S3 (presigned URLs)

## Repo layout

```
ATP-ServeBot/
├── design/     # Design specs — the source of truth (start here)
├── app/        # React live tier
├── server/     # FastAPI heavy tier (models, metrics, tips)
├── infra/      # EC2 setup, S3/CORS/IAM, start-stop scripts
└── README.md
```

## Design docs

The interface and formulas are specified precisely enough to implement without guessing.

| Doc | Contents |
|---|---|
| [OVERVIEW](design/OVERVIEW.md) | Goal, non-goals, glossary, product stance |
| [ARCHITECTURE](design/ARCHITECTURE.md) | Edge/cloud split, data flow, why GPU is out of the live loop |
| [API_CONTRACT](design/API_CONTRACT.md) | Every endpoint with exact request/response JSON |
| [MODELS](design/MODELS.md) | SAM 3 + SAM 3D Body integration, keyframe selection |
| [METRICS](design/METRICS.md) | Metric formulas + rule-based tip thresholds |
| [UI](design/UI.md) | Screens, states, live + post-serve views |
| [INFRA](design/INFRA.md) | EC2, S3 presigned flow, on-demand/auto-stop |
| [MILESTONE_V1](design/MILESTONE_V1.md) | The walking skeleton as an ordered build spec |

## v1 scope

**In:** one serve → captured → uploaded → player masked → 3D mesh at contact → **elbow angle** → one coaching tip → rendered 3D + number in the browser.

**Out (v1):** racket/ball tracking · multi-camera / true 3D · LLM-generated coaching · user accounts · the full metric suite (specified but stubbed) · autoscaling.

---

*Built as a portfolio project exploring edge/cloud ML architecture, single-image 3D human reconstruction, and real-time on-device pose estimation.*
