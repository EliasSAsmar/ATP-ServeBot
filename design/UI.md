# UI

Screens, states, and the two hero views (live overlay, post-serve analysis) for the React web app. This is the **edge tier** (`ARCHITECTURE.md`). Keep the "inferred, not measured" framing everywhere 3D/metrics appear (`OVERVIEW.md §5`).

## 0. Stack & rendering

- **React** (web). Camera via `getUserMedia` into a `<video>`.
- **MediaPipe Tasks — Pose Landmarker** runs on-device (~30fps) → 2D landmarks.
- **Overlay:** skeleton drawn on a `<canvas>` (or three.js orthographic layer) sized to the video, redrawn per animation frame.
- **Clip capture:** `MediaRecorder` on the camera stream, kept as a short **ring buffer** so the moments *before* detection are included.
- **3D render:** **three.js** loads the returned `.glb` (`GLTFLoader`) with `OrbitControls`.
- All heavy work is remote and async (`API_CONTRACT.md`). The UI must remain responsive during analysis.

## 1. Screen map

```
[Permission/Setup] → [Live] ⇄ [Analyzing] → [Analysis Result] → back to [Live]
                         ↑                          │
                         └──────────────────────────┘
[Settings] (handedness, API key, instance status) reachable from Live
```

1. **Setup / Permissions** — request camera; pick handedness; confirm cloud availability.
2. **Live** — camera + skeleton overlay + serve auto-detect (the default screen).
3. **Analyzing** — progress while the job runs.
4. **Analysis Result** — 3D mesh + metric + tip.
5. **Settings** — handedness, API key / endpoint, instance status.

## 2. Setup / Permissions

**Purpose:** get camera permission and the two required inputs before serving.

- **Camera permission** prompt; on deny → blocking state with retry + instructions.
- **Handedness selector** (`right` / `left`) — **required**, sent on every `POST /v1/serves` as `handedness`. Default `right`, persisted locally.
- **Cloud status chip** — calls `GET /v1/health`:
  - reachable + `models_ready` → green "Cloud ready".
  - reachable + `models_ready:false` → amber "Warming up…".
  - unreachable → grey "Cloud offline — start the instance" (analysis disabled; live tier still works). See `INFRA.md` for start/stop.
- Continue → **Live**.

## 3. Live view (hero #1)

**Purpose:** camera preview with real-time skeleton and automatic serve capture.

**Layout:**
- Full-bleed camera preview.
- Skeleton overlay (joints + bones) drawn from MediaPipe landmarks, mirrored to match the preview.
- Top bar: cloud status chip, handedness indicator, settings gear.
- Bottom: **capture mode** toggle — **Auto** (serve-detect) vs **Manual** (tap to grab the last N seconds).
- A subtle **recording buffer** indicator (the ring buffer is always rolling in Auto).

**Serve auto-detect (heuristic, on-device — see `METRICS.md`/`MODELS.md` for the cloud side):**
- Runs on the landmark stream: watches **hitting-wrist vertical velocity** and **arm elevation**.
- On a detected serve: flash a "Serve captured" affordance, freeze the ring buffer into a clip, compute `contact_timestamp_ms` (peak of the reach), and transition to **Analyzing**.
- **Manual fallback:** a capture button always available in case detection misses.

**States:**
| State | UI |
|---|---|
| `initializing` | "Starting camera / loading pose model…" spinner. |
| `no_pose` | Overlay hidden; hint "Step back so your whole body is in frame." |
| `tracking` | Skeleton drawn; detector armed (Auto). |
| `serve_detected` | Brief highlight + "Captured!" → Analyzing. |
| `camera_error` / `permission_denied` | Blocking error + retry. |

**Framing copy:** none of the live overlay implies measurement — it's a tracking aid.

## 4. Analyzing view

**Purpose:** show progress while the async job runs; never block the whole app.

**Flow (mirror of `API_CONTRACT.md §6`):**
1. `POST /v1/uploads` → `PUT` clip to S3 → show "Uploading… {pct}" (use `PUT` progress).
2. `POST /v1/serves` → get `job_id`.
3. Poll `GET /v1/serves/{job_id}` with backoff (`poll_after_ms` then 1.5→2→3s cap).
4. Map `stage` → friendly step label; drive a progress bar from `progress`.

