# MODELS

How SAM 3 and SAM 3D Body are used in the cloud pipeline. This doc is precise about **prompts, checkpoints, tensor shapes, and keyframe selection** so the implementer never guesses. Where an exact value depends on the specific checkpoint/repo revision, it is flagged **[CONFIRM]** — verify against the actual model card/config at implementation time; do not hardcode a guessed value.

## 0. Pipeline position

```
clip → decode frames → [SAM 3: segment+track player] → refine contact keyframe
     → [SAM 3D Body: mesh + 70 kpts on contact frame] → One-Euro filter → metrics
```

Two models, two very different jobs:

- **SAM 3** runs over **many frames** to isolate/track the player (a mask per frame). It does *not* produce 3D.
- **SAM 3D Body** runs on **one frame** (contact) to produce the 3D mesh + keypoints.

## 1. Frame decoding (pre-model)

- Decode the clip with **PyAV** (preferred, frame-accurate) or ffmpeg. Produce an ordered list of RGB frames with their presentation timestamps (PTS) in ms from clip start.
- Normalize to the clip's nominal `fps`; keep a `frame_index → timestamp_ms` map (needed for `refined_frame_index`/`refined_timestamp_ms` in the API result).
- Downscale long side to **[CONFIRM] ≤ 1024 px** before SAM 3 if needed for memory/throughput; keep the **contact frame at full/native resolution** for SAM 3D Body (mesh quality is resolution-sensitive).
- Tensor going into models: `uint8` HWC RGB per frame → model preprocessor handles normalization.

## 2. SAM 3 — segment + track the player

**Repo:** `facebookresearch/sam3`. **Role in v1:** produce a **per-frame binary mask** of the serving player so downstream steps (keyframe refinement, and later cropping for SAM 3D Body) operate on the player, not the background.

### 2.1 Checkpoint

- Use the **largest checkpoint the A10G (24GB) can hold alongside SAM 3D Body** [CONFIRM] — target `sam3_hiera_large` class. Record the exact checkpoint name/hash in `diagnostics.model_versions.sam3`.
- Loaded **once** at process start into VRAM (see §5 lifecycle). Do not reload per request.

### 2.2 Prompting strategy (v1)

SAM 3 supports prompted segmentation + video tracking. For a single-player serve clip, v1 uses an **automatic single-subject prompt**, in this priority order:

