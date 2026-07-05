"""Sanity tests for the placeholder skeleton map and GLB generator."""

import json
import struct

from servebot.glb import GLB_VERTEX_COUNT, placeholder_glb
from servebot.metrics import angle_at_joint
from servebot.skeleton import (
    JOINT_INDEX,
    SAM3D_BODY_70_JOINTS,
    mirror_name,
    stub_contact_pose,
)


class TestSkeleton:
    def test_seventy_unique_names(self):
        assert len(SAM3D_BODY_70_JOINTS) == 70
        assert len(set(SAM3D_BODY_70_JOINTS)) == 70

    def test_confirmed_mhr70_arm_indices(self):
        # CONFIRMED against facebook/sam-3d-body-dinov3 (MHR70). Note the wrists
        # are at the end of the hand chains, not adjacent to the elbows.
        assert JOINT_INDEX["left_shoulder"] == 5
        assert JOINT_INDEX["right_shoulder"] == 6
        assert JOINT_INDEX["left_elbow"] == 7
        assert JOINT_INDEX["right_elbow"] == 8
        assert JOINT_INDEX["right_wrist"] == 41
        assert JOINT_INDEX["left_wrist"] == 62

    def test_six_arm_joints_resolve(self):
        for name in (
            "left_shoulder", "right_shoulder",
            "left_elbow", "right_elbow",
            "left_wrist", "right_wrist",
        ):
            assert name in JOINT_INDEX

    def test_pose_covers_all_joints(self):
        for handedness in ("right", "left"):
            pose = stub_contact_pose(handedness)
            assert set(pose) == set(SAM3D_BODY_70_JOINTS)

    def test_left_handed_pose_is_mirrored_and_angle_invariant(self):
        right = stub_contact_pose("right")
        left = stub_contact_pose("left")
        for name, (xyz, score) in right.items():
            m_xyz, m_score = left[mirror_name(name)]
            assert m_xyz == (-xyz[0], xyz[1], xyz[2])
            assert m_score == score

        def serving_angle(pose, side):
            return angle_at_joint(
                pose[f"{side}_shoulder"][0], pose[f"{side}_elbow"][0], pose[f"{side}_wrist"][0]
            )

        assert serving_angle(right, "right") == serving_angle(left, "left")


class TestPlaceholderGlb:
    def test_valid_glb_container(self):
        blob = placeholder_glb()
        magic, version, length = struct.unpack_from("<4sII", blob, 0)
        assert magic == b"glTF"
        assert version == 2
        assert length == len(blob)

        json_len, json_tag = struct.unpack_from("<I4s", blob, 12)
        assert json_tag == b"JSON"
        gltf = json.loads(blob[20 : 20 + json_len])
        assert gltf["asset"]["version"] == "2.0"
        assert gltf["accessors"][0]["count"] == GLB_VERTEX_COUNT

        bin_off = 20 + json_len
        bin_len, bin_tag = struct.unpack_from("<I4s", blob, bin_off)
        assert bin_tag == b"BIN\x00"
        assert bin_off + 8 + bin_len == len(blob)
        assert gltf["buffers"][0]["byteLength"] == bin_len
