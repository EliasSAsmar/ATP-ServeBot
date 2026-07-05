"""The SAM 3D Body 70-keypoint skeleton map + the stub contact pose.

MHR70 map CONFIRMED against the real `facebook/sam-3d-body-dinov3` checkpoint
(Milestone Step 3 hard gate — MODELS.md §4.4). Source of truth:
`sam_3d_body/metadata/mhr70.py` in facebookresearch/sam-3d-body — the first 70
of the 308 Momentum Human Rig (MHR) keypoints. Index order below matches
`original_keypoint_info` exactly, so a real SAM 3D Body output array (shape
(70, 3), MHR70 order) maps position i -> SAM3D_BODY_70_JOINTS[i] correctly.

IMPORTANT — arm-joint indices are NOT COCO-adjacent. The wrists sit at the end
of the per-hand keypoint chains, far from the elbows:
    left_shoulder=5   right_shoulder=6
    left_elbow=7      right_elbow=8
    right_wrist=41    left_wrist=62      <- note: not next to the elbows
The elbow-angle metric selects joints BY NAME (metrics.py), so it is robust to
this ordering; the ordering here only governs the truthful `index` field and
the mapping of real model output arrays to names.
"""

from __future__ import annotations

from typing import Dict, Tuple

Vec3 = Tuple[float, float, float]
PosedPoint = Tuple[Vec3, float]  # (xyz meters, score)

_FINGERS = ("thumb", "index", "middle", "ring", "pinky")
_SEGS = ("tip", "first_joint", "second_joint", "third_joint")


def _hand(side: str) -> tuple[str, ...]:
    """The 20 hand keypoints for a side, in MHR70 order (5 fingers x 4 points)."""
    return tuple(f"{side}_{finger}_{seg}" for finger in _FINGERS for seg in _SEGS)


# Confirmed MHR70 index -> name (see module docstring).
SAM3D_BODY_70_JOINTS: tuple[str, ...] = (
    # 0-4: head
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    # 5-8: upper arms
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    # 9-14: legs
    "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle",
    # 15-20: feet
    "left_big_toe_tip", "left_small_toe_tip", "left_heel",
    "right_big_toe_tip", "right_small_toe_tip", "right_heel",
    # 21-40: right hand, then 41: right wrist
    *_hand("right"), "right_wrist",
    # 42-61: left hand, then 62: left wrist
    *_hand("left"), "left_wrist",
    # 63-69: extra bony landmarks + neck
    "left_olecranon", "right_olecranon",
    "left_cubital_fossa", "right_cubital_fossa",
    "left_acromion", "right_acromion",
    "neck",
)

assert len(SAM3D_BODY_70_JOINTS) == 70, len(SAM3D_BODY_70_JOINTS)
assert len(set(SAM3D_BODY_70_JOINTS)) == 70
# Spot-check the arm joints the elbow metric depends on.
assert SAM3D_BODY_70_JOINTS.index("right_shoulder") == 6
assert SAM3D_BODY_70_JOINTS.index("right_elbow") == 8
assert SAM3D_BODY_70_JOINTS.index("right_wrist") == 41
assert SAM3D_BODY_70_JOINTS.index("left_wrist") == 62

JOINT_INDEX: Dict[str, int] = {name: i for i, name in enumerate(SAM3D_BODY_70_JOINTS)}


def mirror_name(name: str) -> str:
    if name.startswith("left_"):
        return "right_" + name[len("left_"):]
    if name.startswith("right_"):
        return "left_" + name[len("right_"):]
    return name


