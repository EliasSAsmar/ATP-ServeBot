# METRICS

Metric definitions, **actual formulas**, and the rule-based tip thresholds. **Elbow angle is fully specified and implemented in v1.** Every other metric is **specified as a stub** (shape + intended formula) and returns `null` in the API until built.

All metrics are computed from the **3D keypoints (meters)** emitted by SAM 3D Body on the contact keyframe, except where a metric explicitly needs multiple frames (flagged, and stubbed in v1). Keypoint names refer to the canonical map in `MODELS.md §4.4`.

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
θ = arccos(-0.99930) = 177.9°
```
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

## 2. Shoulder angle — `shoulder_angle_deg` 🔲 STUB

**Intended definition:** abduction/elevation angle of the hitting upper arm relative to the torso at contact (how high the arm is raised).

**Intended joints:** `hip(serving side)`, `shoulder(serving side)`, `elbow(serving side)` — angle at shoulder between torso vector (`shoulder→hip`) and upper-arm vector (`shoulder→elbow`).

**Intended formula:** angle-at-joint with `J=shoulder`, `A=hip`, `C=elbow`. Optionally reported relative to spine vertical.

**v1 status:** returns `null`.

---

## 3. Knee flexion — `knee_flexion_deg` 🔲 STUB

**Intended definition:** flexion of the loading/front knee (leg drive). Requires choosing which leg (front vs back) — depends on stance detection, deferred.

**Intended joints:** `hip`, `knee`, `ankle` (chosen side). Angle at `knee`.

**Intended formula:** angle-at-joint with `J=knee`, `A=hip`, `C=ankle`. 180° = straight leg; smaller = deeper bend.

**v1 status:** `null`. Note: most informative during the *loading* phase, not contact → needs a loading keyframe (multi-keyframe, phase-2).

---

## 4. Kinetic chain sequencing — `kinetic_chain_sequence` 🔲 STUB (temporal)

**Intended definition:** the ordering and timing of peak angular velocities up the chain (legs → hips → trunk → shoulder → elbow → wrist). A proper serve fires proximal→distal.

**Intended output shape:**
```json
{
  "value": null,
  "unit": "ordering",
  "segments": ["hip","trunk","shoulder","elbow","wrist"],
  "peak_times_ms": null,
  "order_correct": null,
  "gaps_ms": null
}
```

**Intended method:** track per-segment angular velocity across a **sequence of keyframes/frames**, find each segment's peak-velocity time, check monotonic proximal→distal ordering and inter-segment gaps.

**v1 status:** `null`. **Hard-blocked** on multi-frame keypoints (v1 reconstructs one frame). This is the flagship phase-2 metric.

---

## 5. Toss placement — `toss_placement` 🔲 STUB

**Intended definition:** where the ball is tossed relative to the body at apex (forward/back, left/right), from the tossing-hand wrist trajectory as a **proxy** (ball not tracked in v1).

**Intended output:** `{ "value": null, "unit": "cm", "offset_forward_cm": null, "offset_lateral_cm": null, "reference": "front_foot" }`.

**v1 status:** `null`. Depends on tossing-arm trajectory over time (temporal) and, ideally, ball tracking (phase-2). Proxy-only until then.

---

## 6. Toss consistency — `toss_consistency` 🔲 STUB (cross-serve)

**Intended definition:** variance of toss placement across **multiple serves** (a session-level metric).

**Intended output:** `{ "value": null, "unit": "cm_stddev", "n_serves": null, "placement_stddev_cm": null }`.

**v1 status:** `null`. Requires persistent multi-serve history (a non-goal in v1, `OVERVIEW.md §3`).

---

## 7. Contact height — `contact_height` 🔲 STUB

**Intended definition:** height of the hitting wrist at contact, **normalized by player standing height** (to compare across players).

**Intended formula:**
```
contact_height_ratio = wrist_y(contact) / standing_height
standing_height ≈ (top_of_head_y − mean(ankle_y))  # from a neutral/standing frame
```

**Intended output:** `{ "value": null, "unit": "ratio", "wrist_y_m": null, "standing_height_m": null }`.

**v1 status:** `null`. Computable at contact from a single frame **once** a standing-height reference exists (needs a calibration/neutral frame). Good early phase-2 candidate.

---

## 8. Phase timing — `phase_timing` 🔲 STUB (temporal)

**Intended definition:** durations of serve phases — start → trophy → contact → follow-through — in ms.

**Intended output:**
```json
{
  "value": null,
  "unit": "ms",
  "phases": {"windup": null, "trophy": null, "acceleration": null, "follow_through": null},
  "contact_ms": null
}
```

**Intended method:** detect phase boundaries from wrist/elbow kinematics across the clip.

**v1 status:** `null`. Temporal; phase-2.

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
