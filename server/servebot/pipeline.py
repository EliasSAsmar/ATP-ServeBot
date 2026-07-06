"""Analysis pipeline — the seam where the real models drop in.

`AnalysisPipeline` is the interface the job worker drives. v1 (Milestone
Step 2) ships `StubAnalysisPipeline`, which walks every contract stage and
returns a fully schema-valid `succeeded` result: a placeholder GLB, a
well-formed 70-keypoint pose (geometrically consistent arm joints), and a
REAL elbow-angle metric + tip computed by the Step-5 engines.

Milestone Steps 3-4 replace the stub with a `RealAnalysisPipeline` that runs
SAM 3 + SAM 3D Body per MODELS.md — same interface, no API changes:
    download clip -> decode (PyAV) -> SAM 3 masks -> refine contact keyframe
    -> SAM 3D Body mesh + 70 kpts -> One-Euro (no-op, 1 frame) -> metrics
    -> tips -> GLB export/upload.
"""

from __future__ import annotations

import abc
import asyncio
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .config import Settings
from .glb import GLB_VERTEX_COUNT, placeholder_glb
from .metrics import METRIC_ENGINE_VERSION, build_metrics
from .schemas import (
    ContactInfo,
    CreateServeRequest,
    Diagnostics,
    Keyframe,
    Keypoint,
    Keypoints3D,
    MeshInfo,
    MetricsBlock,
    ModelVersions,
    ServeResult,
    Tip,
    TimingsMs,
)
from .skeleton import SAM3D_BODY_70_JOINTS, stub_contact_pose
from .storage import StorageBackend
from .tips import TIP_ENGINE_VERSION, generate_tips

# progress reporter: report(stage, progress)
ProgressFn = Callable[[str, float], None]