def _right_handed_contact_pose() -> Dict[str, PosedPoint]:
    """A geometrically consistent right-handed server at contact (MHR70 names).

    The serving-arm joints are the exact coordinates of the METRICS.md §1
    worked example, so the stub's elbow angle is a *real* computation over
    these points (~177.6 deg, an extended arm reaching up-and-forward). The
    rest of the body is a plausible, deterministic standing-reach pose.
    """
    pose: Dict[str, PosedPoint] = {
        # Serving (right) arm — METRICS.md §1 worked example.
        "right_shoulder": ((0.182, 1.402, 0.031), 0.94),
        "right_elbow": ((0.372, 1.560, -0.010), 0.90),
        "right_wrist": ((0.540, 1.712, -0.048), 0.86),
        # Non-serving (toss) arm — bent and dropping after release.
        "left_shoulder": ((-0.155, 1.385, 0.024), 0.93),
        "left_elbow": ((-0.290, 1.150, 0.105), 0.88),
        "left_wrist": ((-0.215, 0.940, 0.240), 0.82),
        # Head.
        "nose": ((0.035, 1.575, 0.095), 0.90),
        "left_eye": ((0.005, 1.600, 0.085), 0.88),
        "right_eye": ((0.062, 1.600, 0.082), 0.88),
        "left_ear": ((-0.030, 1.585, 0.020), 0.84),
        "right_ear": ((0.095, 1.585, 0.018), 0.84),
        "neck": ((0.020, 1.420, 0.015), 0.93),
        # Legs — driving upward, mostly extended.
        "left_hip": ((-0.095, 0.940, 0.005), 0.95),
        "right_hip": ((0.095, 0.945, -0.005), 0.95),
        "left_knee": ((-0.110, 0.520, 0.060), 0.92),
        "right_knee": ((0.115, 0.525, 0.020), 0.92),
        "left_ankle": ((-0.120, 0.090, 0.040), 0.90),
        "right_ankle": ((0.125, 0.110, -0.030), 0.90),
        # Feet.
        "left_heel": ((-0.125, 0.055, -0.010), 0.82),
        "right_heel": ((0.130, 0.075, -0.080), 0.82),
        "left_big_toe_tip": ((-0.105, 0.020, 0.155), 0.72),
        "right_big_toe_tip": ((0.128, 0.040, 0.085), 0.72),
        "left_small_toe_tip": ((-0.145, 0.025, 0.130), 0.68),
        "right_small_toe_tip": ((0.165, 0.045, 0.060), 0.68),
        # Extra bony landmarks near the elbows/shoulders.
        "right_olecranon": ((0.365, 1.548, -0.035), 0.60),
        "left_olecranon": ((-0.300, 1.140, 0.080), 0.60),
        "right_cubital_fossa": ((0.380, 1.572, 0.015), 0.60),
        "left_cubital_fossa": ((-0.280, 1.160, 0.130), 0.60),
        "right_acromion": ((0.192, 1.427, 0.028), 0.70),
        "left_acromion": ((-0.165, 1.410, 0.020), 0.70),
    }
    # Hand joints: short deterministic chains extending from each wrist.
    for side, sign in (("left", -1.0), ("right", 1.0)):
        wx, wy, wz = pose[f"{side}_wrist"][0]
        for f_i, finger in enumerate(_FINGERS):
            for s_i, seg in enumerate(_SEGS, start=1):
                pose[f"{side}_{finger}_{seg}"] = (
                    (
                        round(wx + sign * 0.022 * s_i, 4),
                        round(wy + 0.012 * s_i - 0.008 * f_i, 4),
                        round(wz - 0.006 * f_i, 4),
                    ),
                    0.55,
                )
    assert set(pose) == set(SAM3D_BODY_70_JOINTS), (
        set(SAM3D_BODY_70_JOINTS) - set(pose),
        set(pose) - set(SAM3D_BODY_70_JOINTS),
    )
    return pose


_RIGHT_POSE = _right_handed_contact_pose()


def stub_contact_pose(handedness: str) -> Dict[str, PosedPoint]:
    """Full 70-joint pose for the stub reconstruction.

    For left-handers the pose is mirrored across the YZ plane (x -> -x) and
    side labels are swapped, so the serving-side arm geometry (and therefore
    the elbow angle) is identical for both handednesses.
    """
    if handedness == "right":
        return dict(_RIGHT_POSE)
    return {
        mirror_name(name): ((-xyz[0], xyz[1], xyz[2]), score)
        for name, (xyz, score) in _RIGHT_POSE.items()
    }
