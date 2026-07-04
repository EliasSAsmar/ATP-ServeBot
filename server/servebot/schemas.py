"""Pydantic models for every request/response payload in API_CONTRACT.md.

These are the contract, encoded: closed enums are `Literal`s, timestamps
serialize as ISO-8601 UTC with a `Z` suffix and millisecond precision, and
the nullability semantics of `result.metrics` are captured in the types
(`null` = not built; object with `value: null` = built but failed).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer


def _iso_millis_z(dt: datetime) -> str:
    """ISO-8601 UTC with `Z` and millisecond precision (API_CONTRACT.md §0)."""
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


UtcMillis = Annotated[
    datetime, PlainSerializer(_iso_millis_z, return_type=str, when_used="json")
]

Handedness = Literal["right", "left"]
JobStatus = Literal["queued", "running", "succeeded", "failed"]
Stage = Literal[
    "downloading",
    "decoding",
    "segmenting",
    "selecting_keyframe",
    "reconstructing",
    "filtering",
    "computing_metrics",
    "generating_tips",
    "uploading_mesh",
]
Severity = Literal["info", "suggestion", "flag"]
Comparator = Literal["lt", "lte", "gt", "gte", "in_range", "out_of_range"]
ElbowBand = Literal["straight", "nearly_straight", "slightly_bent", "bent", "very_bent"]
ClipContentType = Literal["video/webm", "video/mp4"]

Vec3 = Tuple[float, float, float]


# ---------------------------------------------------------------- §1 health


class ModelStatus(BaseModel):
    loaded: bool
    checkpoint: str


class HealthModels(BaseModel):
    sam3: ModelStatus
    sam3d_body: ModelStatus


class GpuInfo(BaseModel):
    name: str
    vram_total_mb: int
    vram_used_mb: int


class HealthResponse(BaseModel):
    status: Literal["ok", "starting"]
    instance_up: bool
    models_ready: bool
    models: HealthModels
    gpu: GpuInfo
    queue_depth: int
    server_time: UtcMillis
    api_version: Literal["v1"] = "v1"


# --------------------------------------------------------------- §2 uploads


class UploadRequest(BaseModel):
    # content_type is validated in the route so out-of-enum values map to the
    # contract's `invalid_request` envelope rather than a raw 422.
    content_type: str
    byte_size: int
    duration_ms: int
    fps: float
    width: int
    height: int


class UploadResponse(BaseModel):
    object_key: str
    upload_url: str
    upload_method: Literal["PUT"] = "PUT"
    upload_headers: Dict[str, str]
    expires_at: UtcMillis


# ---------------------------------------------------------------- §3 serves


class ClipMeta(BaseModel):
    duration_ms: int
    fps: float
    width: int
    height: int
    content_type: str


class EdgeDetect(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    detector_version: Optional[str] = None
    contact_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    peak_wrist_velocity_px_s: Optional[float] = None
    arm_elevation_deg_at_contact: Optional[float] = None


class ClientInfo(BaseModel):
    app_version: Optional[str] = None
    platform: Optional[str] = None
    user_label: Optional[str] = None


class CreateServeRequest(BaseModel):
    object_key: str
    # Kept as `str` so the route can return the dedicated `invalid_handedness`
    # error code instead of a generic validation failure.
    handedness: str
    contact_timestamp_ms: int
    clip: ClipMeta
    edge_detect: Optional[EdgeDetect] = None
    client: Optional[ClientInfo] = None


class CreateServeResponse(BaseModel):
    job_id: str
    status: Literal["queued"] = "queued"
    created_at: UtcMillis
    poll_url: str
    poll_after_ms: int


# ------------------------------------------------------- §4 job status/result


class ContactInfo(BaseModel):
    edge_timestamp_ms: int
    refined_timestamp_ms: int
    refined_frame_index: int
    contact_confidence: float = Field(ge=0.0, le=1.0)
    refine_window_ms: int


class MeshInfo(BaseModel):
    glb_url: str
    glb_expires_at: UtcMillis
    vertex_count: int
    up_axis: Literal["Y"] = "Y"
    units: Literal["meters"] = "meters"
    root_translation: Vec3 = (0.0, 0.0, 0.0)


class Keypoint(BaseModel):
    index: int
    name: str
    xyz: Vec3
    score: float = Field(ge=0.0, le=1.0)


class Keypoints3D(BaseModel):
    format: Literal["sam3d-body-70"] = "sam3d-body-70"
    count: Literal[70] = 70
    units: Literal["meters"] = "meters"
    points: List[Keypoint] = Field(min_length=70, max_length=70)


class Keyframe(BaseModel):
    role: Literal["contact"] = "contact"
    timestamp_ms: int
    frame_index: int
    mesh: MeshInfo
    keypoints_3d: Keypoints3D


class ElbowAngleComputed(BaseModel):
    """Successfully computed elbow metric (METRICS.md §1 output object)."""

    value: float
    unit: Literal["degree"] = "degree"
    side: Handedness
    joints: List[str]
    keyframe_role: Literal["contact"] = "contact"
    confidence: float = Field(ge=0.0, le=1.0)
    band: ElbowBand
    reference_range_deg: Tuple[float, float]


class ElbowAngleFailed(BaseModel):
    """Implemented-but-failed shape (API_CONTRACT.md §4c nullability rule)."""

    value: None = None
    unit: Literal["degree"] = "degree"
    side: Handedness
    compute_error: str
    missing: List[str]
    confidence: float = 0.0


ElbowAngleMetric = Union[ElbowAngleComputed, ElbowAngleFailed]


class MetricsBlock(BaseModel):
    """All planned metric keys present; stubs are always null in v1."""

    elbow_angle_deg: ElbowAngleMetric
    shoulder_angle_deg: None = None
    knee_flexion_deg: None = None
    kinetic_chain_sequence: None = None
    toss_placement: None = None
    toss_consistency: None = None
    contact_height: None = None
    phase_timing: None = None


class TriggeredBy(BaseModel):
    value: float
    threshold: float
    comparator: Comparator


class Tip(BaseModel):
    id: str
    metric: str
    severity: Severity
    title: str
    message: str
    triggered_by: TriggeredBy


class ModelVersions(BaseModel):
    sam3: str
    sam3d_body: str
    metric_engine: str
    tip_engine: str


class TimingsMs(BaseModel):
    download: int
    decode: int
    segment: int
    keyframe: int
    reconstruct: int
    filter: int
    metrics: int
    tips: int
    upload_mesh: int


class Diagnostics(BaseModel):
    frames_decoded: int
    frames_masked: int
    mask_coverage_at_contact: float
    model_versions: ModelVersions
    timings_ms: TimingsMs

    model_config = ConfigDict(protected_namespaces=())


class ServeResult(BaseModel):
    schema_version: Literal["serve-result-1"] = "serve-result-1"
    handedness: Handedness
    contact: ContactInfo
    keyframes: List[Keyframe] = Field(min_length=1, max_length=1)  # v1: contact only
    metrics: MetricsBlock
    tips: List[Tip]  # [] when nothing fired — never null
    diagnostics: Diagnostics


class JobErrorInfo(BaseModel):
    """`error` object on a failed job (API_CONTRACT.md §4d)."""

    code: str
    message: str
    stage: Stage
    retriable: bool


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    stage: Optional[Stage]
    progress: float = Field(ge=0.0, le=1.0)
    created_at: UtcMillis
    started_at: Optional[UtcMillis]
    finished_at: Optional[UtcMillis]
    poll_after_ms: Optional[int]
    result: Optional[ServeResult]
    error: Optional[JobErrorInfo]


# ------------------------------------------------------------- §5 job listing


class JobSummary(BaseModel):
    job_id: str
    status: JobStatus
    created_at: UtcMillis
    handedness: Handedness
    user_label: Optional[str]


class ListServesResponse(BaseModel):
    jobs: List[JobSummary]
    count: int


# ---------------------------------------------------------------- §0 errors


class ErrorBody(BaseModel):
    code: str
    message: str
    field: Optional[str]
    request_id: str


class ErrorEnvelope(BaseModel):
    error: ErrorBody


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def dump_json(model: BaseModel) -> Any:
    return model.model_dump(mode="json")
