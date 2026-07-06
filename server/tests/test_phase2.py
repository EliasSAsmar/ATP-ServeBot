"""Phase-2 tests — ball/racket tracking + multi-frame temporal metrics.

Three tiers (mirroring test_pipeline_sam3d.py):
  * Always-run: contract shape — the stub emits the new metric keys and
    `tracking` as null; the new schema objects validate the shared shapes.
  * numpy-only: pure-math units (gravity fit, phase detection, keyframe
    selection, kinetic chain, recon-stack metrics). Skip when numpy is not
    installed (the base venv is torch- and numpy-free).
  * Real-model e2e on an actual serve clip: skips unless SAM3D env vars,
    ultralytics AND SERVEBOT_TEST_SERVE_CLIP (path to a serve mp4) are set.
"""

from __future__ import annotations

import os

import pytest

from .conftest import AUTH, create_serve, make_settings, poll_until_terminal, upload_clip
from .test_pipeline_sam3d import SAM3D_CKPT, SAM3D_REPO, _sam3d_available

TEST_CLIP = os.environ.get("SERVEBOT_TEST_SERVE_CLIP", "")


# ---------------------------------------------------------- always-run tests


def test_stub_emits_new_keys_as_null(client):
    """The stub keeps the phase-2 contract keys present-and-null."""
    object_key, meta = upload_clip(client)
    r = create_serve(client, object_key, meta)
    payload = poll_until_terminal(client, r.json()["job_id"])
    assert payload["status"] == "succeeded"
    result = payload["result"]
    assert "tracking" in result and result["tracking"] is None
    metrics = result["metrics"]
    for key in (
        "shoulder_angle_deg", "knee_flexion_deg", "kinetic_chain_sequence",
        "toss_placement", "toss_consistency", "contact_height", "phase_timing",
    ):
        assert key in metrics and metrics[key] is None
    assert metrics["elbow_angle_deg"]["value"] is not None


def test_new_metric_schemas_match_shared_contract():
    """The exact shapes the frontend is building against must validate."""
    from servebot.schemas import (
        ContactHeightMetric,
        KineticChainMetric,
        KneeFlexionMetric,
        PhaseTimingMetric,
        ShoulderAngleMetric,
        TossPlacementMetric,
        TrackingBlock,
    )

    ShoulderAngleMetric.model_validate({
        "value": 112.4, "unit": "degree", "side": "right", "band": "good",
        "confidence": 1.0, "reference_range_deg": [90.0, 140.0],
    })
    KneeFlexionMetric.model_validate({
        "value": 121.0, "unit": "degree", "side": "left", "band": "moderate",
        "confidence": 1.0,
    })
    ContactHeightMetric.model_validate({
        "value": 1.42, "unit": "ratio", "wrist_y_m": 2.51, "standing_height_m": 1.77,
    })
    PhaseTimingMetric.model_validate({
        "unit": "ms", "contact_ms": 1240,
        "phases": {"windup": 480, "trophy": 320, "acceleration": 160, "follow_through": 300},
    })
    KineticChainMetric.model_validate({
        "segments": ["pelvis", "trunk", "upper_arm", "forearm"],
        "peak_times_ms": {"pelvis": 900, "trunk": 950, "upper_arm": 1000, "forearm": 1060},
        "peak_deg_s": {"pelvis": 310.0, "trunk": 420.5, "upper_arm": 800.1, "forearm": 1400.0},
        "order_correct": True, "gaps_ms": [50, 50, 60], "note": "single-camera caveat",
    })
    TossPlacementMetric.model_validate({
        "offset_forward_cm": 24.0, "offset_lateral_cm": None,
        "apex_height_m": 2.64, "reference": "body_center",
    })
    TrackingBlock.model_validate({
        "ball": {
            "points": [{"t_ms": 400, "x": 512.0, "y": 300.5, "in_flight": True}],
            "apex": {"t_ms": 860, "height_m": 2.64},
        },
        "racket": {
            "peak_speed_m_s": 42.9,
            "points": [{"t_ms": 400, "x": 400.0, "y": 500.0}],
        },
        "contact": {"t_ms": 1000, "height_m": 2.59},
        "scale": {"px_per_m": 138.9, "method": "gravity_fit"},
    })


