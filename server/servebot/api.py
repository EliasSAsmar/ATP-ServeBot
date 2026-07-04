"""/v1 routes — implements API_CONTRACT.md §1-5 exactly."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Query, Request

from .errors import ApiError
from .jobs import Job, JobQueueFull
from .schemas import (
    CreateServeRequest,
    CreateServeResponse,
    GpuInfo,
    HealthModels,
    HealthResponse,
    JobStatusResponse,
    JobSummary,
    ListServesResponse,
    ModelStatus,
    UploadRequest,
    UploadResponse,
    now_utc,
)

_ALLOWED_CLIP_TYPES = {"video/webm": ".webm", "video/mp4": ".mp4"}
_JOB_STATUSES = {"queued", "running", "succeeded", "failed"}


@dataclass
class MintedUpload:
    """Bookkeeping for keys this API minted (API_CONTRACT.md §2)."""

    content_type: str
    byte_size: int
    minted_at: datetime


async def require_api_key(request: Request) -> None:
    """X-API-Key auth on every /v1 endpoint (API_CONTRACT.md §0)."""
    import secrets

    expected = request.app.state.settings.api_key
    provided = request.headers.get("X-API-Key", "")
    if not provided or not secrets.compare_digest(provided, expected):
        raise ApiError(401, "unauthorized", "Missing or invalid X-API-Key header.")


router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])


# ------------------------------------------------------------ §1 GET /health


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    app = request.app
    pipeline = app.state.pipeline
    info = pipeline.model_info()
    return HealthResponse(
        status="ok" if pipeline.ready else "starting",
        instance_up=True,
        models_ready=pipeline.ready,
        models=HealthModels(
            sam3=ModelStatus(**info["sam3"]),
            sam3d_body=ModelStatus(**info["sam3d_body"]),
        ),
        # No GPU on the local-dev box; truthful placeholder until the real
        # pipeline (which reads nvidia-smi/torch) lands.
        gpu=GpuInfo(name="none (local dev stub)", vram_total_mb=0, vram_used_mb=0),
        queue_depth=app.state.worker.queue_depth,
        server_time=now_utc(),
    )


# ---------------------------------------------------------- §2 POST /uploads


@router.post("/uploads", response_model=UploadResponse)
async def create_upload(body: UploadRequest, request: Request) -> UploadResponse:
    settings = request.app.state.settings

    if body.content_type not in _ALLOWED_CLIP_TYPES:
        raise ApiError(
            400,
            "invalid_request",
            "content_type must be one of: video/webm, video/mp4",
            field="content_type",
        )
    if body.byte_size <= 0:
        raise ApiError(400, "invalid_request", "byte_size must be > 0", field="byte_size")
    if body.byte_size > settings.max_clip_bytes:
        raise ApiError(
            413,
            "clip_too_large",
            f"byte_size exceeds MAX_CLIP_BYTES ({settings.max_clip_bytes}).",
            field="byte_size",
        )
    for name, value in (
        ("duration_ms", body.duration_ms),
        ("fps", body.fps),
        ("width", body.width),
        ("height", body.height),
    ):
        if value <= 0:
            raise ApiError(400, "invalid_request", f"{name} must be > 0", field=name)

    ext = _ALLOWED_CLIP_TYPES[body.content_type]
    now = datetime.now(timezone.utc)
    object_key = f"clips/{now:%Y/%m/%d}/{uuid.uuid4()}{ext}"

    presigned = request.app.state.storage.mint_clip_upload(object_key, body.content_type)
    minted: Dict[str, MintedUpload] = request.app.state.minted_uploads
    minted[object_key] = MintedUpload(
        content_type=body.content_type, byte_size=body.byte_size, minted_at=now
    )
    return UploadResponse(
        object_key=object_key,
        upload_url=presigned.url,
        upload_method="PUT",
        upload_headers=presigned.headers,
        expires_at=presigned.expires_at,
    )


# ----------------------------------------------------------- §3 POST /serves


@router.post("/serves", response_model=CreateServeResponse, status_code=202)
async def create_serve(body: CreateServeRequest, request: Request) -> CreateServeResponse:
    app = request.app
    settings = app.state.settings

    if body.handedness not in ("right", "left"):
        raise ApiError(
            400,
            "invalid_handedness",
            "handedness must be one of: right, left",
            field="handedness",
        )
    if (
        not body.object_key.startswith("clips/")
        or body.object_key not in app.state.minted_uploads
    ):
        raise ApiError(
            400,
            "invalid_object_key",
            "object_key was not minted by this API (POST /v1/uploads first).",
            field="object_key",
        )
    if body.contact_timestamp_ms < 0 or body.contact_timestamp_ms > body.clip.duration_ms:
        raise ApiError(
            400,
            "invalid_timestamp",
            "contact_timestamp_ms must satisfy 0 <= x <= clip.duration_ms.",
            field="contact_timestamp_ms",
        )
    if not app.state.storage.object_exists(body.object_key):
        raise ApiError(
            409,
            "clip_not_found",
            "No uploaded object found for object_key; PUT the clip first.",
            field="object_key",
        )
    if not app.state.pipeline.ready:
        raise ApiError(
            503, "models_not_ready", "Models are still loading; retry shortly."
        )

    job = Job(job_id=str(uuid.uuid4()), request=body)
    try:
        app.state.worker.submit(job)
    except JobQueueFull:
        raise ApiError(
            429,
            "busy",
            "A job is already running and the queue is full; retry later.",
            headers={"Retry-After": str(settings.retry_after_s)},
        ) from None
    app.state.job_store.add(job)

    return CreateServeResponse(
        job_id=job.job_id,
        created_at=job.created_at,
        poll_url=f"/v1/serves/{job.job_id}",
        poll_after_ms=settings.poll_after_ms,
    )


# ------------------------------------------------------------ §5 GET /serves
# (declared before the {job_id} route only for readability; paths don't clash)


@router.get("/serves", response_model=ListServesResponse)
async def list_serves(
    request: Request,
    limit: int = Query(default=20),
    status: Optional[str] = Query(default=None),
) -> ListServesResponse:
    if status is not None and status not in _JOB_STATUSES:
        raise ApiError(
            400,
            "invalid_request",
            "status must be one of: queued, running, succeeded, failed",
            field="status",
        )
    limit = max(1, min(limit, 100))
    jobs = request.app.state.job_store.list_recent(limit, status)
    summaries = [
        JobSummary(
            job_id=j.job_id,
            status=j.status,
            created_at=j.created_at,
            handedness=j.request.handedness,
            user_label=j.user_label,
        )
        for j in jobs
    ]
    return ListServesResponse(jobs=summaries, count=len(summaries))


# ---------------------------------------------------- §4 GET /serves/{job_id}


@router.get("/serves/{job_id}", response_model=JobStatusResponse)
async def get_serve(job_id: str, request: Request) -> JobStatusResponse:
    app = request.app
    job = app.state.job_store.get(job_id)
    if job is None:
        raise ApiError(404, "job_not_found", f"No job with id {job_id}.")

    terminal = job.status in ("succeeded", "failed")
    if job.status == "succeeded" and job.result and job.mesh_object_key:
        # Presigned GETs expire (default 15 min); the contract's refresh story
        # is "re-poll" (§4c) — so re-mint a fresh glb_url on every poll.
        glb_url, glb_expires_at = app.state.storage.mint_download(job.mesh_object_key)
        mesh = job.result.keyframes[0].mesh
        mesh.glb_url = glb_url
        mesh.glb_expires_at = glb_expires_at

    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        poll_after_ms=None if terminal else app.state.settings.poll_after_ms,
        result=job.result,
        error=job.error,
    )
