"""The SAM 3D Body 70-keypoint skeleton map + the stub contact pose.

!! PLACEHOLDER MAP — MODELS.md §4.4 hard gate !!
The authoritative index->name mapping is defined by the `sam-3d-body-dinov3`
checkpoint and MUST be read from the model's own skeleton definition when the
real pipeline lands (Milestone Step 3). This placeholder exists so the stub
backend can emit a well-formed 70-point payload; the six arm joints are placed
at the indices used in the API_CONTRACT.md §4c example (right_shoulder=12,
right_elbow=14, right_wrist=16). Do not ship real reconstructions until this
map is confirmed against the checkpoint.
"""

from __future__ import annotations

from typing import Dict, Tuple

Vec3 = Tuple[float, float, float]
PosedPoint = Tuple[Vec3, float]  # (xyz meters, score)

_FINGERS = ("thumb", "index", "middle", "ring", "pinky")

SAM3D_BODY_70_JOINTS: tuple[str, ...] = (
    # 0-6: root + spine + head chain
    "pelvis", "spine_1", "spine_2", "spine_3", "neck", "head", "nose",
    # 7-10: face
    "left_eye", "right_eye", "left_ear", "right_ear",
    # 11-16: arms — indices match API_CONTRACT.md §4c example
    "left_shoulder", "right_shoulder",
    "left_elbow", "right_elbow",
    "left_wrist", "right_wrist",
    # 17-22: legs
    "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle",
    # 23-30: feet
    "left_heel", "right_heel", "left_foot_index", "right_foot_index",
    "left_big_toe", "right_big_toe", "left_small_toe", "right_small_toe",
    # 31-60: hands (5 fingers x 3 segments x 2 sides)
    *(f"left_{f}_{s}" for f in _FINGERS for s in (1, 2, 3)),
    *(f"right_{f}_{s}" for f in _FINGERS for s in (1, 2, 3)),
    # 61-69: extras to reach the checkpoint's 70 [PLACEHOLDER names]
    "jaw", "head_top", "chest",
    "left_clavicle", "right_clavicle",
    "left_eye_outer", "right_eye_outer",
    "left_palm", "right_palm",
)

assert len(SAM3D_BODY_70_JOINTS) == 70
assert len(set(SAM3D_BODY_70_JOINTS)) == 70

JOINT_INDEX: Dict[str, int] = {name: i for i, name in enumerate(SAM3D_BODY_70_JOINTS)}


def mirror_name(name: str) -> str:
    if name.startswith("left_"):
        return "right_" + name[len("left_"):]
    if name.startswith("right_"):
        return "left_" + name[len("right_"):]
    return name


def _right_handed_contact_pose() -> Dict[str, PosedPoint]:
    """A geometrically consistent right-handed server at contact.

    The serving-arm joints are the exact coordinates of the METRICS.md §1
    worked example, so the stub's elbow angle is a *real* computation over
    these points (an extended arm reaching up-and-forward). The rest of the
    body is a plausible, deterministic standing-reach pose.
    """
    pose: Dict[str, PosedPoint] = {
        # Serving (right) arm — METRICS.md §1 worked example, scores from
        # the API_CONTRACT.md §4c example.
        "right_shoulder": ((0.182, 1.402, 0.031), 0.94),
        "right_elbow": ((0.372, 1.560, -0.010), 0.90),
        "right_wrist": ((0.540, 1.712, -0.048), 0.86),
        # Non-serving (toss) arm — bent and dropping after release.
        "left_shoulder": ((-0.155, 1.385, 0.024), 0.93),
        "left_elbow": ((-0.290, 1.150, 0.105), 0.88),
        "left_wrist": ((-0.215, 0.940, 0.240), 0.82),
        # Trunk / head.
        "pelvis": ((0.000, 0.960, 0.000), 0.97),
        "spine_1": ((0.005, 1.070, 0.005), 0.95),
        "spine_2": ((0.010, 1.180, 0.010), 0.95),
        "spine_3": ((0.015, 1.290, 0.012), 0.94),
        "chest": ((0.015, 1.330, 0.015), 0.92),
        "neck": ((0.020, 1.420, 0.015), 0.93),
        "head": ((0.030, 1.560, 0.030), 0.92),
        "head_top": ((0.035, 1.650, 0.030), 0.85),
        "jaw": ((0.032, 1.520, 0.070), 0.80),
        "nose": ((0.035, 1.575, 0.095), 0.90),
        "left_eye": ((0.005, 1.600, 0.085), 0.88),
        "right_eye": ((0.062, 1.600, 0.082), 0.88),
        "left_eye_outer": ((-0.012, 1.600, 0.075), 0.78),
        "right_eye_outer": ((0.080, 1.600, 0.072), 0.78),
        "left_ear": ((-0.030, 1.585, 0.020), 0.84),
        "right_ear": ((0.095, 1.585, 0.018), 0.84),
        "left_clavicle": ((-0.070, 1.400, 0.020), 0.86),
        "right_clavicle": ((0.095, 1.405, 0.024), 0.86),
        # Legs — driving upward, mostly extended.
        "left_hip": ((-0.095, 0.940, 0.005), 0.95),
        "right_hip": ((0.095, 0.945, -0.005), 0.95),
        "left_knee": ((-0.110, 0.520, 0.060), 0.92),
        "right_knee": ((0.115, 0.525, 0.020), 0.92),
        "left_ankle": ((-0.120, 0.090, 0.040), 0.90),
        "right_ankle": ((0.125, 0.110, -0.030), 0.90),
        "left_heel": ((-0.125, 0.055, -0.010), 0.82),
        "right_heel": ((0.130, 0.075, -0.080), 0.82),
        "left_foot_index": ((-0.115, 0.030, 0.130), 0.80),
        "right_foot_index": ((0.130, 0.050, 0.060), 0.80),
        "left_big_toe": ((-0.105, 0.020, 0.155), 0.72),
        "right_big_toe": ((0.128, 0.040, 0.085), 0.72),
        "left_small_toe": ((-0.145, 0.025, 0.130), 0.68),
        "right_small_toe": ((0.165, 0.045, 0.060), 0.68),
        # Palms.
        "left_palm": ((-0.205, 0.905, 0.265), 0.70),
        "right_palm": ((0.565, 1.735, -0.055), 0.70),
    }
    # Hand joints: short deterministic chains extending from each wrist.
    for side, sign in (("left", -1.0), ("right", 1.0)):
        wx, wy, wz = pose[f"{side}_wrist"][0]
        for f_i, finger in enumerate(_FINGERS):
            for seg in (1, 2, 3):
                pose[f"{side}_{finger}_{seg}"] = (
                    (
                        round(wx + sign * 0.022 * seg, 4),
                        round(wy + 0.012 * seg - 0.008 * f_i, 4),
                        round(wz - 0.006 * f_i, 4),
                    ),
                    0.55,
                )
    assert set(pose) == set(SAM3D_BODY_70_JOINTS)
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
