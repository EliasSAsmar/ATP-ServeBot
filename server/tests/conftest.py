import pytest
from fastapi.testclient import TestClient

from servebot.config import Settings
from servebot.main import create_app

API_KEY = "test-api-key"
AUTH = {"X-API-Key": API_KEY}


def make_settings(tmp_path, **overrides) -> Settings:
    base = dict(
        api_key=API_KEY,
        public_base_url="",  # relative presigned URLs -> routable via TestClient
        local_storage_dir=str(tmp_path / "local-s3"),
        signing_secret="test-signing-secret",
        stub_stage_delay_s=0.0,
        poll_after_ms=10,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def upload_clip(client, content: bytes = b"webm" * 600, content_type: str = "video/webm"):
    """Run the §2 upload dance; returns (object_key, clip_meta)."""
    clip_meta = {
        "content_type": content_type,
        "byte_size": max(len(content), 1),
        "duration_ms": 3200,
        "fps": 30,
        "width": 1280,
        "height": 720,
    }
    r = client.post("/v1/uploads", json=clip_meta, headers=AUTH)
    assert r.status_code == 200, r.text
    up = r.json()
    # The PUT goes to "S3" (local stand-in) — no X-API-Key (contract §2b).
    pr = client.put(
        up["upload_url"], content=content, headers={"Content-Type": content_type}
    )
    assert pr.status_code == 200, pr.text
    assert "ETag" in pr.headers
    return up["object_key"], clip_meta


def create_serve(http, object_key, clip_meta, handedness="right", contact_ms=1840, **extra):
    # First param named `http` (not `client`) because the request body's
    # optional `client` object is passed through **extra.
    body = {
        "object_key": object_key,
        "handedness": handedness,
        "contact_timestamp_ms": contact_ms,
        "clip": {
            "duration_ms": clip_meta["duration_ms"],
            "fps": clip_meta["fps"],
            "width": clip_meta["width"],
            "height": clip_meta["height"],
            "content_type": clip_meta["content_type"],
        },
        **extra,
    }
    return http.post("/v1/serves", json=body, headers=AUTH)


def poll_until_terminal(client, job_id, tries=400):
    import time

    for _ in range(tries):
        r = client.get(f"/v1/serves/{job_id}", headers=AUTH)
        assert r.status_code == 200, r.text
        payload = r.json()
        if payload["status"] in ("succeeded", "failed"):
            return payload
        time.sleep(0.01)
    raise AssertionError("job did not reach a terminal status in time")
