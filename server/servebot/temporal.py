"""Temporal serve metrics — phase timing, kinetic chain, multi-frame 3D.

Productionization of the serve-breakdown spike's compute_metrics.py, split by
data source (engineered against the per-serve latency budget):

  * DENSE, CHEAP: 2D YOLO-pose across every window frame drives phase
    boundaries + kinetic-chain *timing* — millisecond-level events need dense
    sampling, and dense 3D reconstruction would blow the budget.
  * SPARSE, EXPENSIVE: SAM 3D Body on ~12-16 keyframes spanning
    windup -> trophy -> acceleration -> contact -> follow-through drives the
    metric-space angles (shoulder/knee) and contact height.

HONEST CAVEAT (confirmed by the serve-breakdown showcase): from a single
camera, pelvis/trunk axial rotation is nearly along the view axis, so the
kinetic-chain *ordering/timing* of the proximal segments is noisy. We report
it with a `note` saying exactly that; treat `order_correct` as indicative.

Pure numpy — no torch. Unit-testable wherever numpy is available.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .tracking import (
    P_L_ANK, P_L_EL, P_L_HIP, P_L_KNEE, P_L_SH, P_L_WR, P_NOSE,
    P_R_ANK, P_R_EL, P_R_HIP, P_R_KNEE, P_R_SH, P_R_WR,
)

log = logging.getLogger("servebot.temporal")

# Motion gate: the serving wrist must travel at least this fraction of the
# person's bbox height across the window, or we refuse to detect phases
# (protects against static/irrelevant clips producing garbage metrics).
MIN_WRIST_TRAVEL_FRAC = 0.25

# Minimum believable serve duration (windup start -> follow-through end).
MIN_SERVE_MS = 400

KINETIC_CHAIN_NOTE = (
    "Timing from dense 2D pose (single camera). Pelvis/trunk rotate mostly "
    "about the view axis, so their peak times are noisy proxies — treat "
    "ordering as indicative, not diagnostic."
)


def smooth(y, t, sigma: float = 0.10):
    """Gaussian smoothing on a (possibly non-uniform) time axis, NaN-aware."""
    import numpy as np

    y = np.asarray(y, dtype=float)
    t = np.asarray(t, dtype=float)
    valid = np.isfinite(y)
    out = np.full_like(y, np.nan)
    if valid.sum() < 2:
        return out
    for i in range(len(t)):
        w = np.exp(-0.5 * ((t - t[i]) / sigma) ** 2) * valid
        s = w.sum()
        if s > 0:
            out[i] = np.nansum(w * np.where(valid, y, 0.0)) / s
    return out


def med3(a):
    """Temporal median-3 — kills single-frame reconstruction glitches."""
    import numpy as np

    out = a.copy()
    if len(a) >= 3:
        out[1:-1] = np.median(np.stack([a[:-2], a[1:-1], a[2:]]), axis=0)
    return out


def angle3(a, b, c):
    """Angle at b (deg) for stacked points (..., 3) or (..., 2)."""
    import numpy as np

    v1, v2 = a - b, c - b
    cos = np.sum(v1 * v2, axis=-1) / (
        np.linalg.norm(v1, axis=-1) * np.linalg.norm(v2, axis=-1) + 1e-9
    )
    return np.degrees(np.arccos(np.clip(cos, -1, 1)))


# ---------------------------------------------------------------------------
# Phase detection from the dense 2D wrist trajectory
# ---------------------------------------------------------------------------


@dataclass
class Phases:
    """Window-frame *indices* of the serve phase boundaries."""

    i_start: int      # windup start
    i_trophy: int     # trophy position reached
    i_accel: int      # racquet-drop turnaround -> upward acceleration
    i_contact: int    # peak serving-wrist reach
    i_end: int        # follow-through end


def detect_phases(pose_xy, pose_conf, fps: float, handedness: str) -> Optional[Phases]:
    """Serve phases from the serving wrist's 2D trajectory (spike §phases).

    Returns None when the wrist doesn't move enough (static clip), tracking
    is too sparse, or the detected serve is implausibly short.
    """
    import numpy as np

    n = len(pose_xy)
    if n < int(0.5 * fps):
        return None
    wr = P_R_WR if handedness == "right" else P_L_WR
    t = np.arange(n) / fps

    wx = pose_xy[:, wr, 0].astype(float)
    wy = pose_xy[:, wr, 1].astype(float)
    ok = np.isfinite(wx) & np.isfinite(wy) & (pose_conf[:, wr] > 0.2)
    if ok.sum() < 0.5 * n:
        return None
    wx[~ok] = np.nan
    wy[~ok] = np.nan

    # person scale for the motion gate
    hip_y = np.nanmean(pose_xy[:, [P_L_HIP, P_R_HIP], 1], axis=1)
    ank_y = np.nanmean(pose_xy[:, [P_L_ANK, P_R_ANK], 1], axis=1)
    nose_y = pose_xy[:, P_NOSE, 1].astype(float)
    body_h = np.nanmedian(ank_y - nose_y)
    if not np.isfinite(body_h) or body_h <= 0:
        return None

    wy_up = -wy  # up is +
    wy_s = smooth(wy_up, t, 0.08)
    if np.nanmax(wy_s) - np.nanmin(wy_s) < MIN_WRIST_TRAVEL_FRAC * body_h:
        return None  # motion gate: not a serve

    # contact = peak serving-wrist reach (refined on the raw signal)
    ic = int(np.nanargmax(wy_s))
    near = np.where(np.abs(t - t[ic]) < 0.15)[0]
    raw = np.where(np.isfinite(wy_up[near]), wy_up[near], -np.inf)
    if np.isfinite(raw).any():
        ic = int(near[np.argmax(raw)])

    # acceleration start: scan back from peak upward wrist velocity until it
    # falls below 10% of the peak — the racquet-drop turnaround.
    v_up = np.gradient(np.where(np.isfinite(wy_s), wy_s, np.nanmean(wy_s)), t)
    i_vpeak = int(np.argmax(v_up[: ic + 1]))
    if v_up[i_vpeak] <= 0:
        return None
    i_accel = i_vpeak
    while i_accel > 0 and v_up[i_accel] > 0.10 * v_up[i_vpeak]:
        i_accel -= 1

    # trophy start: last upward crossing of 40% of the pre-drop wrist rise
    pre = wy_s[: i_accel + 1]
    if len(pre) < 3 or not np.isfinite(pre).any():
        i_trophy = max(0, i_accel - int(0.5 * fps))
    else:
        base = np.nanpercentile(pre, 10)
        lvl = base + 0.40 * (np.nanmax(pre) - base)
        above = pre > lvl
        cross = [i for i in range(1, len(above)) if above[i] and not above[i - 1]]
        i_trophy = cross[-1] if cross else max(0, i_accel - int(0.5 * fps))

    # windup start: first sustained wrist motion before trophy
    wx_s = smooth(wx, t, 0.08)
    spd = np.hypot(np.gradient(np.where(np.isfinite(wx_s), wx_s, np.nanmean(wx_s)), t),
                   np.gradient(np.where(np.isfinite(wy_s), wy_s, np.nanmean(wy_s)), t))
    spd = smooth(spd, t, 0.15)
    pre_max = np.nanmax(spd[: max(i_trophy, 1)]) if i_trophy > 0 else np.nanmax(spd)
    thr = 0.15 * pre_max
    i_start = 0
    for i in range(len(spd)):
        if np.isfinite(spd[i]) and spd[i] > thr:
            i_start = i
            break

    # follow-through end: wrist height minimum after contact
    post = wy_s[ic:]
    i_end = ic + int(np.nanargmin(post)) if np.isfinite(post).any() else n - 1

    if not (i_start <= i_trophy <= i_accel <= ic <= i_end):
        return None
    if (i_end - i_start) / fps * 1000.0 < MIN_SERVE_MS:
        return None
    return Phases(i_start=i_start, i_trophy=i_trophy, i_accel=i_accel, i_contact=ic, i_end=i_end)


def select_keyframes(phases: Phases, n_frames: int, budget: int = 14) -> List[int]:
    """~12-16 window-frame indices spanning the serve, dense near contact.

    Allocation (from the serve-breakdown spike's sampling): 2 windup,
    3 trophy, dense acceleration, contact, 3 follow-through — deduped and
    capped at `budget`.
    """
    import numpy as np

    p = phases
    picks: List[int] = []
    picks += list(np.linspace(p.i_start, p.i_trophy, 2, dtype=int))
    picks += list(np.linspace(p.i_trophy, p.i_accel, 3, dtype=int))
    n_accel = max(2, budget - 9)
    picks += list(np.linspace(p.i_accel, p.i_contact, n_accel + 1, dtype=int))
    picks += list(np.linspace(p.i_contact, p.i_end, 4, dtype=int))
    picks = sorted(set(int(min(max(i, 0), n_frames - 1)) for i in picks))
    while len(picks) > budget:
        # drop the pick whose removal loses the least coverage, never contact
        gaps = [
            (picks[i + 1] - picks[i - 1], i)
            for i in range(1, len(picks) - 1)
            if picks[i] != p.i_contact
        ]
        if not gaps:
            break
        picks.pop(min(gaps)[1])
    return picks


# ---------------------------------------------------------------------------
# Kinetic chain timing from dense 2D pose
# ---------------------------------------------------------------------------


def kinetic_chain_2d(
    pose_xy, pose_conf, fps: float, handedness: str, phases: Phases
) -> Optional[dict]:
    """Peak angular-velocity timing per segment from dense 2D pose.

    Segments (proximal -> distal): pelvis, trunk, upper_arm, forearm.
    Pelvis/trunk axial rotation is estimated from the projected width of the
    hip/shoulder lines (arccos of normalized width) — a single-camera proxy
    (see KINETIC_CHAIN_NOTE). Times are window-frame ms.
    """
    import numpy as np

    n = len(pose_xy)
    t = np.arange(n) / fps
    side_sh, side_el, side_wr, side_hip = (
        (P_R_SH, P_R_EL, P_R_WR, P_R_HIP)
        if handedness == "right"
        else (P_L_SH, P_L_EL, P_L_WR, P_L_HIP)
    )

    def line_azimuth_proxy(ia: int, ib: int):
        # apparent width of a body line -> rotation about the vertical axis
        width = np.abs(pose_xy[:, ia, 0] - pose_xy[:, ib, 0]).astype(float)
        width = smooth(width, t, 0.10)
        ref = np.nanpercentile(width, 95)
        if not np.isfinite(ref) or ref <= 0:
            return None
        az = np.degrees(np.arccos(np.clip(width / ref, -1, 1)))
        return np.abs(np.gradient(az, t))

    hip = pose_xy[:, side_hip].astype(float)
    sh = pose_xy[:, side_sh].astype(float)
    el = pose_xy[:, side_el].astype(float)
    wr = pose_xy[:, side_wr].astype(float)
    shoulder_ang = smooth(angle3(hip, sh, el), t, 0.10)
    elbow_ang = smooth(angle3(sh, el, wr), t, 0.10)

    chain: Dict[str, object] = {}
    pelvis = line_azimuth_proxy(P_L_HIP, P_R_HIP)
    trunk = line_azimuth_proxy(P_L_SH, P_R_SH)
    if pelvis is not None:
        chain["pelvis"] = pelvis
    if trunk is not None:
        chain["trunk"] = trunk
    chain["upper_arm"] = np.abs(np.gradient(np.where(np.isfinite(shoulder_ang), shoulder_ang, np.nanmean(shoulder_ang)), t))
    chain["forearm"] = np.abs(np.gradient(np.where(np.isfinite(elbow_ang), elbow_ang, np.nanmean(elbow_ang)), t))
    if len(chain) < 3:
        return None

    # search window: acceleration phase plus a small margin (spike-proven).
    # On normal-speed footage the detected acceleration start can drift early
    # (the 2D wrist can rise monotonically from trophy to contact), so also
    # clamp the window to the last ~0.6s before contact — the sequence fires
    # in the final whip, and a wide window picks up toss-phase arm motion.
    w0 = max(0, phases.i_accel - 4, phases.i_contact - int(round(0.6 * fps)))
    w1 = min(n - 1, phases.i_contact + 3)
    if w1 <= w0 + 1:
        return None

    segments = [s for s in ("pelvis", "trunk", "upper_arm", "forearm") if s in chain]
    peak_times_ms: Dict[str, int] = {}
    peak_deg_s: Dict[str, float] = {}
    for name in segments:
        sig = np.asarray(chain[name])
        win = sig[w0: w1 + 1]
        if not np.isfinite(win).any():
            return None
        j = w0 + int(np.nanargmax(win))
        peak_times_ms[name] = int(round(t[j] * 1000.0))
        peak_deg_s[name] = round(float(sig[j]), 1)

    times = [peak_times_ms[s] for s in segments]
    gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    return {
        "segments": segments,
        "peak_times_ms": peak_times_ms,
        "peak_deg_s": peak_deg_s,
        "order_correct": bool(all(g >= 0 for g in gaps)),
        "gaps_ms": [int(g) for g in gaps],
        "note": KINETIC_CHAIN_NOTE,
    }


# ---------------------------------------------------------------------------
# 3D metrics from the SAM 3D Body keyframe stack
# ---------------------------------------------------------------------------


def metrics_from_recon_stack(
    kp3_stack,                 # np (K, 70, 3) camera coords, y down, meters
    contact_stack_index: int,  # index of the contact keyframe within the stack
    joint_index: Dict[str, int],
    handedness: str,
) -> dict:
    """shoulder_angle_deg / knee_flexion_deg / contact_height from keyframes.

    Formulas (METRICS.md §2/§3/§7, phase-2):
      shoulder  = angle at shoulder between torso (shoulder->hip) and upper
                  arm (shoulder->elbow), serving side, at contact.
      knee      = minimum hip-knee-ankle angle over the pre-contact keyframes
                  (deepest loading bend), deeper side reported.
      contact_height = (wrist height above ankles at contact)
                       / (standing nose-to-ankle extent * 1.06 head offset),
                  heights taken along -y (camera up).
    """
    import numpy as np

    side = handedness
    j = joint_index
    kp3 = med3(np.asarray(kp3_stack, dtype=float))
    ci = contact_stack_index
    out: dict = {}

    # ---- shoulder angle at contact ----
    sh = kp3[ci, j[f"{side}_shoulder"]]
    hip = kp3[ci, j[f"{side}_hip"]]
    el = kp3[ci, j[f"{side}_elbow"]]
    val = float(angle3(hip[None], sh[None], el[None])[0])
    out["shoulder_angle_deg"] = {
        "value": round(val, 1),
        "unit": "degree",
        "side": side,
        "band": "low" if val < 90.0 else ("good" if val <= 140.0 else "high"),
        "confidence": 1.0,  # SAM3D exposes no per-keypoint confidence
        "reference_range_deg": (90.0, 140.0),
    }

    # ---- knee flexion: deepest bend across pre-contact keyframes ----
    pre = slice(0, ci + 1)
    knees = {}
    for s in ("left", "right"):
        ang = angle3(kp3[pre, j[f"{s}_hip"]], kp3[pre, j[f"{s}_knee"]], kp3[pre, j[f"{s}_ankle"]])
        knees[s] = float(np.min(ang))
    deep_side = min(knees, key=knees.get)
    kval = knees[deep_side]
    out["knee_flexion_deg"] = {
        "value": round(kval, 1),
        "unit": "degree",
        "side": deep_side,
        "band": "deep" if kval < 115.0 else ("moderate" if kval <= 145.0 else "shallow"),
        "confidence": 1.0,
    }

    # ---- contact height ratio ----
    up = -kp3[..., 1]  # camera y is down
    ank = 0.5 * (up[:, j["left_ankle"]] + up[:, j["right_ankle"]])
    wrist_m = float(up[ci, j[f"{side}_wrist"]] - ank[ci])
    # standing reference: the keyframe with the tallest nose-over-ankle extent
    extent = up[:, j["nose"]] - ank
    k_up = int(np.argmax(extent))
    standing_m = float(extent[k_up]) * 1.06  # nose -> top-of-head offset
    if standing_m > 0.5 and wrist_m > 0:
        out["contact_height"] = {
            "value": round(wrist_m / standing_m, 3),
            "unit": "ratio",
            "wrist_y_m": round(wrist_m, 3),
            "standing_height_m": round(standing_m, 3),
        }
    return out


def phase_timing_block(phases: Phases, fps: float, window_t0_ms: int) -> dict:
    """`phase_timing` metric object — durations in ms + absolute contact ms."""
    to_ms = lambda i: int(round(i / fps * 1000.0))
    return {
        "unit": "ms",
        "contact_ms": window_t0_ms + to_ms(phases.i_contact),
        "phases": {
            "windup": to_ms(phases.i_trophy - phases.i_start),
            "trophy": to_ms(phases.i_accel - phases.i_trophy),
            "acceleration": to_ms(phases.i_contact - phases.i_accel),
            "follow_through": to_ms(phases.i_end - phases.i_contact),
        },
    }