def test_temporal_and_tracking_import_torch_free():
    import sys

    had_torch = "torch" in sys.modules
    import servebot.temporal  # noqa: F401
    import servebot.tracking  # noqa: F401

    if not had_torch:
        assert "torch" not in sys.modules


def test_phase2_settings_env_wiring(monkeypatch):
    from servebot.config import Settings

    monkeypatch.setenv("SERVEBOT_YOLO_DET_MODEL", "yolo11x.pt")
    monkeypatch.setenv("SERVEBOT_SAM3D_KEYFRAMES", "16")
    monkeypatch.setenv("SERVEBOT_TRACK_WINDOW_BEFORE_MS", "2000")
    s = Settings.from_env()
    assert s.yolo_det_model == "yolo11x.pt"
    assert s.sam3d_keyframes == 16
    assert s.track_window_before_ms == 2000
    d = Settings()
    assert d.yolo_det_model == "yolo11m.pt"
    assert d.sam3d_keyframes == 14


# ------------------------------------------------------------- numpy-only
# (the base venv is numpy-free; each test below skips itself when numpy is
# missing — a module-level importorskip would skip the always-run tier too)

def _np():
    return pytest.importorskip("numpy")


def test_gravity_fit_recovers_g_and_apex():
    np = _np()
    from servebot.tracking import fit_toss_parabola

    # synthetic free flight at 140 px/m: g = 9.81 * 140 px/s^2, apex at t=1.2s
    g_true = 9.81 * 140.0
    t = np.linspace(0.6, 1.5, 24)
    y = 300.0 + 0.5 * g_true * (t - 1.2) ** 2  # y down = +
    g_fit, t_apex, y_apex = fit_toss_parabola(t, y)
    assert abs(g_fit - g_true) < 1.0
    assert abs(t_apex - 1.2) < 0.01
    assert abs(y_apex - 300.0) < 0.5


def _synthetic_serve_pose(n=90, fps=30.0):
    """A right-handed serve-like wrist trajectory + static body."""
    np = _np()
    pose = np.zeros((n, 17, 2))
    conf = np.ones((n, 17))
    t = np.arange(n) / fps
    # static body: nose y=250, hips y=520, ankles y=700
    pose[:, 0] = (640, 250)
    for j, y in ((5, 330), (6, 330), (11, 520), (12, 520), (15, 700), (16, 700)):
        pose[:, j] = (640, y)
    # serving wrist (idx 10): low -> trophy rise -> drop -> whip up -> fall
    wy = np.full(n, 620.0)
    wy[10:30] = np.linspace(620, 430, 20)   # windup/trophy rise
    wy[30:45] = np.linspace(430, 520, 15)   # racquet drop
    wy[45:56] = np.linspace(520, 210, 11)   # acceleration to contact
    wy[56:80] = np.linspace(210, 560, 24)   # follow-through
    wy[80:] = 560.0
    pose[:, 10, 0] = 660.0
    pose[:, 10, 1] = wy
    return pose, conf, fps


def test_detect_phases_on_synthetic_serve():
    _np()
    from servebot.temporal import detect_phases

    pose, conf, fps = _synthetic_serve_pose()
    ph = detect_phases(pose, conf, fps, "right")
    assert ph is not None
    assert ph.i_start <= ph.i_trophy <= ph.i_accel <= ph.i_contact <= ph.i_end
    assert abs(ph.i_contact - 55) <= 2          # peak reach
    assert 40 <= ph.i_accel <= 50               # racquet-drop turnaround
    assert ph.i_end >= 70


def test_detect_phases_rejects_static_clip():
    _np()
    from servebot.temporal import detect_phases

    pose, conf, fps = _synthetic_serve_pose()
    pose[:, 10, 1] = 620.0  # freeze the wrist -> motion gate must trip
    assert detect_phases(pose, conf, fps, "right") is None


def test_select_keyframes_bounded_and_includes_contact():
    _np()
    from servebot.temporal import Phases, select_keyframes

    ph = Phases(i_start=5, i_trophy=28, i_accel=44, i_contact=55, i_end=78)
    picks = select_keyframes(ph, n_frames=90, budget=14)
    assert 10 <= len(picks) <= 14
    assert ph.i_contact in picks
    assert picks[0] >= 0 and picks[-1] <= 89
    assert picks == sorted(set(picks))
    # denser sampling through acceleration than through windup
    accel = [p for p in picks if ph.i_accel <= p <= ph.i_contact]
    assert len(accel) >= 3


