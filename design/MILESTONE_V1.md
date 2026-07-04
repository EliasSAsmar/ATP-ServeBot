# MILESTONE_V1 — the walking skeleton

The v1 goal is to **prove the pipeline end-to-end**, not to ship features. This doc turns the locked build order into an ordered spec with **acceptance criteria per step**. Each step is independently demonstrable and builds on the last. Do not start a step before its predecessor's acceptance criteria pass.

> **Definition of done for v1 (the whole milestone):**
> One real serve → captured on device → uploaded to S3 → SAM 3 masks the player → SAM 3D Body reconstructs the **contact** frame → **elbow angle** computed → one rule-based tip → 3D mesh + the number + the tip rendered in the browser. Everything else is additive.

Reference docs: interface = `API_CONTRACT.md`, models = `MODELS.md`, formulas = `METRICS.md`, screens = `UI.md`, infra = `INFRA.md`.

---

## Step 1 — Live overlay + capture (edge only, no cloud)

**Build:** React app; camera via `getUserMedia`; MediaPipe Pose Landmarker on-device; skeleton overlay ~30fps; serve auto-detect heuristic (wrist velocity + arm elevation); `MediaRecorder` ring buffer → clip; compute `contact_timestamp_ms`; handedness selector.

**Acceptance criteria:**
- [ ] Live camera preview with a skeleton overlay tracking the body at ~30fps (no stutter) on target hardware.
- [ ] Standing in frame and performing a serve motion triggers **auto-detect** and produces a saved clip (webm/mp4) of ~2–4s that **contains the contact moment**.
- [ ] Manual capture fallback works.
- [ ] The app emits a clip blob + metadata `{duration_ms, fps, width, height, content_type, contact_timestamp_ms, handedness}` (logged/inspectable).
- [ ] Works with the cloud **entirely absent** (no network calls required to reach this state).

**Demo:** record a serve, download/inspect the clip, confirm contact is inside it.

---

## Step 2 — Upload + stub backend (contract first, no models)

**Build:** FastAPI with `GET /v1/health`, `POST /v1/uploads`, `POST /v1/serves`, `GET /v1/serves/{job_id}` — **exactly** the shapes in `API_CONTRACT.md`. `X-API-Key` auth. Presigned S3 PUT/GET (`INFRA.md §3`). The worker is a **stub**: it returns a **canned but schema-valid** `succeeded` result (a placeholder GLB + a hardcoded `elbow_angle_deg` + a tip). Wire the client's full sequence (upload → create → poll → fetch GLB → render).

**Acceptance criteria:**
- [ ] Client completes the full sequence in `API_CONTRACT.md §6` against the stub and renders the placeholder GLB + fake metric + fake tip in the UI.
- [ ] `POST /v1/uploads` returns a working presigned URL; client `PUT` succeeds directly to S3; `POST /v1/serves` verifies the object (`HEAD`) and 409s if missing.
- [ ] Polling transitions `queued → running → succeeded` with valid payloads at each stage; `poll_after_ms`/backoff respected.
- [ ] Auth: missing/wrong `X-API-Key` → `401` with the standard error envelope.
- [ ] Error envelope + codes match the contract for at least `invalid_handedness`, `job_not_found`, `clip_not_found`.
- [ ] CORS lets the browser talk to both the API and S3.

**Demo:** end-to-end round trip with a fake result rendered — the *pipes* are proven before any model runs.

---

## Step 3 — SAM 3 + SAM 3D Body in isolation (no backend wiring)

**Build:** on the g5.xlarge, standalone scripts/notebooks: (a) load SAM 3, mask a player in a sample clip; (b) select the contact frame per `MODELS.md §3`; (c) run SAM 3D Body on that frame → mesh + 70 keypoints; (d) export GLB; (e) confirm the 70-keypoint index→name map and resolve the six arm joints. One-Euro filter present as a one-frame no-op.

**Acceptance criteria:**
- [ ] SAM 3 produces a plausible per-frame player mask on a sample serve clip; `mask_coverage_at_contact` computed.
- [ ] Keyframe selector returns a `refined_frame_index` that visually corresponds to contact (arm extended overhead).
- [ ] SAM 3D Body produces a mesh + 70 3D keypoints on the contact frame; `vertex_count`, axis/units confirmed and documented.
- [ ] Exported `.glb` opens in a standard viewer (and later in three.js) with correct orientation/scale.
- [ ] **`SAM3D_BODY_70_JOINTS` map confirmed against the real checkpoint**; `{l,r}_shoulder/elbow/wrist` resolve to correct indices (verified by eye on the mesh).
- [ ] Both models fit resident in 24GB VRAM together (or the load/unload strategy in `MODELS.md §5` works).

