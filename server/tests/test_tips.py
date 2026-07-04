"""Unit tests for the rule-based tip engine (METRICS.md §9)."""

import math

from servebot.config import Thresholds
from servebot.metrics import compute_elbow_angle
from servebot.tips import elbow_tips, generate_tips

TH = Thresholds()


def _metric(value, confidence=0.86):
    return {
        "value": value,
        "unit": "degree",
        "side": "right",
        "joints": ["right_shoulder", "right_elbow", "right_wrist"],
        "keyframe_role": "contact",
        "confidence": confidence,
        "band": "straight",
        "reference_range_deg": (150.0, 180.0),
    }


def _failed_metric():
    return {
        "value": None,
        "unit": "degree",
        "side": "right",
        "compute_error": "missing_keypoint",
        "missing": ["right_wrist"],
        "confidence": 0.0,
    }


class TestElbowRules:
    def test_good_extension(self):
        tips = elbow_tips(_metric(177.6), TH)
        assert len(tips) == 1
        tip = tips[0]
        assert tip["id"] == "elbow_good_extension"
        assert tip["metric"] == "elbow_angle_deg"
        assert tip["severity"] == "info"
        assert "~178°" in tip["message"]  # {value} renders rounded degrees
        assert tip["triggered_by"] == {
            "value": 177.6,
            "threshold": 150.0,
            "comparator": "gte",
        }

    def test_boundary_150_is_good(self):
        tips = elbow_tips(_metric(150.0), TH)
        assert [t["id"] for t in tips] == ["elbow_good_extension"]

    def test_slightly_bent(self):
        tips = elbow_tips(_metric(135.4), TH)
        assert len(tips) == 1
        assert tips[0]["id"] == "elbow_slightly_bent"
        assert tips[0]["severity"] == "suggestion"
        assert "~135°" in tips[0]["message"]
        assert tips[0]["triggered_by"] == {
            "value": 135.4,
            "threshold": 150.0,
            "comparator": "lt",
        }

    def test_boundary_120_is_slightly_bent(self):
        assert [t["id"] for t in elbow_tips(_metric(120.0), TH)] == ["elbow_slightly_bent"]

    def test_too_bent(self):
        tips = elbow_tips(_metric(118.3), TH)
        assert len(tips) == 1
        assert tips[0]["id"] == "elbow_too_bent"
        assert tips[0]["severity"] == "suggestion"
        assert "~118°" in tips[0]["message"]
        # Literal triggered_by from API_CONTRACT.md §4c / METRICS.md §9.1:
        # the reported threshold is the good-band boundary (150.0, "lt").
        assert tips[0]["triggered_by"] == {
            "value": 118.3,
            "threshold": 150.0,
            "comparator": "lt",
        }

    def test_exactly_one_tip_fires_per_band(self):
        for value in (0.0, 89.9, 119.9, 120.0, 149.9, 150.0, 165.0, 180.0):
            assert len(elbow_tips(_metric(value), TH)) == 1


class TestGuards:
    def test_low_confidence_suppresses_corrective_tip(self):
        tips = elbow_tips(_metric(118.3, confidence=TH.MIN_TIP_CONFIDENCE - 0.01), TH)
        assert all(t["severity"] == "info" for t in tips)
        assert [t["id"] for t in tips] == ["elbow_low_confidence"]

    def test_confidence_at_threshold_allows_tip(self):
        tips = elbow_tips(_metric(118.3, confidence=TH.MIN_TIP_CONFIDENCE), TH)
        assert [t["id"] for t in tips] == ["elbow_too_bent"]

    def test_value_null_suppresses_corrective_tip(self):
        tips = elbow_tips(_failed_metric(), TH)
        assert [t["id"] for t in tips] == ["elbow_low_confidence"]

    def test_unimplemented_metric_produces_no_tips(self):
        assert elbow_tips(None, TH) == []


class TestGenerateTips:
    def test_null_metrics_are_skipped(self):
        metrics = {
            "elbow_angle_deg": _metric(160.0),
            "shoulder_angle_deg": None,
            "knee_flexion_deg": None,
        }
        tips = generate_tips(metrics, TH)
        assert [t["id"] for t in tips] == ["elbow_good_extension"]

    def test_never_null_always_list(self):
        assert generate_tips({}, TH) == []


class TestGoldenEndToEnd:
    """Fixed keypoints -> exact value, band, and tips[] (METRICS.md §10)."""

    def test_golden_straight_arm(self):
        points = {
            "right_shoulder": ((0.182, 1.402, 0.031), 0.94),
            "right_elbow": ((0.372, 1.560, -0.010), 0.90),
            "right_wrist": ((0.540, 1.712, -0.048), 0.86),
        }
        metric = compute_elbow_angle(points, "right", TH)
        assert metric["value"] == 177.6
        assert metric["band"] == "straight"
        assert metric["confidence"] == 0.86
        tips = generate_tips({"elbow_angle_deg": metric}, TH)
        assert [t["id"] for t in tips] == ["elbow_good_extension"]
        assert tips[0]["triggered_by"]["value"] == 177.6

    def test_golden_bent_arm_synthetic(self):
        # Elbow at origin: upper arm along +x, forearm at exactly 100° from it.
        angle = math.radians(100.0)
        points = {
            "right_shoulder": ((0.30, 0.0, 0.0), 0.95),
            "right_elbow": ((0.0, 0.0, 0.0), 0.92),
            "right_wrist": ((0.25 * math.cos(angle), 0.25 * math.sin(angle), 0.0), 0.90),
        }
        metric = compute_elbow_angle(points, "right", TH)
        assert metric["value"] == 100.0
        assert metric["band"] == "bent"
        assert metric["confidence"] == 0.90
        tips = generate_tips({"elbow_angle_deg": metric}, TH)
        assert [t["id"] for t in tips] == ["elbow_too_bent"]
        assert "~100°" in tips[0]["message"]
