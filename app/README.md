# app/ — Live tier (React)

The edge tier that runs entirely in the browser. **No GPU, no hard cloud dependency.**

Responsibilities:
- Camera capture (`getUserMedia`) + MediaPipe Pose Landmarker (~30fps) → skeleton overlay
- Serve auto-detect heuristic (wrist velocity + arm elevation)
- Clip capture via `MediaRecorder` ring buffer + `contact_timestamp_ms`
- Upload orchestration → job polling → three.js GLB render

**Build against:** [`../design/UI.md`](../design/UI.md) and [`../design/API_CONTRACT.md`](../design/API_CONTRACT.md).
Build order starts at [`../design/MILESTONE_V1.md`](../design/MILESTONE_V1.md) Step 1.

_Not yet scaffolded — implementation begins at Milestone v1 Step 1._
