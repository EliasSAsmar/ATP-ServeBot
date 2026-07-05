"""API contract tests — the full client sequence (API_CONTRACT.md §6) plus
the auth, error-envelope, and job-lifecycle acceptance criteria of
MILESTONE_V1.md Steps 2 and 5, running against local storage + stub pipeline.
"""

import re
import time
import uuid

from fastapi.testclient import TestClient

from servebot.main import create_app

from .conftest import AUTH, create_serve, make_settings, poll_until_terminal, upload_clip

ISO_MS_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def assert_error_envelope(response, status, code, field=None):
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    err = body["error"]
    assert err["code"] == code
    assert err["field"] == field
    assert err["request_id"] == response.headers["X-Request-Id"]
    assert err["message"]


class TestAuth:
    def test_missing_key_401(self, client):
        assert_error_envelope(client.get("/v1/health"), 401, "unauthorized")

    def test_wrong_key_401(self, client):
        r = client.get("/v1/health", headers={"X-API-Key": "nope"})
        assert_error_envelope(r, 401, "unauthorized")

    def test_request_id_echoed(self, client):
        rid = str(uuid.uuid4())
        r = client.get("/v1/health", headers={**AUTH, "X-Request-Id": rid})
        assert r.headers["X-Request-Id"] == rid


class TestHealth:
    def test_health_shape(self, client):
        r = client.get("/v1/health", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["instance_up"] is True
        assert body["models_ready"] is True
        assert body["models"]["sam3"]["loaded"] is True
        assert body["models"]["sam3d_body"]["loaded"] is True
        assert body["queue_depth"] == 0
        assert body["api_version"] == "v1"
        assert ISO_MS_Z.match(body["server_time"])


class TestUploads:
    def test_mint_upload(self, client):
        r = client.post(
            "/v1/uploads",
            json={
                "content_type": "video/webm",
                "byte_size": 1843200,
                "duration_ms": 3200,
                "fps": 30,
                "width": 1280,
                "height": 720,
            },
            headers=AUTH,
        )
        assert r.status_code == 200
        body = r.json()
        assert re.match(r"^clips/\d{4}/\d{2}/\d{2}/[0-9a-f-]{36}\.webm$", body["object_key"])
        assert body["upload_method"] == "PUT"
        assert body["upload_headers"] == {"Content-Type": "video/webm"}
        assert ISO_MS_Z.match(body["expires_at"])
        assert "token=" in body["upload_url"]

    def test_oversized_413(self, client):
        r = client.post(
            "/v1/uploads",
            json={
                "content_type": "video/webm",
                "byte_size": 25 * 1024 * 1024 + 1,
                "duration_ms": 3200,
                "fps": 30,
                "width": 1280,
                "height": 720,
            },
            headers=AUTH,
        )
        assert_error_envelope(r, 413, "clip_too_large", field="byte_size")

    def test_bad_content_type(self, client):
        r = client.post(
            "/v1/uploads",
            json={
                "content_type": "video/avi",
                "byte_size": 1000,
                "duration_ms": 3200,
                "fps": 30,
                "width": 1280,
                "height": 720,
            },
            headers=AUTH,
        )
        assert_error_envelope(r, 400, "invalid_request", field="content_type")

    def test_malformed_json(self, client):
        r = client.post(
            "/v1/uploads",
            content=b"{not json",
            headers={**AUTH, "Content-Type": "application/json"},
        )
        assert_error_envelope(r, 400, "invalid_request")

    def test_put_with_bad_token_403(self, client):
        object_key, _ = upload_clip(client)  # mints + uploads happily
        r = client.put(
            f"/local-s3/{object_key}?expires=9999999999&token=deadbeef",
            content=b"x",
            headers={"Content-Type": "video/webm"},
        )
        assert r.status_code == 403


class TestCreateServeValidation:
    def test_invalid_handedness(self, client):
        object_key, meta = upload_clip(client)
        r = create_serve(client, object_key, meta, handedness="ambidextrous")
        assert_error_envelope(r, 400, "invalid_handedness", field="handedness")

    def test_invalid_object_key(self, client):
        _, meta = upload_clip(client)
        r = create_serve(client, "clips/2026/07/04/not-minted.webm", meta)
        assert_error_envelope(r, 400, "invalid_object_key", field="object_key")

    def test_invalid_timestamp(self, client):
        object_key, meta = upload_clip(client)
        r = create_serve(client, object_key, meta, contact_ms=meta["duration_ms"] + 1)
        assert_error_envelope(r, 400, "invalid_timestamp", field="contact_timestamp_ms")
        r = create_serve(client, object_key, meta, contact_ms=-1)
        assert_error_envelope(r, 400, "invalid_timestamp", field="contact_timestamp_ms")

    def test_clip_not_found_409(self, client):
        # Minted but never PUT.
        r = client.post(
            "/v1/uploads",
            json={
                "content_type": "video/webm",
                "byte_size": 1000,
                "duration_ms": 3200,
                "fps": 30,
                "width": 1280,
                "height": 720,
            },
            headers=AUTH,
        )
        object_key = r.json()["object_key"]
        meta = {"duration_ms": 3200, "fps": 30, "width": 1280, "height": 720, "content_type": "video/webm"}
        resp = create_serve(client, object_key, meta)
        assert_error_envelope(resp, 409, "clip_not_found", field="object_key")

    def test_job_not_found_404(self, client):
        r = client.get(f"/v1/serves/{uuid.uuid4()}", headers=AUTH)
        assert_error_envelope(r, 404, "job_not_found")


class TestEndToEnd:
    """The full §6 client sequence against local storage + stub pipeline."""

    def test_full_sequence_succeeds(self, client):
        # 1. health
        assert client.get("/v1/health", headers=AUTH).json()["models_ready"] is True

        # 2-3. mint + PUT clip
        object_key, meta = upload_clip(client)

        # 4. create job
        r = create_serve(
            client,
            object_key,
            meta,
            edge_detect={"detector_version": "serve-heuristic-1", "contact_confidence": 0.71},
            client={"app_version": "0.1.0", "platform": "web", "user_label": "practice-serve-1"},
        )
        assert r.status_code == 202, r.text
        created = r.json()
        assert created["status"] == "queued"
        assert created["poll_url"] == f"/v1/serves/{created['job_id']}"
        assert created["poll_after_ms"] > 0
        assert ISO_MS_Z.match(created["created_at"])

        # 5. poll to terminal
        payload = poll_until_terminal(client, created["job_id"])
        assert payload["status"] == "succeeded"
        assert payload["stage"] is None
        assert payload["progress"] == 1.0
        assert payload["poll_after_ms"] is None
        assert payload["error"] is None
        assert ISO_MS_Z.match(payload["started_at"])
        assert ISO_MS_Z.match(payload["finished_at"])

        result = payload["result"]
        assert result["schema_version"] == "serve-result-1"
        assert result["handedness"] == "right"

        contact = result["contact"]
        assert contact["edge_timestamp_ms"] == 1840
        assert contact["refined_timestamp_ms"] == 1840  # stub trusts edge (MODELS §3.2 fallback)
        assert contact["refined_frame_index"] == 55  # round(1840 * 30/1000)
        assert contact["contact_confidence"] == 0.71  # echoed edge confidence
        assert contact["refine_window_ms"] == 200

        # keyframes: exactly one, role=contact, 70 well-formed keypoints
        assert len(result["keyframes"]) == 1
        kf = result["keyframes"][0]
        assert kf["role"] == "contact"
        kps = kf["keypoints_3d"]
        assert kps["format"] == "sam3d-body-70"
        assert kps["count"] == 70
        assert kps["units"] == "meters"
        assert len(kps["points"]) == 70
        by_name = {p["name"]: p for p in kps["points"]}
        assert by_name["right_shoulder"]["index"] == 6  # confirmed MHR70
        assert by_name["right_wrist"]["index"] == 41  # wrist at end of hand chain
        assert by_name["right_shoulder"]["xyz"] == [0.182, 1.402, 0.031]
        assert all(0.0 <= p["score"] <= 1.0 for p in kps["points"])

        # REAL metric computed from the keypoints
        metrics = result["metrics"]
        elbow = metrics["elbow_angle_deg"]
        assert elbow["value"] == 177.6
        assert elbow["band"] == "straight"
        assert elbow["side"] == "right"
        assert elbow["confidence"] == 0.86
        assert elbow["reference_range_deg"] == [150.0, 180.0]
        for stub_key in (
            "shoulder_angle_deg", "knee_flexion_deg", "kinetic_chain_sequence",
            "toss_placement", "toss_consistency", "contact_height", "phase_timing",
        ):
            assert stub_key in metrics and metrics[stub_key] is None

        # matching tip
        assert len(result["tips"]) == 1
        tip = result["tips"][0]
        assert tip["id"] == "elbow_good_extension"
        assert tip["metric"] == "elbow_angle_deg"
        assert "~178°" in tip["message"]
        assert tip["triggered_by"] == {"value": 177.6, "threshold": 150.0, "comparator": "gte"}

        # diagnostics
        diag = result["diagnostics"]
        assert diag["model_versions"]["metric_engine"] == "metrics-1"
        assert diag["model_versions"]["tip_engine"] == "tips-1"
        assert set(diag["timings_ms"]) == {
            "download", "decode", "segment", "keyframe", "reconstruct",
            "filter", "metrics", "tips", "upload_mesh",
        }
        assert diag["frames_decoded"] == 96  # 3200ms * 30fps

        # 6. fetch the GLB from the presigned URL — a valid binary glTF
        mesh = kf["mesh"]
        assert mesh["up_axis"] == "Y" and mesh["units"] == "meters"
        glb = client.get(mesh["glb_url"])
        assert glb.status_code == 200
        assert glb.content[:4] == b"glTF"
        assert glb.headers["content-type"] == "model/gltf-binary"

        # re-polling refreshes the presigned mesh URL (expiry refresh story)
        again = client.get(f"/v1/serves/{created['job_id']}", headers=AUTH).json()
        assert again["result"]["keyframes"][0]["mesh"]["glb_url"]

        # job listing (§5)
        listing = client.get("/v1/serves", headers=AUTH).json()
        assert listing["count"] == 1
        assert listing["jobs"][0]["job_id"] == created["job_id"]
        assert listing["jobs"][0]["status"] == "succeeded"
        assert listing["jobs"][0]["user_label"] == "practice-serve-1"

    def test_left_handed_serve(self, client):
        object_key, meta = upload_clip(client)
        r = create_serve(client, object_key, meta, handedness="left")
        payload = poll_until_terminal(client, r.json()["job_id"])
        assert payload["status"] == "succeeded"
        elbow = payload["result"]["metrics"]["elbow_angle_deg"]
        assert elbow["side"] == "left"
        assert elbow["joints"] == ["left_shoulder", "left_elbow", "left_wrist"]
        assert elbow["value"] == 177.6  # mirrored pose, identical angle

    def test_empty_clip_fails_unprocessable(self, client):
        object_key, meta = upload_clip(client, content=b"")
        r = create_serve(client, object_key, meta)
        payload = poll_until_terminal(client, r.json()["job_id"])
        assert payload["status"] == "failed"
        assert payload["result"] is None
        assert payload["progress"] == 1.0
        err = payload["error"]
        assert err["code"] == "unprocessable_clip"
        assert err["stage"] == "decoding"
        assert err["retriable"] is False

    def test_status_filter_and_bad_status(self, client):
        object_key, meta = upload_clip(client)
        r = create_serve(client, object_key, meta)
        poll_until_terminal(client, r.json()["job_id"])
        ok = client.get("/v1/serves", params={"status": "succeeded"}, headers=AUTH)
        assert ok.json()["count"] == 1
        empty = client.get("/v1/serves", params={"status": "failed"}, headers=AUTH)
        assert empty.json()["count"] == 0
        bad = client.get("/v1/serves", params={"status": "done"}, headers=AUTH)
        assert_error_envelope(bad, 400, "invalid_request", field="status")


class TestBusyQueue:
    def test_queue_cap_429_with_retry_after(self, tmp_path):
        settings = make_settings(tmp_path, stub_stage_delay_s=0.2, queue_max_depth=1)
        app = create_app(settings)
        with TestClient(app) as client:
            object_key, meta = upload_clip(client)
            statuses = []
            busy = None
            for _ in range(4):
                r = create_serve(client, object_key, meta)
                statuses.append(r.status_code)
                if r.status_code == 429:
                    busy = r
                    break
            assert busy is not None, f"expected a 429, got {statuses}"
            assert busy.headers["Retry-After"] == "5"
            assert busy.json()["error"]["code"] == "busy"
            # accepted jobs still drain to success
            time.sleep(0.1)

    def test_lifecycle_stages_observed(self, tmp_path):
        """queued -> running(stage from the closed enum) -> succeeded."""
        settings = make_settings(tmp_path, stub_stage_delay_s=0.05)
        app = create_app(settings)
        valid_stages = {
            "downloading", "decoding", "segmenting", "selecting_keyframe",
            "reconstructing", "filtering", "computing_metrics",
            "generating_tips", "uploading_mesh",
        }
        with TestClient(app) as client:
            object_key, meta = upload_clip(client)
            job_id = create_serve(client, object_key, meta).json()["job_id"]
            seen_statuses, seen_stages, last_progress = [], [], -1.0
            for _ in range(400):
                p = client.get(f"/v1/serves/{job_id}", headers=AUTH).json()
                seen_statuses.append(p["status"])
                if p["status"] == "running" and p["stage"]:
                    assert p["stage"] in valid_stages
                    seen_stages.append(p["stage"])
                    assert p["progress"] >= last_progress  # monotonic in stub
                    last_progress = p["progress"]
                if p["status"] in ("succeeded", "failed"):
                    break
                time.sleep(0.01)
            assert seen_statuses[-1] == "succeeded"
            assert "running" in seen_statuses
            assert seen_stages, "expected to observe at least one running stage"
