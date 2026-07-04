"""Rule-based tip engine — METRICS.md §9.

A pure, deterministic function `generate_tips(metrics, thresholds) -> [tip]`.
No LLM. Rules are evaluated in order; first match wins; one tip max per
metric. Null (unimplemented) metrics never produce tips (§9.4).

`triggered_by` convention (resolving the docs' literal examples): the
threshold reported is the *good-band boundary* the value sits relative to —
`>= ELBOW_GOOD_MIN_DEG` ("gte") for the positive tip, `< ELBOW_GOOD_MIN_DEG`
("lt") for both corrective tips. This matches the literal examples in
API_CONTRACT.md §4c and METRICS.md §9.1, where `elbow_too_bent` (value 118.3)
reports `threshold: 150.0, comparator: "lt"`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Mapping, Optional

from .config import Thresholds

TIP_ENGINE_VERSION = "tips-1"


@dataclass(frozen=True)
class _ElbowRule:
    id: str
    severity: str  # info | suggestion | flag
    title: str
    template: str  # "{value}" renders the rounded whole-degree value
    matches: Callable[[float, Thresholds], bool]
    comparator: str

    def render(self, value: float, thresholds: Thresholds) -> dict:
        return {
            "id": self.id,
            "metric": "elbow_angle_deg",
            "severity": self.severity,
            "title": self.title,
            "message": self.template.format(value=f"{value:.0f}"),
            "triggered_by": {
                "value": value,
                "threshold": thresholds.ELBOW_GOOD_MIN_DEG,
                "comparator": self.comparator,
            },
        }


# METRICS.md §9.2 — evaluated in order, first match wins.
ELBOW_RULES: tuple[_ElbowRule, ...] = (
    _ElbowRule(
        id="elbow_good_extension",
        severity="info",
        title="Good extension",
        template=(
            "Nice — your hitting arm was well extended at contact (~{value}°). "
            "Keep reaching up through the ball."
        ),
        matches=lambda v, t: v >= t.ELBOW_GOOD_MIN_DEG,
        comparator="gte",
    ),
    _ElbowRule(
        id="elbow_slightly_bent",
        severity="suggestion",
        title="Reach a little higher",
        template=(
            "Your hitting elbow was slightly bent at contact (~{value}°). "
            "Try reaching to a straighter arm for more height and easier power."
        ),
        matches=lambda v, t: t.ELBOW_BENT_MAX_DEG <= v < t.ELBOW_GOOD_MIN_DEG,
        comparator="lt",
    ),
    _ElbowRule(
        id="elbow_too_bent",
        severity="suggestion",
        title="Extend at contact",
        template=(
            "Your hitting elbow was quite bent at contact (~{value}°). "
            "Focus on hitting up and out to a straighter arm at contact."
        ),
        matches=lambda v, t: v < t.ELBOW_BENT_MAX_DEG,
        comparator="lt",
    ),
)


def _elbow_low_confidence_tip(confidence: float, thresholds: Thresholds) -> dict:
    """Optional info tip for the low-confidence guard (METRICS.md §9.2).

    Product choice ([CONFIRM] in the spec, resolved here): we DO emit this
    `info` tip so the payload carries a machine-readable reason alongside the
    client's "AI estimate uncertain" state. It is informational, never
    corrective.
    """
    return {
        "id": "elbow_low_confidence",
        "metric": "elbow_angle_deg",
        "severity": "info",
        "title": "Estimate uncertain",
        "message": (
            "We couldn't estimate your elbow angle confidently on this serve. "
            "Try better lighting and keeping your whole body in frame."
        ),
        "triggered_by": {
            "value": confidence,
            "threshold": thresholds.MIN_TIP_CONFIDENCE,
            "comparator": "lt",
        },
    }


def elbow_tips(metric: Optional[Mapping], thresholds: Thresholds) -> List[dict]:
    """Tips for the elbow metric. At most one tip is emitted (§9.2)."""
    if metric is None:  # not implemented -> no tips (§9.4)
        return []
    value = metric.get("value")
    confidence = float(metric.get("confidence", 0.0))
    # Low-confidence guard: no corrective tip when uncertain or failed.
    if value is None or confidence < thresholds.MIN_TIP_CONFIDENCE:
        return [_elbow_low_confidence_tip(confidence, thresholds)]
    for rule in ELBOW_RULES:
        if rule.matches(float(value), thresholds):
            return [rule.render(float(value), thresholds)]
    return []  # unreachable given full band coverage; tips is [] never null


def generate_tips(
    metrics: Mapping[str, Optional[Mapping]], thresholds: Thresholds
) -> List[dict]:
    """tips(metrics, context) -> Tip[] (METRICS.md §9). Deterministic, ordered."""
    tips: List[dict] = []
    tips.extend(elbow_tips(metrics.get("elbow_angle_deg"), thresholds))
    # Stubbed metrics are null and produce no tips (§9.4). New rule tables
    # slot in here as metrics are implemented.
    return tips
