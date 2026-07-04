# server/ — Heavy tier (FastAPI)

The cloud inference service on the g5.xlarge. **GPU is never in the live loop** — this handles per-serve, async analysis only.

Responsibilities:
- Presigned S3 URL minting + async job lifecycle (`X-API-Key` auth)
- Pipeline: download clip → decode → **SAM 3** mask → refine contact keyframe → **SAM 3D Body** mesh + 70 keypoints → One-Euro filter → metrics → rule-based tips → upload GLB

**Build against:** [`../design/API_CONTRACT.md`](../design/API_CONTRACT.md) (interface), [`../design/MODELS.md`](../design/MODELS.md) (models), [`../design/METRICS.md`](../design/METRICS.md) (formulas + tips).
Build order: [`../design/MILESTONE_V1.md`](../design/MILESTONE_V1.md) Steps 2–5.

**Hard gate before shipping:** confirm the SAM 3D Body 70-joint index→name map against the real checkpoint (the six arm joints especially) — see MODELS.md §4.4.

_Not yet scaffolded — implementation begins at Milestone v1 Step 2._