def test_kinetic_chain_sequential_peaks():
    np = _np()
    from servebot.temporal import Phases, kinetic_chain_2d

    n, fps = 70, 30.0
    t = np.arange(n) / fps
    pose = np.zeros((n, 17, 2))
    conf = np.ones((n, 17))

    def sig(center_s, width_s=0.10):
        return 1.0 / (1.0 + np.exp(-(t - center_s) / width_s))

    # hip/shoulder line widths shrink through sigmoids -> azimuth-proxy peaks
    pose[:, 11, 0] = 600 - 40 * sig(1.20)   # left hip
    pose[:, 12, 0] = 680                    # right hip
    pose[:, 5, 0] = 590 - 50 * sig(1.30)    # left shoulder
    pose[:, 6, 0] = 690                     # right shoulder
    for j, y in ((11, 520), (12, 520), (5, 330), (6, 330)):
        pose[:, j, 1] = y
    # serving arm: elbow rotates about the shoulder (peak rate ~1.40s),
    # wrist extends about the elbow (peak rate ~1.50s). Sweeps chosen so the
    # 3-point angles stay clear of the 0/180 arccos folds (monotone signals).
    th_sh = np.radians(30 + 55 * sig(1.40))
    pose[:, 8, 0] = pose[:, 6, 0] + 60 * np.cos(th_sh)
    pose[:, 8, 1] = pose[:, 6, 1] - 60 * np.sin(th_sh)
    th_el = np.radians(10 + 120 * sig(1.50))
    pose[:, 10, 0] = pose[:, 8, 0] + 55 * np.cos(th_sh + th_el)
    pose[:, 10, 1] = pose[:, 8, 1] - 55 * np.sin(th_sh + th_el)

    ph = Phases(i_start=2, i_trophy=20, i_accel=32, i_contact=50, i_end=65)
    out = kinetic_chain_2d(pose, conf, fps, "right", ph)
    assert out is not None
    assert out["segments"] == ["pelvis", "trunk", "upper_arm", "forearm"]
    times = [out["peak_times_ms"][s] for s in out["segments"]]
    assert times == sorted(times), times    # proximal -> distal
    assert out["order_correct"] is True
    assert len(out["gaps_ms"]) == 3
    assert "single camera" in out["note"] or "2D" in out["note"]


def test_metrics_from_recon_stack():
    np = _np()
    from servebot.skeleton import JOINT_INDEX
    from servebot.temporal import metrics_from_recon_stack

    K = 5
    kp3 = np.zeros((K, 70, 3))
    j = JOINT_INDEX
    # camera coords: y down (up = -y); standing frames 0..3, contact at 4
    for k in range(K):
        kp3[k, j["nose"]] = (0.0, -1.60, 0.0)
        for s in ("left", "right"):
            kp3[k, j[f"{s}_ankle"]] = (0.0, -0.10, 0.0)
            kp3[k, j[f"{s}_hip"]] = (0.0, -0.90, 0.0)
            kp3[k, j[f"{s}_knee"]] = (0.0, -0.50, 0.0)
            kp3[k, j[f"{s}_shoulder"]] = (0.0, -1.40, 0.0)
    # loading bend on the right knee, frames 1-3 (survives the median-3 filter)
    for k in (1, 2, 3):
        kp3[k, j["right_knee"]] = (0.20, -0.50, 0.0)
    # contact frame: reaching arm
    kp3[4, j["right_elbow"]] = (0.30, -1.55, 0.0)
    kp3[4, j["right_wrist"]] = (0.55, -2.20, 0.0)

    out = metrics_from_recon_stack(kp3, contact_stack_index=4,
                                   joint_index=j, handedness="right")
    sh = out["shoulder_angle_deg"]
    assert sh["side"] == "right" and sh["unit"] == "degree"
    assert 100.0 < sh["value"] < 130.0          # analytic ~116.6 deg
    assert sh["band"] == "good"

    kn = out["knee_flexion_deg"]
    assert kn["side"] == "right"
    assert 115.0 < kn["value"] < 140.0          # analytic ~126.9 deg
    assert kn["band"] == "moderate"

    ch = out["contact_height"]
    assert ch["wrist_y_m"] == pytest.approx(2.10, abs=0.02)
    assert ch["standing_height_m"] == pytest.approx(1.59, abs=0.02)
    assert ch["value"] == pytest.approx(2.10 / 1.59, abs=0.02)


