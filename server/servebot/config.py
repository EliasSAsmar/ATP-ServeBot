"""Centralized configuration.

Two layers:
  * ``Thresholds`` — METRICS.md §9.3, the single source of truth for metric/tip
    tuning constants. Referenced by the metric engine and the tip engine.
  * ``Settings``   — process configuration (INFRA.md §7), read from environment
    variables prefixed ``SERVEBOT_`` (see server/.env.example).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Thresholds:
    """METRICS.md §9.3 — thresholds constants (single source of truth)."""

    ELBOW_GOOD_MIN_DEG: float = 150.0
    ELBOW_BENT_MAX_DEG: float = 120.0  # below -> "too bent"
    MIN_KP_SCORE: float = 0.30
    MIN_TIP_CONFIDENCE: float = 0.50


def _env_str(name: str, default: str) -> str:
    return os.environ.get(f"SERVEBOT_{name}", default)


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(f"SERVEBOT_{name}", default))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(f"SERVEBOT_{name}", default))


@dataclass(frozen=True)
class Settings:
    """Process settings. Defaults are safe for local dev; override via env."""

    # Auth (INFRA.md §7). Never commit a real key; this default is dev-only.
    api_key: str = "dev-local-key"

    # Base URL this server is reachable at; used to mint absolute local
    # "presigned" URLs. Empty string -> relative URLs (handy in tests).
    public_base_url: str = "http://127.0.0.1:8000"

    # Storage: "local" (zero-AWS dev backend) or "s3" (real, TODO — INFRA.md §3).
    storage_backend: str = "local"
    local_storage_dir: str = "./.local-s3"
    signing_secret: str = "local-dev-signing-secret"  # local URL "presigning" only
    s3_clips_bucket: str = ""
    s3_meshes_bucket: str = ""
    aws_region: str = ""

    # Limits / TTLs (INFRA.md §2-3).
    max_clip_bytes: int = 25 * 1024 * 1024  # MAX_CLIP_BYTES [CONFIRM 25MB]
    upload_url_ttl_s: int = 300             # presigned PUT expiry (5 min)
    glb_url_ttl_s: int = 900                # presigned GET expiry (15 min)

    # Analysis pipeline selection (Milestone v1 Step 4).
    #   "stub"  = schema-valid canned pipeline (default; torch-free)
    #   "sam3d" = real SAM 3D Body on SERVEBOT_DEVICE (needs requirements-ml.txt,
    #             the MPS-patched sam-3d-body repo, and the local checkpoint dir)
    pipeline: str = "stub"
    sam3d_checkpoint_dir: str = ""  # dir with model.ckpt / model_config.yaml / assets/mhr_model.pt
    sam3d_repo: str = ""            # MPS-patched facebookresearch/sam-3d-body checkout
    device: str = "mps"

    # Phase-2 tracking + multi-frame recon (sam3d pipeline only).
    # YOLO weights: names auto-download into yolo_models_dir on first use.
    # yolo11m@1280 measured ~107ms/frame on M1 Pro MPS with spike-equal ball
    # recall; yolo11n-pose@960 ~23ms/frame.
    yolo_det_model: str = "yolo11m.pt"
    yolo_pose_model: str = "yolo11n-pose.pt"
    yolo_models_dir: str = "./.models"
    # Analysis window around the edge contact timestamp (bounds latency and
    # memory — frames outside it are decoded but not kept).
    track_window_before_ms: int = 2800
    track_window_after_ms: int = 1500
    track_window_max_frames: int = 240
    # SAM 3D Body keyframe budget per serve (12-16; NOT every frame).
    sam3d_keyframes: int = 14

    # Job model (API_CONTRACT.md §3-4).
    queue_max_depth: int = 4
    poll_after_ms: int = 1500
    retry_after_s: int = 5
    refine_window_ms: int = 200  # MODELS.md §3.1 default (±100ms)

    # Stub pipeline pacing — how long each simulated stage takes. Small in
    # tests; a bit slower in dev so the client can watch stages progress.
    stub_stage_delay_s: float = 0.15

    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
    )

    thresholds: Thresholds = field(default_factory=Thresholds)

    @classmethod
    def from_env(cls) -> "Settings":
        defaults = cls()
        origins = tuple(
            o.strip()
            for o in _env_str("CORS_ORIGINS", ",".join(defaults.cors_origins)).split(",")
            if o.strip()
        )
        return cls(
            api_key=_env_str("API_KEY", defaults.api_key),
            public_base_url=_env_str("PUBLIC_BASE_URL", defaults.public_base_url),
            storage_backend=_env_str("STORAGE_BACKEND", defaults.storage_backend),
            local_storage_dir=_env_str("LOCAL_STORAGE_DIR", defaults.local_storage_dir),
            signing_secret=_env_str("SIGNING_SECRET", defaults.signing_secret),
            s3_clips_bucket=_env_str("S3_CLIPS_BUCKET", defaults.s3_clips_bucket),
            s3_meshes_bucket=_env_str("S3_MESHES_BUCKET", defaults.s3_meshes_bucket),
            aws_region=_env_str("AWS_REGION", defaults.aws_region),
            pipeline=_env_str("PIPELINE", defaults.pipeline),
            sam3d_checkpoint_dir=_env_str("SAM3D_CHECKPOINT_DIR", defaults.sam3d_checkpoint_dir),
            sam3d_repo=_env_str("SAM3D_REPO", defaults.sam3d_repo),
            device=_env_str("DEVICE", defaults.device),
            yolo_det_model=_env_str("YOLO_DET_MODEL", defaults.yolo_det_model),
            yolo_pose_model=_env_str("YOLO_POSE_MODEL", defaults.yolo_pose_model),
            yolo_models_dir=_env_str("YOLO_MODELS_DIR", defaults.yolo_models_dir),
            track_window_before_ms=_env_int(
                "TRACK_WINDOW_BEFORE_MS", defaults.track_window_before_ms
            ),
            track_window_after_ms=_env_int(
                "TRACK_WINDOW_AFTER_MS", defaults.track_window_after_ms
            ),
            track_window_max_frames=_env_int(
                "TRACK_WINDOW_MAX_FRAMES", defaults.track_window_max_frames
            ),
            sam3d_keyframes=_env_int("SAM3D_KEYFRAMES", defaults.sam3d_keyframes),
            max_clip_bytes=_env_int("MAX_CLIP_BYTES", defaults.max_clip_bytes),
            upload_url_ttl_s=_env_int("UPLOAD_URL_TTL_S", defaults.upload_url_ttl_s),
            glb_url_ttl_s=_env_int("GLB_URL_TTL_S", defaults.glb_url_ttl_s),
            queue_max_depth=_env_int("QUEUE_MAX_DEPTH", defaults.queue_max_depth),
            poll_after_ms=_env_int("POLL_AFTER_MS", defaults.poll_after_ms),
            retry_after_s=_env_int("RETRY_AFTER_S", defaults.retry_after_s),
            refine_window_ms=_env_int("REFINE_WINDOW_MS", defaults.refine_window_ms),
            stub_stage_delay_s=_env_float("STUB_STAGE_DELAY_S", defaults.stub_stage_delay_s),
            cors_origins=origins,
        )