class PipelineError(Exception):
    """A job-level failure — becomes the `error` object of a failed job."""

    def __init__(self, code: str, message: str, stage: str, retriable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.retriable = retriable


@dataclass
class PipelineOutput:
    result: ServeResult
    mesh_object_key: str  # kept so polls can re-mint a fresh glb_url


class AnalysisPipeline(abc.ABC):
    """Interface between the job worker and the model stack."""

    @property
    @abc.abstractmethod
    def ready(self) -> bool:
        """False while models are loading -> /v1/serves returns 503."""

    @abc.abstractmethod
    def model_info(self) -> Dict[str, Dict]:
        """Per-model {loaded, checkpoint} for GET /v1/health."""

    @abc.abstractmethod
    async def run(
        self, job_id: str, request: CreateServeRequest, report: ProgressFn
    ) -> PipelineOutput:
        """Run the full analysis; call `report` at each stage transition."""


def one_euro_passthrough(points: List[Keypoint]) -> List[Keypoint]:
    """One-Euro filter seam (MODELS.md §6).

    With a single keyframe there is no temporal sequence to smooth, so this
    is a wired no-op. Phase-2 (temporal keypoints) replaces it with the real
    filter (min_cutoff/beta/d_cutoff) without touching the pipeline shape.
    """
    return points


# (api_stage, progress_at_stage_start, timings_ms key) — API_CONTRACT.md §4.
_STAGES = (
    ("downloading", 0.05, "download"),
    ("decoding", 0.15, "decode"),
    ("segmenting", 0.35, "segment"),
    ("selecting_keyframe", 0.45, "keyframe"),
    ("reconstructing", 0.65, "reconstruct"),
    ("filtering", 0.72, "filter"),
    ("computing_metrics", 0.80, "metrics"),
    ("generating_tips", 0.86, "tips"),
    ("uploading_mesh", 0.95, "upload_mesh"),
)


class StubAnalysisPipeline(AnalysisPipeline):
    """Schema-valid canned pipeline (Milestone Step 2) + real metric (Step 5)."""

    SAM3_CHECKPOINT = "stub-sam3 (models not wired — Step 3)"
    SAM3D_CHECKPOINT = "stub-sam3d-body (models not wired — Step 3)"

    def __init__(self, storage: StorageBackend, settings: Settings) -> None:
        self._storage = storage
        self._settings = settings

    @property
    def ready(self) -> bool:
        return True  # nothing to load; real pipeline flips this after VRAM load

    def model_info(self) -> Dict[str, Dict]:
        return {
            "sam3": {"loaded": True, "checkpoint": self.SAM3_CHECKPOINT},
            "sam3d_body": {"loaded": True, "checkpoint": self.SAM3D_CHECKPOINT},
        }

    async def run(
        self, job_id: str, request: CreateServeRequest, report: ProgressFn
    ) -> PipelineOutput:
        timings: Dict[str, int] = {}
        delay = self._settings.stub_stage_delay_s

        async def stage(name: str, timing_key: str, progress: float, fn=None):
            report(name, progress)
            t0 = time.perf_counter()
            if delay:
                await asyncio.sleep(delay)
            out = fn() if fn is not None else None
            timings[timing_key] = max(1, int((time.perf_counter() - t0) * 1000))
            return out

        # downloading — really fetch the uploaded clip from storage.
        def download() -> bytes:
            try:
                return self._storage.get_object(request.object_key)
            except KeyError:
                raise PipelineError(
                    "unprocessable_clip",
                    "Uploaded clip disappeared from storage before analysis.",
                    "downloading",
                    retriable=True,
                )

        clip_bytes = await stage("downloading", "download", 0.05, download)

        # decoding — stub: derive frame count from the clip metadata; an empty
        # upload is the local stand-in for a corrupt/0-frame clip.
        def decode() -> int:
            if len(clip_bytes) == 0:
                raise PipelineError(
                    "unprocessable_clip",
                    "Clip decoded to zero frames (empty or corrupt upload).",
                    "decoding",
                    retriable=False,
                )
            return max(1, round(request.clip.duration_ms * request.clip.fps / 1000.0))

        frames_decoded: int = await stage("decoding", "decode", 0.15, decode)

        # segmenting — stub: canned full-clip mask with plausible coverage.
        mask_coverage = await stage("segmenting", "segment", 0.35, lambda: 0.83)

        # selecting_keyframe — stub uses MODELS.md §3.2's no-2D-keypoints
        # fallback: trust the edge timestamp verbatim.
        def select_keyframe() -> ContactInfo:
            refined_ms = request.contact_timestamp_ms
            frame = round(refined_ms * request.clip.fps / 1000.0)
            frame = min(max(frame, 0), frames_decoded - 1)
            edge_conf = (
                request.edge_detect.contact_confidence
                if request.edge_detect and request.edge_detect.contact_confidence is not None
                else 0.5
            )
            return ContactInfo(
                edge_timestamp_ms=request.contact_timestamp_ms,
                refined_timestamp_ms=refined_ms,
                refined_frame_index=frame,
                contact_confidence=edge_conf,
                refine_window_ms=self._settings.refine_window_ms,
            )

        contact: ContactInfo = await stage(
            "selecting_keyframe", "keyframe", 0.45, select_keyframe
        )

        # reconstructing — stub: deterministic 70-joint pose whose serving-arm
        # joints are geometrically consistent (METRICS.md §1 worked example).
        def reconstruct() -> List[Keypoint]:
            pose = stub_contact_pose(request.handedness)
            return [
                Keypoint(index=i, name=name, xyz=pose[name][0], score=pose[name][1])
                for i, name in enumerate(SAM3D_BODY_70_JOINTS)
            ]

        keypoints: List[Keypoint] = await stage(
            "reconstructing", "reconstruct", 0.65, reconstruct
        )

        # filtering — One-Euro pass-through (single frame, MODELS.md §6).
        keypoints = await stage(
            "filtering", "filter", 0.72, lambda: one_euro_passthrough(keypoints)
        )

        # computing_metrics — REAL Step-5 engine over the keypoints. Golf is a
        # body scan only: every metric key stays null and no tips fire.
        golf = request.sport == "golf"

        def compute() -> Dict[str, Optional[dict]]:
            if golf:
                return {}
            point_map = {p.name: (p.xyz, p.score) for p in keypoints}
            return build_metrics(point_map, request.handedness, self._settings.thresholds)

        metrics = await stage("computing_metrics", "metrics", 0.80, compute)

        # generating_tips — REAL Step-5 rule engine.
        tips = await stage(
            "generating_tips",
            "tips",
            0.86,
            lambda: [] if golf else generate_tips(metrics, self._settings.thresholds),
        )

        # uploading_mesh — really write a valid placeholder GLB to storage and
        # mint a presigned GET for it.
        mesh_key = f"meshes/{job_id}/contact.glb"

        def upload_mesh() -> MeshInfo:
            self._storage.put_object(mesh_key, placeholder_glb(), "model/gltf-binary")
            glb_url, glb_expires_at = self._storage.mint_download(mesh_key)
            return MeshInfo(
                glb_url=glb_url,
                glb_expires_at=glb_expires_at,
                vertex_count=GLB_VERTEX_COUNT,
                up_axis="Y",
                units="meters",
                root_translation=(0.0, 0.0, 0.0),
            )

        mesh: MeshInfo = await stage("uploading_mesh", "upload_mesh", 0.95, upload_mesh)

        result = ServeResult(
            handedness=request.handedness,  # validated upstream
            contact=contact,
            keyframes=[
                Keyframe(
                    role="contact",
                    timestamp_ms=contact.refined_timestamp_ms,
                    frame_index=contact.refined_frame_index,
                    mesh=mesh,
                    keypoints_3d=Keypoints3D(points=keypoints),
                )
            ],
            metrics=MetricsBlock.model_validate(metrics),
            tips=[Tip.model_validate(t) for t in tips],
            diagnostics=Diagnostics(
                frames_decoded=frames_decoded,
                frames_masked=frames_decoded,
                mask_coverage_at_contact=mask_coverage,
                model_versions=ModelVersions(
                    sam3=self.SAM3_CHECKPOINT,
                    sam3d_body=self.SAM3D_CHECKPOINT,
                    metric_engine=METRIC_ENGINE_VERSION,
                    tip_engine=TIP_ENGINE_VERSION,
                ),
                timings_ms=TimingsMs(**timings),
            ),
        )
        return PipelineOutput(result=result, mesh_object_key=mesh_key)