def test_phase_timing_block_math():
    _np()
    from servebot.temporal import Phases, phase_timing_block

    ph = Phases(i_start=6, i_trophy=24, i_accel=39, i_contact=48, i_end=69)
    out = phase_timing_block(ph, fps=30.0, window_t0_ms=200)
    assert out["unit"] == "ms"
    assert out["contact_ms"] == 200 + 1600
    assert out["phases"] == {
        "windup": 600, "trophy": 500, "acceleration": 300, "follow_through": 700,
    }


# ------------------------------------------- real-model e2e on a serve clip


requires_full_stack = pytest.mark.skipif(
    not (_sam3d_available() and TEST_CLIP and os.path.isfile(TEST_CLIP)),
    reason="needs torch+MPS, SAM3D env vars, ultralytics and "
    "SERVEBOT_TEST_SERVE_CLIP pointing at a real serve mp4",
)


@requires_full_stack
def test_phase2_end_to_end_real_serve(tmp_path):
    """Full flow on a REAL serve: populated temporal metrics + tracking."""
    import time

    from fastapi.testclient import TestClient

    from servebot.main import create_app

    pytest.importorskip("ultralytics")
    import cv2

    cap = cv2.VideoCapture(TEST_CLIP)
    fps = cap.get(cv2.CAP_PROP_FPS)
    w, h = int(cap.get(3)), int(cap.get(4))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    clip_bytes = open(TEST_CLIP, "rb").read()
    duration_ms = int(n / fps * 1000)
    contact_ms = int(os.environ.get("SERVEBOT_TEST_SERVE_CONTACT_MS", "1000"))

    settings = make_settings(
        tmp_path, pipeline="sam3d", sam3d_repo=SAM3D_REPO,
        sam3d_checkpoint_dir=SAM3D_CKPT, device="mps",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        meta = {
            "content_type": "video/mp4", "byte_size": len(clip_bytes),
            "duration_ms": duration_ms, "fps": fps, "width": w, "height": h,
        }
        r = client.post("/v1/uploads", json=meta, headers=AUTH)
        assert r.status_code == 200, r.text
        up = r.json()
        pr = client.put(up["upload_url"], content=clip_bytes,
                        headers={"Content-Type": "video/mp4"})
        assert pr.status_code == 200, pr.text

        body = {
            "object_key": up["object_key"], "handedness": "right",
            "contact_timestamp_ms": contact_ms,
            "clip": {k: meta[k] for k in
                     ("duration_ms", "fps", "width", "height", "content_type")},
        }
        r = client.post("/v1/serves", json=body, headers=AUTH)
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]

        deadline = time.time() + 600
        payload = None
        while time.time() < deadline:
            payload = client.get(f"/v1/serves/{job_id}", headers=AUTH).json()
            if payload["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.5)
        assert payload and payload["status"] == "succeeded", payload

        m = payload["result"]["metrics"]
        assert m["elbow_angle_deg"]["value"] is not None
        assert m["shoulder_angle_deg"] is not None
        assert 0.0 < m["shoulder_angle_deg"]["value"] < 180.0
        assert m["knee_flexion_deg"] is not None
        assert 60.0 < m["knee_flexion_deg"]["value"] < 180.0
        assert m["contact_height"] is not None
        assert 0.8 < m["contact_height"]["value"] < 2.0
        assert m["phase_timing"] is not None
        assert sum(m["phase_timing"]["phases"].values()) >= 400
        assert m["kinetic_chain_sequence"] is not None
        assert len(m["kinetic_chain_sequence"]["segments"]) >= 3
        assert m["toss_placement"] is not None
        assert 1.5 < m["toss_placement"]["apex_height_m"] < 4.5
        assert m["toss_consistency"] is None  # needs multi-serve history

        tr = payload["result"]["tracking"]
        assert tr is not None
        assert len(tr["ball"]["points"]) >= 10
        assert any(p["in_flight"] for p in tr["ball"]["points"])
        assert tr["scale"]["px_per_m"] > 10
        assert tr["scale"]["method"] in ("gravity_fit", "person_height_prior")
        assert tr["contact"]["height_m"] > 1.5