**Demo:** input a sample clip file, output a GLB + a JSON of 70 keypoints, eyeballed correct.

---

## Step 4 — Wire models into the backend (replace the stub)

**Build:** replace the Step-2 stub worker with the real Step-3 pipeline: download clip → decode → SAM 3 → refine keyframe → SAM 3D Body → One-Euro (no-op) → upload GLB → populate `result` (keypoints, mesh, `contact`, `diagnostics`). Metrics still empty/placeholder is fine here — the mesh + keypoints must be **real**.

**Acceptance criteria:**
- [ ] A clip captured by the Step-1 client, uploaded via Step-2, produces a **real** GLB and **real** 70 keypoints through `GET /v1/serves/{job_id}`.
- [ ] `result.contact` reflects real refinement; `keyframes[0].mesh.glb_url` is a working presigned GET; client renders the **real** mesh.
- [ ] `stage` progresses through real pipeline stages; `diagnostics.timings_ms` populated.
- [ ] Failure paths return contract errors: no person → `unprocessable_clip`/`failed`/non-retriable; GPU OOM → retriable.

**Demo:** serve for real, watch your own 3D reconstruction appear in the browser.

---

## Step 5 — One metric + one rule-based tip

**Build:** implement `elbow_angle_deg` exactly per `METRICS.md §1` (angle-at-joint on the serving side), including bands, confidence, and the nullability rules. Implement the elbow tip rules (`METRICS.md §9.2`) and the low-confidence guard. Populate `metrics.elbow_angle_deg` and `tips[]` in the result; keep all other metrics `null`.

**Acceptance criteria:**
- [ ] For a real serve, `elbow_angle_deg.value` is computed from the real keypoints and is plausible (0–180°, matches the visible arm bend).
- [ ] Golden unit test: fixed keypoints → exact expected `value`, `band`, and `tips[]` (includes the §1 worked example).
- [ ] Nullability correct: missing/low-score joint → object with `value:null` + `compute_error`; unimplemented metrics remain `null`.
- [ ] Exactly one elbow tip fires per band per the rules; low confidence → no corrective tip.
- [ ] `diagnostics.model_versions.metric_engine`/`tip_engine` set.

**Demo:** serve → get a real elbow-angle number and a matching coaching tip.

---

## Step 6 — 3D render in UI (the payoff view)

**Build:** the Analysis Result view (`UI.md §5`): three.js `GLTFLoader` + `OrbitControls` rendering the returned GLB; metric card for elbow angle (value + band gauge + confidence); tip card(s); the "AI estimate — single camera" framing; uncertain/unavailable states; mesh-URL-expiry refresh.

**Acceptance criteria:**
- [ ] Returned GLB renders and is rotatable/zoomable; auto-framed; correct orientation/scale.
- [ ] Elbow angle shown as `~{value}°` (≤1 decimal) with band + reference range; confidence surfaced.
- [ ] Tip rendered with severity styling; empty `tips[]` → neutral message.
- [ ] Uncertain state and `metric_unavailable` state render without ever showing a fabricated number.
- [ ] Expired `glb_url` → transparent re-poll/refresh, no broken viewer.
- [ ] The full DoD path (one serve → 3D + number + tip) works on target hardware/browser.

**Demo:** the complete v1 story, one continuous flow, in the real app.

---

## Cross-cutting acceptance (applies to the whole milestone)

- [ ] Live tier functions with the cloud offline (edge independence, `ARCHITECTURE.md §2`).
- [ ] Every payload conforms to `API_CONTRACT.md` (validate against it — treat it as the schema).
- [ ] "Inferred, not measured" framing present on all 3D/metric surfaces (`OVERVIEW.md §5`).
- [ ] No LLM in the runtime path; no racket/ball tracking; no user accounts (non-goals honored).
- [ ] Instance start/stop + idle auto-stop behave per `INFRA.md §4`; no GPU idle burn.

## Explicitly deferred to phase-2 (not part of this milestone)

Multi-keyframe reconstruction (trophy/loading/follow-through) · temporal keypoints + real One-Euro smoothing · all stubbed metrics (shoulder/knee angles, kinetic chain sequencing, toss placement/consistency, contact height, phase timing) · racket/ball tracking · serve history/progress · user accounts · autoscaling/queueing · spot-interruption durability.

## Suggested demo script (for sign-off)

1. Open app, grant camera, pick handedness, confirm "Cloud ready".
2. Serve. Watch the live skeleton; auto-detect captures the serve.
3. "Analyzing…" progresses through friendly stages.
4. Result view: rotate your 3D self at contact; read `~{elbow}°` + band; read the coaching tip.
5. Serve again from the result view. Repeat.

If a stranger can do that unaided, v1 is done.
