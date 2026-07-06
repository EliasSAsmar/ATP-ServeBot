"""Metric engine — METRICS.md. Computes `elbow_angle_deg` from the contact
keypoints and initializes every other planned metric key to `null` per the
API_CONTRACT.md §4c nullability rule (`null` = not built; an object with
`value: null` + `compute_error` = built but failed on this serve).

The stub pipeline ships this dict as-is (all phase-2 keys null). The sam3d
pipeline overlays the phase-2 metrics (shoulder/knee/contact-height/phase-
timing/kinetic-chain/toss-placement) computed by `servebot.temporal` and
`servebot.tracking`; `toss_consistency` stays null (needs multi-serve
history).
"""

from __future__ import annotations

import math
from typing import Dict, Mapping, Optional, Sequence, Tuple

from .config import Thresholds

METRIC_ENGINE_VERSION = "metrics-1"

Vec3 = Sequence[float]
# name -> (xyz meters, score) — the metric engine's input substrate.
PointMap = Mapping[str, Tuple[Vec3, float]]

# Every planned metric key must appear in the payload (stubs as null).
STUB_METRIC_KEYS: tuple[str, ...] = (
    "shoulder_angle_deg",
    "knee_flexion_deg",
    "kinetic_chain_sequence",
    "toss_placement",
    "toss_consistency",
    "contact_height",
    "phase_timing",
)


def angle_at_joint(a: Vec3, j: Vec3, c: Vec3) -> float:
    """Interior angle (degrees) at joint J formed by neighbors A and C.

    METRICS.md §0:
        u = A - J ;  w = C - J
        theta = degrees(arccos(clamp((u.w)/(|u||w|), -1, 1)))

    Raises ValueError on a degenerate (zero-length) limb vector.
    """
    u = (a[0] - j[0], a[1] - j[1], a[2] - j[2])
    w = (c[0] - j[0], c[1] - j[1], c[2] - j[2])
    norm_u = math.sqrt(u[0] * u[0] + u[1] * u[1] + u[2] * u[2])
    norm_w = math.sqrt(w[0] * w[0] + w[1] * w[1] + w[2] * w[2])
    if norm_u == 0.0 or norm_w == 0.0:
        raise ValueError("degenerate joint configuration: zero-length limb vector")
    dot = u[0] * w[0] + u[1] * w[1] + u[2] * w[2]
    cos_theta = max(-1.0, min(1.0, dot / (norm_u * norm_w)))
    return math.degrees(math.acos(cos_theta))


def elbow_band(value: float) -> str:
    """Qualitative band for the elbow angle (METRICS.md §1 table)."""
    if value >= 165.0:
        return "straight"
    if value >= 150.0:
        return "nearly_straight"
    if value >= 120.0:
        return "slightly_bent"
    if value >= 90.0:
        return "bent"
    return "very_bent"


def _failed(side: str, compute_error: str, missing: Sequence[str]) -> dict:
    """Implemented-but-failed shape (API_CONTRACT.md §4c nullability rule)."""
    return {
        "value": None,
        "unit": "degree",
        "side": side,
        "compute_error": compute_error,
        "missing": list(missing),
        "confidence": 0.0,
    }


def compute_elbow_angle(
    points: PointMap, handedness: str, thresholds: Thresholds
) -> dict:
    """`elbow_angle_deg` per METRICS.md §1.

    Serving side = handedness. Confidence = min of the participating joints'
    scores (METRICS.md §0, [CONFIRM] resolved to `min` for v1). Any required
    joint absent or with score < MIN_KP_SCORE -> value:null + compute_error.
    """
    side = handedness
    joint_names = [f"{side}_shoulder", f"{side}_elbow", f"{side}_wrist"]
    missing = [
        n
        for n in joint_names
        if n not in points or points[n][1] < thresholds.MIN_KP_SCORE
    ]
    if missing:
        return _failed(side, "missing_keypoint", missing)

    (s_xyz, s_score), (e_xyz, e_score), (w_xyz, w_score) = (
        points[n] for n in joint_names
    )
    try:
        theta = angle_at_joint(s_xyz, e_xyz, w_xyz)
    except ValueError:
        return _failed(side, "degenerate_keypoints", [])

    value = round(theta, 1)  # 1 decimal max meaningful resolution (§0)
    return {
        "value": value,
        "unit": "degree",
        "side": side,
        "joints": joint_names,
        "keyframe_role": "contact",
        "confidence": round(min(s_score, e_score, w_score), 2),
        "band": elbow_band(value),
        "reference_range_deg": (thresholds.ELBOW_GOOD_MIN_DEG, 180.0),
    }


def build_metrics(
    points: PointMap, handedness: str, thresholds: Thresholds
) -> Dict[str, Optional[dict]]:
    """The full `result.metrics` block: elbow implemented, everything else null."""
    metrics: Dict[str, Optional[dict]] = {
        "elbow_angle_deg": compute_elbow_angle(points, handedness, thresholds)
    }
    for key in STUB_METRIC_KEYS:
        metrics[key] = None
    return metrics
