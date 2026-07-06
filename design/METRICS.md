# METRICS

Metric definitions, **actual formulas**, and the rule-based tip thresholds.

**Status (phase-2):** on the real (`sam3d`) pipeline, `elbow_angle_deg`, `shoulder_angle_deg`, `knee_flexion_deg`, `contact_height`, `phase_timing`, `kinetic_chain_sequence`, and `toss_placement` are implemented; **`toss_consistency` remains a stub** (needs multi-serve history). The stub pipeline still returns `null` for every metric except elbow. A metric that is implemented can still be `null` on a given serve when its input signal was unusable (no ball found, no serve motion, etc.).

Data sources per metric: **contact-frame** metrics (elbow) use the SAM 3D Body contact keyframe; **multi-keyframe** metrics (shoulder/knee/contact-height) use a bounded stack of ~12–16 SAM 3D Body keyframes spanning windup → follow-through; **dense temporal** metrics (phase timing, kinetic chain) use a cheap 2D YOLO-pose pass over every frame of the analysis window; **ball** metrics (toss placement, plus `result.tracking`) use YOLO ball/racket tracking with gravity-fit scale calibration. Keypoint names refer to the canonical map in `MODELS.md §4.4`.

## 0. Shared conventions

- A **3D point** is `P = (x, y, z)` in meters. `+Y` is up (see `MODELS.md §4.2` axis [CONFIRM]).
- **Vector** between joints: `v(A→B) = B − A`.
- **Angle at a joint** `J` formed by neighbors `A` and `C`:

  ```
  u = A − J
  w = C − J
  θ = degrees( arccos( clamp( (u · w) / (‖u‖ · ‖w‖), -1, 1 ) ) )
  ```

  where `·` is dot product and `‖·‖` is Euclidean norm. `clamp` guards against floating-point domain errors. Result θ ∈ [0°, 180°].
- **Side selection:** metrics that are arm/leg-specific use the **serving side** = `handedness` from the request. `handedness="right"` → use `right_*` joints. (Legs may later use the opposite/front leg — noted per metric.)
- **Confidence:** a metric's `confidence` = product (or min) of the participating keypoints' `score` values, clamped to [0,1]. **[CONFIRM] use `min` for v1** (weakest joint dominates).
- **Missing keypoints:** if any required joint has `score < MIN_KP_SCORE` (**[CONFIRM] default 0.3**) or is absent, the metric returns `{ value: null, compute_error: "missing_keypoint", missing: [...] }` (see `API_CONTRACT.md §4c nullability rule`).
- **Precision:** round `value` to **1 decimal**. Do not present sub-degree precision to users (product stance, `OVERVIEW.md §5`).

---

## 1. Elbow angle — `elbow_angle_deg` ✅ IMPLEMENTED (v1)

**Definition:** the interior angle at the **hitting elbow** at the contact keyframe — how straight the serving arm is at contact. 180° = fully extended (straight arm); smaller = more bent.

**Joints (serving side):** `S = shoulder`, `E = elbow`, `W = wrist`.

**Formula:**

```
u = S − E          # elbow → shoulder (upper arm)
w = W − E          # elbow → wrist   (forearm)
elbow_angle_deg = degrees( arccos( clamp( (u · w)/(‖u‖·‖w‖), -1, 1 ) ) )
```