1. **Preferred — reuse the edge pose.** The client already ran MediaPipe and knows where the player is. **[DESIGN OPTION]** Pass an optional bounding box or a small set of positive point prompts derived from the on-device landmarks at the contact timestamp. (Not required by the API contract in v1; if not passed, fall back to (2).)
2. **Fallback — largest-person auto-prompt.** Seed SAM 3 with a positive point at the **center of the largest person-like region** in the contact frame (or a coarse person detector's top box). Track that instance forward and backward across the clip.

**Prompt object (conceptual):**
```
prompt = {
  "frame_index": <contact frame>,        # seed on the frame we care about most
  "points": [[x_px, y_px]],              # positive point(s), pixel coords
  "labels": [1],                          # 1 = foreground
  "box": [x0, y0, x1, y1] | null          # optional, from edge bbox
}
```

### 2.3 Tracking

- Seed on the (refined) contact frame, then **propagate** the mask across the clip in both temporal directions to get a mask for every frame. (v1 only strictly needs the contact-frame mask; masking the full clip is used for keyframe refinement and is cheap relative to reconstruction — but if throughput is tight, mask only a **±window** around contact.)
- Output per frame: a binary mask `H×W` (`bool`/`uint8`).

### 2.4 Shapes

| Item | Shape / dtype | Notes |
|---|---|---|
| Input frame | `H×W×3` `uint8` RGB | native or downscaled |
| Prompt points | `N×2` float (px) | seed frame only |
| Output mask (per frame) | `H×W` `uint8` {0,1} | one tracked instance |
| Output logits (optional) | `H×W` float | for `mask_coverage` diagnostic |

`diagnostics.mask_coverage_at_contact` = (mask pixel count at contact) / (H·W).

### 2.5 Failure handling

- **No person tracked** → job `failed`, `error.code="unprocessable_clip"`, `stage="segmenting"`, `retriable=false`.
- **Multiple people** → v1 picks the largest/most-central instance; log a diagnostic but do not fail.

## 3. Keyframe selection — the CONTACT frame

The single most important step for v1 correctness, since SAM 3D Body runs on exactly one frame.

### 3.1 Inputs
- `contact_timestamp_ms` from the client (edge heuristic estimate).
- `refine_window_ms` (default **±100ms**, i.e. `refine_window_ms: 200` total — [CONFIRM] tune).
- Per-frame player masks (from SAM 3) and, if available, 2D keypoints.

### 3.2 Algorithm (v1)

1. Map `contact_timestamp_ms` → nearest decoded `frame_index` (`c0`).
2. Consider the frame window `[c0 - W, c0 + W]` where `W = round(refine_window_ms/2 / (1000/fps))` frames.
3. **Refinement objective — pick the frame that best matches "arm fully extended overhead at contact."** Since racket/ball are not tracked, use a pose-derived proxy computed per candidate frame from 2D landmarks (MediaPipe result can be recomputed cloud-side on the masked crop, or the edge landmarks reused):
   - **wrist height:** maximize wrist `y` (highest point). Weight `w1`.
   - **arm elevation / extension:** maximize elbow-angle proxy (straighter arm) and shoulder-to-wrist vertical alignment. Weight `w2`.
   - **motion apex:** the frame where vertical wrist velocity crosses zero from + to − (top of the reach). Weight `w3`.
   - Score `S(f) = w1·norm(wrist_y) + w2·norm(extension) + w3·apex_indicator`. **[CONFIRM] default weights `w1=0.5, w2=0.3, w3=0.2`.**
4. Choose `argmax S(f)` over the window → `refined_frame_index`, `refined_timestamp_ms`.
5. `contact_confidence` (result field) = normalized peak sharpness of `S` around the max (a flat score surface → low confidence). **[CONFIRM] define concretely, e.g. `1 - (2nd_best/best)` clamped to [0,1].**

If no 2D keypoints are available in the window, **fall back** to trusting `contact_timestamp_ms` verbatim (set `refined == edge`, `contact_confidence` = the edge `contact_confidence` if provided else `0.5`).

### 3.3 Output
- `refined_frame_index`, `refined_timestamp_ms`, `contact_confidence` → surfaced in `result.contact` (see `API_CONTRACT.md §4c`).

## 4. SAM 3D Body — 3D mesh + 70 keypoints (contact frame only)

**Repo/checkpoint:** `facebook/sam-3d-body-dinov3`. **Role:** from a **single image** (the contact frame, ideally cropped to the player mask), estimate a 3D human mesh and ~70 3D keypoints.

### 4.1 Input prep
- Take the **contact frame** at native resolution.
- Crop to the player's bounding box (from SAM 3 mask), with padding **[CONFIRM] ~15%**, then resize/letterbox to the model's expected input size **[CONFIRM] (e.g. 256×256 or 512×512 — read from the model config)**.
- Keep the crop transform (scale, offset) so keypoints can be reported in a consistent space. v1 reports keypoints in **model/camera 3D space (meters)** as emitted; it does not re-project to pixel space (not needed for elbow angle).
- Optionally pass the mask to constrain the subject **[CONFIRM if the checkpoint accepts a mask input]**.

### 4.2 Output
| Item | Shape / dtype | Notes |
|---|---|---|
| Mesh vertices | `V×3` float (meters) | `V` ≈ **[CONFIRM]** (SMPL-family ~6890, or model-specific ~10475 — read from model). Report `vertex_count`. |
| Mesh faces | `F×3` int | triangle indices, for GLB export |
| 3D keypoints | `70×3` float (meters) | the analysis substrate |
| Keypoint scores | `70` float [0,1] | per-joint confidence |
| Root translation | `3` float | model origin offset; report as `root_translation` |
| Camera / pose params | model-specific | not surfaced in v1 API |

- **Axis convention [CONFIRM]:** confirm up-axis and handedness from the model. The API contract assumes **+Y up, right-handed, meters**; if the checkpoint differs, convert before emitting and keep `up_axis`/`units` fields truthful.

### 4.3 GLB export
- Convert `(vertices, faces)` → a `.glb` (binary glTF). Use `trimesh` or `pygltflib` **[CONFIRM tooling]**.
- Bake a neutral material (single color, double-sided) — no textures in v1.
- Ensure `up_axis`/units in the GLB match what the API reports; three.js in the client applies no implicit conversion beyond what `UI.md` specifies.
- Upload GLB to S3; return presigned GET as `glb_url` (`API_CONTRACT.md §4c`).

### 4.4 The 70-keypoint skeleton — canonical index/name map [CONFIRM]

The result payload reports each keypoint with an `index` **and** a human `name`. The **authoritative** index→name mapping is defined by the `sam-3d-body-dinov3` checkpoint and **must be read from the model's own skeleton definition at implementation time**, not invented. Persist it as a single constant (`SAM3D_BODY_70_JOINTS`) and reference it everywhere (metrics depend on it).

Minimum joints v1 metrics require (must resolve to real indices):

| Canonical name | Used by |
|---|---|
| `right_shoulder`, `left_shoulder` | elbow angle, shoulder angle (stub) |
| `right_elbow`, `left_elbow` | elbow angle |
| `right_wrist`, `left_wrist` | elbow angle |
| `right_hip`, `left_hip` | knee/torso metrics (stub) |
| `right_knee`, `left_knee` | knee flexion (stub) |
| `right_ankle`, `left_ankle` | knee flexion (stub) |

> **Do not ship** until the six arm joints (`{l,r}_shoulder/elbow/wrist`) are confirmed against the model's real indices. The elbow-angle metric is meaningless if these are mismapped.

## 5. Model lifecycle & VRAM

- **Load once** at FastAPI startup (or lazily on first job, then keep resident). Both models resident simultaneously must fit in **24GB**; if not, **[CONFIRM]** either use smaller SAM 3 checkpoint or load SAM 3D Body on demand and unload SAM 3 during reconstruction.
- Expose readiness via `GET /v1/health` (`models_ready`, per-model `loaded`).
- On CUDA OOM during a job: fail the job with `retriable=true`, `error.code="internal_error"`, `stage` = current stage; do not crash the process.
- Use `torch.inference_mode()`, fp16/bf16 where the checkpoints support it **[CONFIRM]**, and a single CUDA stream (one job at a time in v1).

## 6. One-Euro filter

- Applied to **keypoints** to reduce jitter.
- **v1 caveat:** with a single keyframe there is no temporal sequence to filter across, so One-Euro has **nothing to smooth in the strict walking skeleton**. Keep the filter in the pipeline as a **pass-through/no-op for one frame**, wired and tested, so that when multi-keyframe/temporal keypoints are added (phase-2) it is already in place.
- Parameters to expose (defaults **[CONFIRM]**): `min_cutoff` (e.g. 1.0), `beta` (e.g. 0.007), `d_cutoff` (e.g. 1.0). Document that these tune jitter-vs-lag once temporal data exists.

## 7. Determinism & versioning

- Record exact checkpoint identifiers in every result's `diagnostics.model_versions`.
- Seed any stochastic ops for reproducibility where possible.
- A change in either checkpoint that alters keypoint indexing is a **breaking change** to metrics — bump `metric_engine` version and re-validate the joint map.
