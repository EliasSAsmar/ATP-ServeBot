"""Unit tests for the metric engine (METRICS.md §1, §10)."""

import math

import pytest

from servebot.config import Thresholds
from servebot.metrics import (
    STUB_METRIC_KEYS,
    angle_at_joint,
    build_metrics,
    compute_elbow_angle,
    elbow_band,
)

TH = Thresholds()

# METRICS.md §1 worked example keypoints.
S = (0.182, 1.402, 0.031)
E = (0.372, 1.560, -0.010)
W = (0.540, 1.712, -0.048)

# NOTE ON THE SPEC'S PRINTED RESULT: METRICS.md §1 prints theta = 177.9°, but
# that number comes from rounding intermediates (it uses cos = -0.99930; the
# exact cos over these keypoints is -0.9991511...). Full-precision IEEE
# arithmetic — which §1 says an implementer must reproduce ("the exact
# arithmetic") — gives 177.639...° -> 177.6° at the contract's 1-decimal
# precision. We assert the exact full-precision value.
WORKED_EXAMPLE_DEG = 177.6


def _points(s_score=0.94, e_score=0.90, w_score=0.86):
    return {
        "right_shoulder": (S, s_score),
        "right_elbow": (E, e_score),
        "right_wrist": (W, w_score),
    }


class TestAngleAtJoint:
    def test_right_angle(self):
        assert angle_at_joint((1, 0, 0), (0, 0, 0), (0, 1, 0)) == pytest.approx(90.0)

    def test_straight(self):
        assert angle_at_joint((1, 0, 0), (0, 0, 0), (-1, 0, 0)) == pytest.approx(180.0)

    def test_zero_angle(self):
        assert angle_at_joint((1, 1, 0), (0, 0, 0), (2, 2, 0)) == pytest.approx(0.0, abs=1e-3)

    def test_worked_example_exact(self):
        theta = angle_at_joint(S, E, W)
        assert theta == pytest.approx(177.639, abs=0.001)
        assert round(theta, 1) == WORKED_EXAMPLE_DEG

    def test_worked_example_matches_doc_cosine(self):
        # The doc's printed 177.9° reproduces only with its rounded cosine.
        assert math.degrees(math.acos(-0.99930)) == pytest.approx(177.9, abs=0.05)

    def test_clamp_guards_fp_domain(self):
        # Collinear vectors whose cosine could exceed 1.0 by float error.
        a, j, c = (0.1, 0.2, 0.3), (0.0, 0.0, 0.0), (0.3, 0.6, 0.9)
        assert angle_at_joint(a, j, c) == pytest.approx(0.0, abs=1e-6)

    def test_degenerate_raises(self):
        with pytest.raises(ValueError):
            angle_at_joint((0, 0, 0), (0, 0, 0), (1, 0, 0))


class TestElbowBands:
    @pytest.mark.parametrize(
        "value,band",
        [
            (180.0, "straight"),
            (165.0, "straight"),
            (164.9, "nearly_straight"),
            (150.0, "nearly_straight"),
            (149.9, "slightly_bent"),
            (120.0, "slightly_bent"),
            (119.9, "bent"),
            (90.0, "bent"),
            (89.9, "very_bent"),
            (0.0, "very_bent"),
        ],
    )
    def test_band_boundaries(self, value, band):
        assert elbow_band(value) == band


class TestComputeElbowAngle:
    def test_golden_right_handed(self):
        m = compute_elbow_angle(_points(), "right", TH)
        assert m["value"] == WORKED_EXAMPLE_DEG
        assert m["unit"] == "degree"
        assert m["side"] == "right"
        assert m["joints"] == ["right_shoulder", "right_elbow", "right_wrist"]
        assert m["keyframe_role"] == "contact"
        assert m["confidence"] == 0.86  # min of joint scores
        assert m["band"] == "straight"
        assert m["reference_range_deg"] == (150.0, 180.0)

    def test_serving_side_selection_left(self):
        points = {
            "left_shoulder": (S, 0.9),
            "left_elbow": (E, 0.9),
            "left_wrist": (W, 0.9),
        }
        m = compute_elbow_angle(points, "left", TH)
        assert m["side"] == "left"
        assert m["joints"] == ["left_shoulder", "left_elbow", "left_wrist"]
        assert m["value"] == WORKED_EXAMPLE_DEG

    def test_missing_joint_is_value_null_with_compute_error(self):
        points = _points()
        del points["right_wrist"]
        m = compute_elbow_angle(points, "right", TH)
        assert m["value"] is None
        assert m["compute_error"] == "missing_keypoint"
        assert m["missing"] == ["right_wrist"]
        assert m["confidence"] == 0.0
        assert "band" not in m  # failed shape carries no band/joints

    def test_low_score_joint_counts_as_missing(self):
        m = compute_elbow_angle(_points(w_score=TH.MIN_KP_SCORE - 0.01), "right", TH)
        assert m["value"] is None
        assert m["missing"] == ["right_wrist"]

    def test_score_at_threshold_is_usable(self):
        m = compute_elbow_angle(_points(w_score=TH.MIN_KP_SCORE), "right", TH)
        assert m["value"] == WORKED_EXAMPLE_DEG

    def test_degenerate_geometry(self):
        points = {
            "right_shoulder": (E, 0.9),  # shoulder collapsed onto elbow
            "right_elbow": (E, 0.9),
            "right_wrist": (W, 0.9),
        }
        m = compute_elbow_angle(points, "right", TH)
        assert m["value"] is None
        assert m["compute_error"] == "degenerate_keypoints"


class TestBuildMetrics:
    def test_nullability_rule(self):
        """null = not built; object with value:null = built but failed (§4c)."""
        metrics = build_metrics(_points(), "right", TH)
        assert set(metrics) == {"elbow_angle_deg", *STUB_METRIC_KEYS}
        assert metrics["elbow_angle_deg"]["value"] == WORKED_EXAMPLE_DEG
        for key in STUB_METRIC_KEYS:
            assert metrics[key] is None

    def test_failed_elbow_still_object(self):
        metrics = build_metrics({}, "right", TH)
        assert metrics["elbow_angle_deg"] is not None
        assert metrics["elbow_angle_deg"]["value"] is None
        assert metrics["elbow_angle_deg"]["compute_error"] == "missing_keypoint"