**Worked example** (matches `API_CONTRACT.md §4c`, right-handed):
```
S = right_shoulder = (0.182, 1.402, 0.031)
E = right_elbow    = (0.372, 1.560, -0.010)
W = right_wrist    = (0.540, 1.712, -0.048)

u = S − E = (-0.190, -0.158, 0.041)      ‖u‖ = 0.2506
w = W − E = ( 0.168,  0.152, -0.038)     ‖w‖ = 0.2296
u·w = (-0.190)(0.168) + (-0.158)(0.152) + (0.041)(-0.038)
    = -0.03192 - 0.02402 - 0.00156 = -0.05750
cos θ = -0.05750 / (0.2506·0.2296) = -0.05750 / 0.05754 = -0.99930
θ = arccos(-0.99930) ≈ 177.9°   ← from the rounded cosine above
```
**Compute at full precision.** The `177.9°` above comes from the intermediate values *as rounded for display*. Carrying full precision through the same keypoints gives `cos θ = -0.99915…` → **θ = 177.6°**. Implementations must not round intermediates; the canonical expected value for this keypoint set is **177.6°** (this is what the metric engine's golden test asserts).

*(The literal numbers in the API example were illustrative; this shows the exact arithmetic an implementer must reproduce. Use the real keypoints at runtime.)*

**Output object (implemented form):**
```json
{
  "value": 118.3,
  "unit": "degree",
  "side": "right",
  "joints": ["right_shoulder","right_elbow","right_wrist"],
  "keyframe_role": "contact",
  "confidence": 0.88,
  "band": "slightly_bent",
  "reference_range_deg": [150.0, 180.0]
}
```

**Bands** (qualitative, for UI + tips; `reference_range_deg` = the "good" band):

| Band | Range (°) | Meaning |
|---|---|---|
| `straight` | `[165, 180]` | Well extended at contact. |
| `nearly_straight` | `[150, 165)` | Good. |
| `slightly_bent` | `[120, 150)` | Room to extend. |
| `bent` | `[90, 120)` | Noticeably bent. |
| `very_bent` | `[0, 90)` | Likely a low/mistimed contact or reconstruction issue. |

**Tip rules** — see `§9`.

---

## 2. Shoulder angle — `shoulder_angle_deg` ✅ IMPLEMENTED (phase-2, sam3d pipeline)

**Definition:** abduction/elevation angle of the hitting upper arm relative to the torso at contact (how high the arm is raised).

**Joints:** `hip(serving side)`, `shoulder(serving side)`, `elbow(serving side)` — angle at shoulder between torso vector (`shoulder→hip`) and upper-arm vector (`shoulder→elbow`).

**Formula:** angle-at-joint (§0) with `J=shoulder`, `A=hip`, `C=elbow`, evaluated on the SAM 3D Body contact keyframe after a temporal median-3 filter over the keyframe stack (kills single-frame reconstruction glitches — the fastest phase has motion blur).

**Output:** `{ value, unit:"degree", side, band, confidence, reference_range_deg:[90,140] }`.

| band | range |
|---|---|
| `low` | `< 90°` (arm not elevated enough at contact) |
| `good` | `[90°, 140°]` |
| `high` | `> 140°` |

`confidence` is 1.0 (SAM 3D Body exposes no per-keypoint confidence). `null` when the recon stack failed; stub pipeline: always `null`.

---

## 3. Knee flexion — `knee_flexion_deg` ✅ IMPLEMENTED (phase-2, sam3d pipeline)

**Definition:** deepest loading knee bend before contact (leg drive).

**Joints:** `hip`, `knee`, `ankle` per side. Angle at `knee`; 180° = straight leg, smaller = deeper bend.

**Formula:** angle-at-joint (§0) with `J=knee`, `A=hip`, `C=ankle`, computed per side across **all pre-contact SAM 3D Body keyframes** (median-3 filtered); the reported `value` is the minimum (deepest bend) and `side` is the deeper leg — no stance detection needed, the loading leg self-selects as the one that bends more.

**Output:** `{ value, unit:"degree", side, band, confidence }`.

| band | range |
|---|---|
| `deep` | `< 115°` |
| `moderate` | `[115°, 145°]` |
| `shallow` | `> 145°` (little leg drive) |

---

## 4. Kinetic chain sequencing — `kinetic_chain_sequence` ✅ IMPLEMENTED (phase-2, sam3d pipeline) — with a big caveat

**Definition:** the ordering and timing of peak angular velocities up the chain (pelvis → trunk → upper arm → forearm). A proper serve fires proximal→distal.

**Method (engineered for latency):** millisecond-level peak timing needs *dense* sampling, and dense 3D reconstruction of every frame would blow the per-serve budget — so timing comes from a **dense 2D pose pass (YOLO-pose) across every window frame**, not from the sparse SAM 3D keyframes:

- `pelvis` / `trunk`: axial-rotation-rate proxy from the projected width of the hip/shoulder line — `az(t) = arccos(width(t) / p95(width))`, rate = `|d az/dt|` (deg/s).
- `upper_arm`: `|d/dt angle(hip, shoulder, elbow)|` (2D, serving side).
- `forearm`: `|d/dt angle(shoulder, elbow, wrist)|` (2D, serving side).

All signals are Gaussian-smoothed (σ≈0.10 s); peaks are searched in the acceleration phase (racquet drop → contact, plus a small margin) — the sequence fires there, not during the slow windup.

**Output:** `{ segments[], peak_times_ms{seg}, peak_deg_s{seg}, order_correct, gaps_ms[], note }`. `peak_times_ms` are absolute clip ms; `gaps_ms[i]` = time from segment *i* to segment *i+1* (negative = out of order); `order_correct` = all gaps ≥ 0.

**⚠️ HONEST CAVEAT (confirmed by the serve-breakdown showcase):** from a **single camera**, pelvis/trunk axial rotation happens mostly *about the view axis* — the projected-width proxy is noisy and its peak time can slip by several frames. The `note` field carries this caveat verbatim; clients should present `order_correct` as indicative, not diagnostic. Proper kinetic-chain grading needs either a second view or a rotation-aware 3D fit of dense frames.

`null` when phase detection failed (no usable serve motion) or fewer than 3 segment signals were computable.

---

## 5. Toss placement — `toss_placement` ✅ IMPLEMENTED (phase-2, sam3d pipeline) — real ball tracking, not a wrist proxy

**Definition:** where the ball is tossed relative to the body at toss apex.

**Method (from the ball-racket spike):** YOLO detects the ball per frame; static detections (ball baskets, court clutter) are suppressed by grid occupancy; moving detections are chained into tracklets with velocity-predicted gating; the **toss chain** is the tracklet with the largest upward travel, gap-filled by ROI-zoom re-detection. The toss free flight (release → contact) is fit with a parabola `y(t) = a t² + b t + c`:

```
g_px      = 2a                       # px/s², should be gravity
px_per_m  = g_px / 9.81              # scale calibration — no reference object needed
apex      = vertex of the parabola   # t_apex = -b/2a
offset_forward_cm = (x_apex - body_center_x_at_release) * direction * 100 / px_per_m
```

`direction` (+1 = serving toward image +x) comes from the ball's post-contact horizontal velocity. When the gravity fit is degenerate (`g_px < 300 px/s²`), scale falls back to a 1.75 m person-height prior (`scale.method = "person_height_prior"` in `result.tracking`).

**Output:** `{ offset_forward_cm, offset_lateral_cm: null, apex_height_m, reference: "body_center" }`.

**Limitation:** `offset_lateral_cm` is `null` — with a single side-on camera the lateral (baseline) axis runs along the camera axis and is not observable. `null` when no toss arc was found (ball never detected rising).

---

## 6. Toss consistency — `toss_consistency` 🔲 STUB (cross-serve)

**Intended definition:** variance of toss placement across **multiple serves** (a session-level metric).

**Intended output:** `{ "value": null, "unit": "cm_stddev", "n_serves": null, "placement_stddev_cm": null }`.

**v1 status:** `null`. Requires persistent multi-serve history (a non-goal in v1, `OVERVIEW.md §3`).

---

## 7. Contact height — `contact_height` ✅ IMPLEMENTED (phase-2, sam3d pipeline)

**Definition:** height of the hitting wrist at contact, **normalized by player standing height** (to compare across players).

**Formula** (3D, meters, from the SAM 3D Body keyframe stack; camera y is down so "up" = −y):
```
wrist_y_m         = up(wrist, contact) − mean(up(ankles), contact)
extent(k)         = up(nose, k) − mean(up(ankles), k)          # per keyframe
standing_height_m = max_k extent(k) × 1.06                     # nose → top-of-head offset
value             = wrist_y_m / standing_height_m
```
The standing reference self-selects as the keyframe with the tallest nose-over-ankle extent (the most upright moment of the windup) — no separate calibration frame needed. The 1.06 factor converts nose height to stature (the nose sits ≈ 94% of standing height).

**Output:** `{ value, unit:"ratio", wrist_y_m, standing_height_m }`. Typical good serves contact at ~1.4–1.5× standing height. `null` when the recon stack was degenerate (non-positive extent or wrist height).

---

## 8. Phase timing — `phase_timing` ✅ IMPLEMENTED (phase-2, sam3d pipeline)

**Definition:** durations of the serve phases in ms, plus the absolute contact time.

**Method:** phase boundaries from the serving wrist's dense 2D trajectory (YOLO-pose, every window frame — from the serve-breakdown spike):

- **contact** = peak wrist reach (max smoothed wrist height, refined on the raw signal within ±150 ms).
- **acceleration start** = scan back from the peak upward wrist velocity until it falls below 10% of that peak (the racquet-drop turnaround).
- **trophy start** = last upward crossing of 40% of the pre-drop wrist rise (arm cocking upward).
- **windup start** = first sustained wrist motion (speed > 15% of the pre-trophy max).
- **follow-through end** = wrist height minimum after contact.

Guards: the wrist must travel ≥ 25% of body height across the window and the detected serve must span ≥ 400 ms, else the metric is `null` (protects static/irrelevant clips from producing garbage).

**Output:** `{ unit:"ms", contact_ms, phases: { windup, trophy, acceleration, follow_through } }` — each phase value is a **duration** in ms; `contact_ms` is absolute clip time.

---

## 9. Rule-based tip engine

**Design:** a pure function `tips(metrics, context) → Tip[]`. Deterministic, ordered, no LLM (`OVERVIEW.md §3`). Each rule maps a metric condition to a **templated** message. In v1 only the elbow rules exist; the engine is built to scale by adding rules.

### 9.1 Tip object (matches `API_CONTRACT.md §4c`)
```json
{
  "id": "elbow_too_bent",
  "metric": "elbow_angle_deg",
  "severity": "info",
  "title": "Extend at contact",
  "message": "Your hitting elbow was fairly bent at contact (~118°). ...",
  "triggered_by": { "value": 118.3, "threshold": 150.0, "comparator": "lt" }
}
```
`comparator` ∈ `{lt, lte, gt, gte, in_range, out_of_range}`. `severity` ∈ `{info, suggestion, flag}`.

### 9.2 Elbow-angle rules (v1) — evaluated in order, first match wins

| Rule id | Condition | severity | title | message template |
|---|---|---|---|---|
| `elbow_good_extension` | `value ≥ 150` | `info` | Good extension | "Nice — your hitting arm was well extended at contact (~{value}°). Keep reaching up through the ball." |
| `elbow_slightly_bent` | `120 ≤ value < 150` | `suggestion` | Reach a little higher | "Your hitting elbow was slightly bent at contact (~{value}°). Try reaching to a straighter arm for more height and easier power." |
| `elbow_too_bent` | `value < 120` | `suggestion` | Extend at contact | "Your hitting elbow was quite bent at contact (~{value}°). Focus on hitting up and out to a straighter arm at contact." |

- `{value}` renders the rounded degree value. Copy stays directional/qualitative (product stance).
- **Low-confidence guard:** if `metrics.elbow_angle_deg.confidence < MIN_TIP_CONFIDENCE` (**[CONFIRM] default 0.5**) OR `value == null`, emit **no** elbow tip; instead the client shows the "AI estimate uncertain for this serve" state (`UI.md`). Optionally emit an `info` tip `id="elbow_low_confidence"` with `severity:"info"` — **[CONFIRM] product choice**.
- If no rule matches (should not happen given full coverage) → no tip for that metric. `tips` is `[]`, never `null`.

### 9.3 Thresholds constants (single source of truth)

Implement as one config object so tuning is centralized:

```
ELBOW_GOOD_MIN_DEG        = 150.0
ELBOW_BENT_MAX_DEG        = 120.0     # below → "too bent"
MIN_KP_SCORE              = 0.30
MIN_TIP_CONFIDENCE        = 0.50
```

### 9.4 Stubbed rules

Each stubbed metric will get an analogous rule table when implemented. Until then, `null` metrics **produce no tips** and the engine skips them. Do not fabricate tips from `null` metrics.

## 10. Testing the metric engine (implementer guidance)

- Unit-test `angle_at_joint` with known vectors: right-angle (90°), straight (180°), and the worked example in §1.
- Test the nullability paths: missing joint, low score, `value:null` vs metric-absent (`null`).
- Golden test: feed a fixed keypoint set → assert exact `elbow_angle_deg.value`, `band`, and emitted `tips[]`.
