"""Sam3DAnalysisPipeline tests (Milestone Step 4).

Two tiers:
  * Cheap tests that always run — config wiring, torch-free import, loud
    failure on a bad pipeline name.
  * A full end-to-end API test against the REAL model that SKIPS unless the
    environment provides torch (MPS), the MPS-patched sam-3d-body repo
    (SERVEBOT_SAM3D_REPO) and the local checkpoint dir
    (SERVEBOT_SAM3D_CHECKPOINT_DIR) — CI without a GPU stays green.
"""

from __future__ import annotations

import os
import sys

import pytest

from servebot.config import Settings
from servebot.main import create_app

from .conftest import AUTH, make_settings

SAM3D_REPO = os.environ.get("SERVEBOT_SAM3D_REPO", "")
SAM3D_CKPT = os.environ.get("SERVEBOT_SAM3D_CHECKPOINT_DIR", "")


def _sam3d_available() -> bool:
    if not (
        SAM3D_REPO
        and SAM3D_CKPT
        and os.path.isdir(os.path.join(SAM3D_REPO, "sam_3d_body"))
        and os.path.isfile(os.path.join(SAM3D_CKPT, "model.ckpt"))
    ):
        return False
    try:
        import torch  # noqa: F401
    except ImportError:
        return False
    import torch

    return torch.backends.mps.is_available()


requires_sam3d = pytest.mark.skipif(
    not _sam3d_available(),
    reason="needs torch+MPS, SERVEBOT_SAM3D_REPO and SERVEBOT_SAM3D_CHECKPOINT_DIR",
)


# ---------------------------------------------------------- always-run tests


def test_default_pipeline_is_stub():
    assert Settings().pipeline == "stub"


def test_pipeline_env_selection(monkeypatch):
    monkeypatch.setenv("SERVEBOT_PIPELINE", "sam3d")
    monkeypatch.setenv("SERVEBOT_SAM3D_CHECKPOINT_DIR", "/x/ckpt")
    monkeypatch.setenv("SERVEBOT_SAM3D_REPO", "/x/repo")
    monkeypatch.setenv("SERVEBOT_DEVICE", "mps")
    s = Settings.from_env()
    assert s.pipeline == "sam3d"
    assert s.sam3d_checkpoint_dir == "/x/ckpt"
    assert s.sam3d_repo == "/x/repo"
    assert s.device == "mps"


def test_unknown_pipeline_fails_loudly(tmp_path):
    with pytest.raises(RuntimeError, match="SERVEBOT_PIPELINE"):
        create_app(make_settings(tmp_path, pipeline="definitely-not-a-pipeline"))


def test_sam3d_module_import_is_torch_free():
    """Importing the module must not pull in torch (base app stays light)."""
    had_torch = "torch" in sys.modules
    import servebot.pipeline_sam3d  # noqa: F401

    if not had_torch:
        assert "torch" not in sys.modules


def test_sam3d_with_bad_paths_fails_loudly(tmp_path):
    """Selecting sam3d without a valid repo/checkpoint must refuse to start."""
    with pytest.raises(RuntimeError, match="SERVEBOT_SAM3D"):
        create_app(
            make_settings(
                tmp_path,
                pipeline="sam3d",
                sam3d_repo=str(tmp_path / "nope"),
                sam3d_checkpoint_dir=str(tmp_path / "nope-ckpt"),
            )
        )


# ------------------------------------------------- real-model e2e (skippable)


def _make_test_clip(tmp_path) -> tuple[bytes, dict]:
    """~2s mp4 built by repeating the spike's known-good person frame."""
    import cv2

    img_path = os.path.join(SAM3D_REPO, "notebook", "images", "dancing.jpg")
    img = cv2.imread(img_path)
    assert img is not None, f"missing test image: {img_path}"
    h, w = img.shape[:2]
    fps, seconds = 30, 2
    clip_path = str(tmp_path / "serve.mp4")
    vw = cv2.VideoWriter(clip_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    assert vw.isOpened()
    for _ in range(fps * seconds):
        vw.write(img)
    vw.release()
    clip_bytes = open(clip_path, "rb").read()
    meta = {
        "content_type": "video/mp4",
        "byte_size": len(clip_bytes),
        "duration_ms": seconds * 1000,
        "fps": fps,
        "width": w,
        "height": h,
    }
    return clip_bytes, meta


@requires_sam3d
def test_sam3d_end_to_end_real_model(tmp_path):
    """Full API_CONTRACT §6 flow against the real SAM 3D Body on MPS."""
    import io
    import time

    import trimesh
    from fastapi.testclient import TestClient

    from servebot.skeleton import SAM3D_BODY_70_JOINTS

    settings = make_settings(
        tmp_path,
        pipeline="sam3d",
        sam3d_repo=SAM3D_REPO,
        sam3d_checkpoint_dir=SAM3D_CKPT,
        device="mps",
    )
    app = create_app(settings)  # loads the model (singleton; ~20s cold)
    with TestClient(app) as client:
        # health reports the real MPS device + real sam3d checkpoint
        health = client.get("/v1/health", headers=AUTH).json()
        assert health["models_ready"] is True
        assert health["models"]["sam3d_body"]["loaded"] is True
        assert "MPS" in health["gpu"]["name"]

        clip_bytes, meta = _make_test_clip(tmp_path)
        r = client.post("/v1/uploads", json=meta, headers=AUTH)
        assert r.status_code == 200, r.text
        up = r.json()
        pr = client.put(
            up["upload_url"], content=clip_bytes, headers={"Content-Type": "video/mp4"}
        )
        assert pr.status_code == 200, pr.text

        body = {
            "object_key": up["object_key"],
            "handedness": "right",
            "contact_timestamp_ms": 1000,
            "clip": {
                k: meta[k]
                for k in ("duration_ms", "fps", "width", "height", "content_type")
            },
        }
        r = client.post("/v1/serves", json=body, headers=AUTH)
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]

        deadline = time.time() + 300  # cold MPS shader compile can be slow
        payload = None
        while time.time() < deadline:
            payload = client.get(f"/v1/serves/{job_id}", headers=AUTH).json()
            if payload["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.5)
        assert payload and payload["status"] == "succeeded", payload

        result = payload["result"]
        # real 70 keypoints, correct MHR70 names in order
        points = result["keyframes"][0]["keypoints_3d"]["points"]
        assert len(points) == 70
        assert [p["name"] for p in points] == list(SAM3D_BODY_70_JOINTS)
        # real elbow angle, plausible range
        elbow = result["metrics"]["elbow_angle_deg"]
        assert elbow["value"] is not None
        assert 0.0 < elbow["value"] < 180.0
        assert elbow["side"] == "right"
        # diagnostics are truthful
        diag = result["diagnostics"]
        assert diag["frames_decoded"] == 60
        assert diag["model_versions"]["sam3d_body"] == "sam-3d-body-dinov3"
        assert diag["model_versions"]["sam3"] is None
        assert diag["timings_ms"]["reconstruct"] > 100  # real inference happened
        # the GLB is a real binary glTF with a human-scale mesh
        mesh_info = result["keyframes"][0]["mesh"]
        glb = client.get(mesh_info["glb_url"])
        assert glb.status_code == 200
        assert glb.content[:4] == b"glTF"
        scene = trimesh.load(io.BytesIO(glb.content), file_type="glb")
        n_verts = sum(len(g.vertices) for g in scene.geometry.values())
        assert n_verts == mesh_info["vertex_count"]
        assert n_verts > 10000  # full SAM 3D Body mesh, not a placeholder