**Stage → label map:**
| `stage` | Label |
|---|---|
| `downloading` / `decoding` | "Preparing your serve…" |
| `segmenting` | "Finding you in the video…" |
| `selecting_keyframe` | "Locating the contact moment…" |
| `reconstructing` | "Building your 3D model…" |
| `filtering` / `computing_metrics` / `generating_tips` | "Analyzing your form…" |
| `uploading_mesh` | "Almost ready…" |

**States:**
| State | UI |
|---|---|
| `uploading` | Progress from `PUT`. Cancelable → returns to Live. |
| `queued` | "Waiting for an open slot…" (only if `429`/queued). |
| `running` | Stage label + progress bar. |
| `succeeded` | Auto-advance to **Analysis Result**. |
| `failed` | Error card (`error.message`); if `retriable` show **Retry** (re-`POST /v1/serves` with same `object_key`); else **Try another serve** → Live. |

Long-running: after **[CONFIRM] ~30s** with no progress change, show a soft "still working" note; do not auto-fail (client-side timeout only on network errors).

## 5. Analysis Result view (hero #2)

**Purpose:** show the 3D mesh at contact + the metric + the tip.

**Layout (top → bottom):**
1. **3D viewer** (dominant): three.js canvas rendering `keyframes[0].mesh.glb_url` via `GLTFLoader`, `OrbitControls` (rotate/zoom, no pan needed). Auto-frame the mesh; default camera 3/4 front. Respect `up_axis`/`units` from the payload — apply no implicit axis flip beyond reconciling with three.js's +Y-up convention.
   - **Label overlay:** persistent chip "AI 3D estimate — single camera" (product stance).
   - Loading/error states for the GLB fetch (URL may be expired → re-poll `GET /v1/serves/{job_id}` to refresh `glb_url`).
2. **Metric card(s):** for `elbow_angle_deg` (implemented) show:
   - Big value `~118°` (tilde communicates estimate; 1-decimal max).
   - Band label ("Slightly bent") + the reference range (150–180°) as a small gauge.
   - `confidence` reflected subtly (e.g. "estimate confidence: medium"); if low, dim the value and show the uncertain state (§6).
   - **Stubbed metrics** (`null` in payload) render as **"Coming soon"** placeholders or are hidden — **[CONFIRM] product choice** (recommend: hidden in v1 to keep it clean).
3. **Tip card(s):** render `tips[]` — `title` + `message`, styled by `severity`. Empty `tips[]` → a neutral "Looking solid on this one." message.
4. **Actions:** "New serve" → Live. (Optional "Save"/history is a non-goal in v1.)

**States:**
| State | UI |
|---|---|
| `loading_mesh` | Viewer skeleton/spinner while GLB downloads. |
| `ready` | Full result. |
| `mesh_expired` | "Refreshing 3D model…" → re-poll for fresh `glb_url`. |
| `metric_uncertain` | See §6. |
| `metric_unavailable` | Metric object has `value:null` + `compute_error` → "Couldn't measure this angle on this serve." No fabricated number. |

## 6. Uncertain / low-confidence state (product stance)

When `elbow_angle_deg.confidence < MIN_TIP_CONFIDENCE` (`METRICS.md §9`) or `value:null`:
- Do **not** show a confident number or a corrective tip.
- Show: "The AI wasn't confident about your form on this serve — try again with your whole body clearly in frame and good lighting."
- Still render the 3D mesh (labeled as an estimate) so the user gets *something*.

## 7. Settings

- **Handedness** (right/left) — persisted; the value sent on every job.
- **API endpoint + API key** (`X-API-Key`) — for the walking skeleton, entered manually and stored locally. (No user accounts — `OVERVIEW.md §3`.)
- **Instance status** — live `GET /v1/health` readout (up / warming / offline) with a reminder that analysis needs the instance running.

## 8. Error & offline matrix (edge resilience)

| Condition | Live tier | Analysis |
|---|---|---|
| Cloud unreachable | **Works** (overlay + capture + buffer). | Disabled; clear "cloud offline" messaging. |
| `models_not_ready` (503) | Works. | "Warming up" — allow retry. |
| Upload fails | Works. | Retry upload; keep the clip in memory until success or user discards. |
| Job `failed` retriable | Works. | Offer Retry. |
| Job `failed` non-retriable | Works. | "Couldn't analyze this serve" + guidance (framing, lighting). |

## 9. Accessibility & framing rules (non-negotiable)

- Every 3D/metric surface carries the "AI estimate, single camera" framing.
- No clinical/medical/injury language.
- Numbers shown with `~` and ≤1 decimal; never imply precision the model doesn't have.
- Color is not the only signal for band/severity (text labels too).
