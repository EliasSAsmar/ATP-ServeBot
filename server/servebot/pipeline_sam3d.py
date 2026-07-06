"""Real analysis pipeline — SAM 3D Body on Apple Silicon (MPS), phase-2.

Drop-in `AnalysisPipeline` implementation (same `run(...)` shape and stage
callbacks as `StubAnalysisPipeline`) that does the real work:

    download clip -> decode a bounded window around contact (OpenCV)
    -> YOLO tracking: person + racket + ball chains, dense 2D pose  (segmenting)
    -> refine contact (ball horizontal-velocity break / pose peak reach),
       detect serve phases, pick ~12-16 SAM3D keyframes            (selecting_keyframe)
    -> SAM 3D Body mesh + 70 real MHR70 keypoints on the keyframes (reconstructing)
    -> temporal median filter over the keyframe stack              (filtering)
    -> real metrics: elbow/shoulder/knee/contact-height at contact +
       phase timing + kinetic chain (dense 2D) + toss placement    (computing_metrics)
    -> real tips -> real GLB export/upload (trimesh).

Latency engineering (M1 Pro, measured): YOLO det yolo11m@1280 ~107 ms/frame,
pose yolo11n-pose@960 ~23 ms/frame, SAM3D ~1.5-2.5 s/keyframe -> a 3-4 s
serve lands well under the ~60 s/serve budget. SAM3D runs on KEYFRAMES ONLY.

Model loading notes (proven by the MPS spike — do not "simplify" back):
  * The upstream HF loader (`load_sam_3d_body_hf`) ignores its device kwarg,
    so we replicate `load_sam_3d_body` against a LOCAL checkpoint dir and move
    the model to the device ourselves.
  * fp32 (TRAIN.USE_FP16=False) is deliberate: on M1 it is faster than bf16
    and numerically identical for this model.
  * The sam-3d-body repo pointed to by SERVEBOT_SAM3D_REPO must be the
    MPS-patched copy (guarded cuda.empty_cache, device-aware recursive_to,
    MHR TorchScript head forced to CPU/float32 — MPS lacks float64).

torch / sam_3d_body / cv2 / trimesh / ultralytics are imported lazily inside
functions so importing this module (and therefore the base app) stays
torch-free.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from . import temporal, tracking
from .config import Settings
from .metrics import METRIC_ENGINE_VERSION, build_metrics
from .pipeline import (
    AnalysisPipeline,
    PipelineError,
    PipelineOutput,
    ProgressFn,
    one_euro_passthrough,
)
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
    TimingsMs,
    Tip,
    TrackingBlock,
)
from .skeleton import JOINT_INDEX, SAM3D_BODY_70_JOINTS
from .storage import StorageBackend
from .tips import TIP_ENGINE_VERSION, generate_tips

log = logging.getLogger("servebot.pipeline.sam3d")

SAM3D_BODY_VERSION = "sam-3d-body-dinov3"

# The estimator output carries no per-keypoint confidence (verified against
# sam_3d_body_estimator.py output dict), so every real keypoint gets this
# default score. Revisit if a future checkpoint exposes one.
DEFAULT_KEYPOINT_SCORE = 1.0

# ---------------------------------------------------------------------------
# Model singleton — loaded once per process, kept resident across serves.
# ---------------------------------------------------------------------------

_ESTIMATOR: Any = None
_ESTIMATOR_KEY: Optional[Tuple[str, str, str]] = None
_ESTIMATOR_LOCK = threading.Lock()


def _validate_paths(settings: Settings) -> None:
    repo = settings.sam3d_repo
    ckpt_dir = settings.sam3d_checkpoint_dir
    if not repo or not os.path.isdir(os.path.join(repo, "sam_3d_body")):
        raise RuntimeError(
            "SERVEBOT_SAM3D_REPO must point at the MPS-patched sam-3d-body repo "
            f"(a directory containing sam_3d_body/); got: {repo!r}"
        )
    required = (
        "model_config.yaml",
        "model.ckpt",
        os.path.join("assets", "mhr_model.pt"),
    )
    missing = [f for f in required if not os.path.isfile(os.path.join(ckpt_dir, f))]
    if not ckpt_dir or missing:
        raise RuntimeError(
            "SERVEBOT_SAM3D_CHECKPOINT_DIR must point at the sam-3d-body-dinov3 "
            f"checkpoint dir; got {ckpt_dir!r} (missing: {missing or 'directory'})"
        )


def _load_estimator(settings: Settings) -> Any:
    """Load SAM 3D Body onto the configured device. Raises RuntimeError loudly."""
    _validate_paths(settings)

    repo = os.path.abspath(settings.sam3d_repo)
    if repo not in sys.path:
        sys.path.insert(0, repo)
    # A handful of ops still fall back to CPU on MPS; allow it (spike-proven).
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

    try:
        import torch
    except ImportError as exc:
        raise RuntimeError(
            "SERVEBOT_PIPELINE=sam3d requires the ML deps: "
            "pip install -r server/requirements-ml.txt"
        ) from exc

    device = settings.device
    if device == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError(
            "SERVEBOT_DEVICE=mps but torch.backends.mps is unavailable on this "
            "machine/build. Refusing to fall back silently — set SERVEBOT_DEVICE "
            "or fix the torch install."
        )

    from sam_3d_body import SAM3DBodyEstimator  # noqa: E402 — needs repo on sys.path
    from sam_3d_body.models.meta_arch import SAM3DBody
    from sam_3d_body.utils.checkpoint import load_state_dict
    from sam_3d_body.utils.config import get_config

    ckpt_dir = settings.sam3d_checkpoint_dir
    t0 = time.perf_counter()
    cfg = get_config(os.path.join(ckpt_dir, "model_config.yaml"))
    cfg.defrost()
    cfg.MODEL.MHR_HEAD.MHR_MODEL_PATH = os.path.join(ckpt_dir, "assets", "mhr_model.pt")
    cfg.TRAIN.USE_FP16 = False  # fp32 on M1: faster than bf16, identical output
    cfg.freeze()

    model = SAM3DBody(cfg)
    ckpt = torch.load(
        os.path.join(ckpt_dir, "model.ckpt"), map_location="cpu", weights_only=False
    )
    load_state_dict(model, ckpt.get("state_dict", ckpt), strict=False)
    model = model.to(device)
    model.eval()

    estimator = SAM3DBodyEstimator(
        model, cfg, human_detector=None, human_segmentor=None, fov_estimator=None
    )
    log.info(
        "SAM 3D Body loaded on %s in %.1fs (fp32, checkpoint=%s)",
        device,
        time.perf_counter() - t0,
        ckpt_dir,
    )
    return estimator


def get_estimator(settings: Settings) -> Any:
    """Module-level lazy singleton — the ~20s load happens once per process."""
    global _ESTIMATOR, _ESTIMATOR_KEY
    key = (settings.sam3d_repo, settings.sam3d_checkpoint_dir, settings.device)
    with _ESTIMATOR_LOCK:
        if _ESTIMATOR is None or _ESTIMATOR_KEY != key:
            _ESTIMATOR = _load_estimator(settings)
            _ESTIMATOR_KEY = key
        return _ESTIMATOR


def _yolo_weight_path(settings: Settings, name: str) -> str:
    """Resolve (and download once) a YOLO weight into yolo_models_dir."""
    d = settings.yolo_models_dir
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    if not os.path.isfile(path):
        from ultralytics.utils.downloads import attempt_download_asset

        attempt_download_asset(path)
    return path


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class Sam3DAnalysisPipeline(AnalysisPipeline):
    """SAM 3D Body (MPS) + YOLO tracking pipeline. SAM 3 is NOT used (v1)."""

    def __init__(self, storage: StorageBackend, settings: Settings) -> None:
        self._storage = storage
        self._settings = settings
        # Fail loudly at startup: no model -> the process must not come up
        # pretending to be able to analyze serves.
        self._estimator = get_estimator(settings)
        self._yolo_det = tracking.get_yolo(
            _yolo_weight_path(settings, settings.yolo_det_model), settings.device
        )
        self._yolo_pose = tracking.get_yolo(
            _yolo_weight_path(settings, settings.yolo_pose_model), settings.device
        )

    @property
    def ready(self) -> bool:
        return self._estimator is not None

    def model_info(self) -> Dict[str, Dict]:
        return {
            "sam3": {"loaded": False, "checkpoint": "not used (v1 local path)"},
            "sam3d_body": {
                "loaded": self._estimator is not None,
                "checkpoint": self._settings.sam3d_checkpoint_dir,
            },
        }

    def gpu_info(self) -> Dict[str, Any]:
        """Real device stats for GET /v1/health (MPS unified memory)."""
        import torch

        if self._settings.device == "mps" and torch.backends.mps.is_available():
            try:
                total_mb = int(torch.mps.recommended_max_memory() / (1024 * 1024))
                used_mb = int(torch.mps.driver_allocated_memory() / (1024 * 1024))
            except Exception:  # pragma: no cover — API drift safety
                total_mb = used_mb = 0
            return {
                "name": "Apple Silicon GPU (MPS, unified memory)",
                "vram_total_mb": total_mb,
                "vram_used_mb": used_mb,
            }
        return {"name": self._settings.device, "vram_total_mb": 0, "vram_used_mb": 0}

    # ---- stage implementations (sync, run in worker threads when heavy) ----

    def _download(self, request: CreateServeRequest) -> bytes:
        try:
            return self._storage.get_object(request.object_key)
        except KeyError:
            raise PipelineError(
                "unprocessable_clip",
                "Uploaded clip disappeared from storage before analysis.",
                "downloading",
                retriable=True,
            ) from None

    def _decode(self, request: CreateServeRequest, clip_bytes: bytes):
        """Decode the clip, keeping only the analysis window around contact.

        Returns (frames_bgr, frame_ids, effective_fps, frames_decoded, fps).
        The window is [contact - track_window_before_ms, contact +
        track_window_after_ms], capped at track_window_max_frames by striding
        (bounds memory and the tracking pass latency).
        """
        import cv2

        if len(clip_bytes) == 0:
            raise PipelineError(
                "unprocessable_clip",
                "Clip decoded to zero frames (empty or corrupt upload).",
                "decoding",
                retriable=False,
            )

        suffix = ".webm" if "webm" in (request.clip.content_type or "") else ".mp4"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(clip_bytes)
            tmp_path = tmp.name
        try:
            cap = cv2.VideoCapture(tmp_path)
            try:
                fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
                if not fps or fps <= 0 or fps != fps:  # missing/NaN -> trust the edge
                    fps = float(request.clip.fps)
                s = self._settings
                lo = int((request.contact_timestamp_ms - s.track_window_before_ms) * fps / 1000.0)
                hi = int((request.contact_timestamp_ms + s.track_window_after_ms) * fps / 1000.0)
                lo = max(lo, 0)
                stride = max(1, -(-(hi - lo + 1) // s.track_window_max_frames))  # ceil

                frames: List[Any] = []
                frame_ids: List[int] = []
                frames_decoded = 0
                last_frame = None
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    i = frames_decoded
                    if lo <= i <= hi and (i - lo) % stride == 0:
                        frames.append(frame)
                        frame_ids.append(i)
                    last_frame = frame
                    frames_decoded += 1
            finally:
                cap.release()
        finally:
            os.unlink(tmp_path)

        if frames_decoded == 0:
            raise PipelineError(
                "unprocessable_clip",
                "Clip decoded to zero frames (empty or corrupt upload).",
                "decoding",
                retriable=False,
            )
        if not frames:
            # Contact timestamp maps past the end — fall back to the last
            # decoded frame (v1 behavior) so contact-only analysis still runs.
            frames = [last_frame]
            frame_ids = [frames_decoded - 1]
            stride = 1
        return frames, frame_ids, fps / stride, frames_decoded, fps

    def _track(self, frames, frame_ids, eff_fps) -> tracking.Tracks:
        """YOLO detection/tracking + dense 2D pose over the window frames."""
        try:
            return tracking.run_tracking(
                frames, frame_ids, eff_fps,
                self._yolo_det, self._yolo_pose, self._settings.device,
            )
        except Exception:
            log.exception("tracking failed; continuing without ball/racket/pose")
            h, w = frames[0].shape[:2]
            return tracking.Tracks(
                fps=eff_fps, width=w, height=h, frame_ids=list(frame_ids),
                person=[None] * len(frames), racket=[None] * len(frames),
                racket_src=[None] * len(frames), ball_chain=None,
            )

    def _select_keyframes(
        self,
        request: CreateServeRequest,
        tracks: tracking.Tracks,
        frame_ids: List[int],
        eff_fps: float,
        clip_fps: float,
        frames_decoded: int,
    ):
        """Contact refinement + phase detection + SAM3D keyframe picks.

        Returns (contact_info, phases|None, ball|None, keyframe_window_idxs,
        contact_pick_idx).
        """
        import numpy as np

        edge_conf = (
            request.edge_detect.contact_confidence
            if request.edge_detect and request.edge_detect.contact_confidence is not None
            else 0.5
        )
        edge_frame = int(round(request.contact_timestamp_ms * clip_fps / 1000.0))

        phases = None
        if tracks.pose_xy is not None:
            phases = temporal.detect_phases(
                tracks.pose_xy, tracks.pose_conf, eff_fps, request.handedness
            )

        ball = tracking.analyze_ball_arc(tracks, clip_fps)

        # ---- refined contact: ball break > pose peak reach > edge ----
        if ball is not None:
            contact_frame = ball.contact_frame  # absolute clip frame
            confidence = ball.contact_confidence
        elif phases is not None:
            contact_frame = frame_ids[phases.i_contact]
            confidence = 0.6
        else:
            contact_frame = min(max(edge_frame, 0), frames_decoded - 1)
            confidence = edge_conf
        refined_ms = int(round(contact_frame * 1000.0 / clip_fps))

        # window index of the refined contact (recon + metrics anchor)
        contact_widx = int(np.argmin([abs(f - contact_frame) for f in frame_ids]))
        if phases is not None:
            # keep phase-derived picks but force the contact pick onto the
            # refined contact frame
            phases = temporal.Phases(
                i_start=min(phases.i_start, contact_widx),
                i_trophy=min(phases.i_trophy, contact_widx),
                i_accel=min(phases.i_accel, contact_widx),
                i_contact=contact_widx,
                i_end=max(phases.i_end, contact_widx),
            )
            picks = temporal.select_keyframes(
                phases, len(frame_ids), self._settings.sam3d_keyframes
            )
        else:
            # no usable motion signal: spread the keyframe budget evenly
            n = min(self._settings.sam3d_keyframes, 12, len(frame_ids))
            picks = sorted(set(
                int(v) for v in np.linspace(0, len(frame_ids) - 1, n)
            ) | {contact_widx})
        if contact_widx not in picks:
            picks = sorted(set(picks) | {contact_widx})

        contact = ContactInfo(
            edge_timestamp_ms=request.contact_timestamp_ms,
            refined_timestamp_ms=refined_ms,
            refined_frame_index=contact_frame,
            contact_confidence=confidence,
            refine_window_ms=self._settings.refine_window_ms,
        )
        return contact, phases, ball, picks, picks.index(contact_widx)

    # ---- golf body-scan path (no serve refinement / metrics / tracking) ----

    def _golf_contact_widx(
        self, request: CreateServeRequest, frame_ids: List[int],
        clip_fps: float, frames_decoded: int,
    ) -> int:
        """Window index of the frame at the captured moment (edge timestamp)."""
        edge_frame = int(round(request.contact_timestamp_ms * clip_fps / 1000.0))
        contact_frame = min(max(edge_frame, 0), frames_decoded - 1)
        return min(range(len(frame_ids)), key=lambda i: abs(frame_ids[i] - contact_frame))

    def _track_golf(
        self, request: CreateServeRequest, frames, frame_ids: List[int],
        eff_fps: float, clip_fps: float, frames_decoded: int,
    ) -> tracking.Tracks:
        """Golf: skip the full ball/racket/pose window pass — only detect the
        person on the single frame SAM 3D will reconstruct."""
        h, w = frames[0].shape[:2]
        tracks = tracking.Tracks(
            fps=eff_fps, width=w, height=h, frame_ids=list(frame_ids),
            person=[None] * len(frames), racket=[None] * len(frames),
            racket_src=[None] * len(frames), ball_chain=None,
        )
        widx = self._golf_contact_widx(request, frame_ids, clip_fps, frames_decoded)
        try:
            tracks.person[widx] = tracking.detect_person(
                frames[widx], self._yolo_det, self._settings.device
            )
        except Exception:
            log.exception("golf person detect failed; SAM3D will use the full frame")
        return tracks

    def _select_keyframes_golf(
        self, request: CreateServeRequest, frame_ids: List[int],
        clip_fps: float, frames_decoded: int,
    ):
        """Golf: trust the captured moment verbatim (no ball/pose refinement)
        and reconstruct only that one frame."""
        widx = self._golf_contact_widx(request, frame_ids, clip_fps, frames_decoded)
        edge_conf = (
            request.edge_detect.contact_confidence
            if request.edge_detect and request.edge_detect.contact_confidence is not None
            else 0.5
        )
        contact_frame = frame_ids[widx]
        contact = ContactInfo(
            edge_timestamp_ms=request.contact_timestamp_ms,
            refined_timestamp_ms=int(round(contact_frame * 1000.0 / clip_fps)),
            refined_frame_index=contact_frame,
            contact_confidence=edge_conf,
            refine_window_ms=0,  # no refinement in golf mode
        )
        return contact, None, None, [widx], 0

    def _reconstruct_one(self, frame_bgr, person_box) -> Dict[str, Any]:
        """SAM 3D Body inference on one frame (MPS), person-bbox cropped."""
        import cv2
        import numpy as np

        frame_rgb = np.ascontiguousarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
        h, w = frame_rgb.shape[:2]
        if person_box is not None:
            x0, y0, x1, y1 = person_box[:4]
            pw, ph = 0.04 * (x1 - x0), 0.04 * (y1 - y0)  # spike's 4% pad
            bbox = np.array(
                [max(0, x0 - pw), max(0, y0 - ph), min(w, x1 + pw), min(h, y1 + ph)],
                dtype=np.float32,
            )
        else:
            bbox = np.array([0, 0, w, h], dtype=np.float32)

        outputs = self._estimator.process_one_image(
            frame_rgb, bboxes=bbox, masks=None, use_mask=False, inference_type="body"
        )
        if not outputs:
            raise PipelineError(
                "no_person_detected",
                "SAM 3D Body returned no person for the contact frame.",
                "reconstructing",
                retriable=False,
            )
        out = outputs[0]
        kp3d = np.asarray(out["pred_keypoints_3d"], dtype=np.float64)
        verts = np.asarray(out["pred_vertices"], dtype=np.float64)
        if kp3d.shape != (70, 3) or np.isnan(kp3d).any() or np.isnan(verts).any():
            raise PipelineError(
                "internal_error",
                f"SAM 3D Body produced malformed output (kp3d shape {kp3d.shape}).",
                "reconstructing",
                retriable=True,
            )
        return {"kp3d": kp3d, "vertices": verts}

    def _reconstruct_keyframes(self, frames, tracks, picks, contact_pick):
        """Bounded multi-frame SAM3D: keyframes only (NOT every frame)."""
        import numpy as np

        recons: List[Optional[dict]] = []
        contact_recon: Optional[dict] = None
        for k, widx in enumerate(picks):
            try:
                rec = self._reconstruct_one(frames[widx], tracks.person[widx])
            except PipelineError:
                if k == contact_pick:
                    raise  # contact frame recon is mandatory
                recons.append(None)
                continue
            recons.append(rec)
            if k == contact_pick:
                contact_recon = rec
        assert contact_recon is not None
        # stack only the successful recons (track which pick each row is)
        rows = [(k, r["kp3d"]) for k, r in enumerate(recons) if r is not None]
        kp3_stack = np.stack([r for _, r in rows])
        stack_picks = [k for k, _ in rows]
        contact_stack_index = stack_picks.index(contact_pick)
        faces = np.asarray(self._estimator.faces)
        return contact_recon, kp3_stack, stack_picks, contact_stack_index, faces

    # ---- metric/tracking block assembly ------------------------------------

    def _assemble_metrics(
        self,
        request: CreateServeRequest,
        contact_recon: dict,
        kp3_stack,
        contact_stack_index: int,
        phases,
        ball,
        eff_fps: float,
        window_t0_ms: int,
        tracks: tracking.Tracks,
    ) -> Dict[str, Optional[dict]]:
        keypoints = _to_keypoints(contact_recon["kp3d"])
        point_map = {p.name: (p.xyz, p.score) for p in keypoints}
        metrics = build_metrics(point_map, request.handedness, self._settings.thresholds)

        # multi-frame 3D metrics (shoulder / knee / contact height)
        try:
            metrics.update(
                temporal.metrics_from_recon_stack(
                    kp3_stack, contact_stack_index, JOINT_INDEX, request.handedness
                )
            )
        except Exception:
            log.exception("recon-stack metrics failed; leaving them null")

        # dense-2D temporal metrics
        if phases is not None:
            metrics["phase_timing"] = temporal.phase_timing_block(
                phases, eff_fps, window_t0_ms
            )
            try:
                metrics["kinetic_chain_sequence"] = temporal.kinetic_chain_2d(
                    tracks.pose_xy, tracks.pose_conf, eff_fps, request.handedness, phases
                )
            except Exception:
                log.exception("kinetic chain failed; leaving it null")

        # ball-derived toss placement
        if ball is not None:
            metrics["toss_placement"] = {
                "offset_forward_cm": round(ball.offset_forward_m * 100.0, 1),
                "offset_lateral_cm": None,  # along the camera axis (single view)
                "apex_height_m": round(ball.apex_height_m, 2),
                "reference": "body_center",
            }
        return metrics

    def _tracking_block(self, ball, clip_fps: float) -> Optional[TrackingBlock]:
        if ball is None:
            return None
        to_ms = lambda f: int(round(f * 1000.0 / clip_fps))
        block = {
            "ball": {
                "points": [
                    {"t_ms": to_ms(f), "x": x, "y": y, "in_flight": fl}
                    for f, x, y, fl in zip(ball.frames, ball.cx, ball.cy, ball.in_flight)
                ],
                "apex": {
                    "t_ms": int(round(ball.apex_t_s * 1000.0)),
                    "height_m": round(ball.apex_height_m, 2),
                },
            },
            "racket": {
                "peak_speed_m_s": (
                    round(ball.racket_peak_speed_m_s, 1)
                    if ball.racket_peak_speed_m_s is not None
                    else None
                ),
                "points": [
                    {"t_ms": to_ms(f), "x": x, "y": y} for f, x, y in ball.racket_points
                ],
            },
            "contact": {
                "t_ms": int(round(ball.contact_t_s * 1000.0)),
                "height_m": round(ball.contact_height_m, 2),
            },
            "scale": {
                "px_per_m": round(ball.px_per_m, 1),
                "method": ball.scale_method,
            },
        }
        return TrackingBlock.model_validate(block)

    def _export_glb(self, vertices, faces) -> bytes:
        """Vertices+faces -> binary glTF, +Y up, meters.

        SAM 3D Body outputs camera-convention coordinates (+Y down, +Z into
        the scene); the contract/viewer are +Y up. Rotate 180° about X — the
        same transform the upstream demo Renderer applies before display —
        or every mesh renders upside down.
        """
        import numpy as np
        import trimesh

        vertices = np.asarray(vertices) * np.array([1.0, -1.0, -1.0])
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        # Neutral matte material so the client viewer gets sane shading.
        mesh.visual = trimesh.visual.TextureVisuals(
            material=trimesh.visual.material.PBRMaterial(
                baseColorFactor=[0.72, 0.72, 0.75, 1.0],
                metallicFactor=0.0,
                roughnessFactor=0.9,
                doubleSided=True,
            )
        )
        return mesh.export(file_type="glb")

    # ---- the drop-in run() -------------------------------------------------

    async def run(
        self, job_id: str, request: CreateServeRequest, report: ProgressFn
    ) -> PipelineOutput:
        timings: Dict[str, int] = {}
        t_serve0 = time.perf_counter()

        async def stage(name: str, timing_key: str, progress: float, fn=None, thread=False):
            report(name, progress)
            t0 = time.perf_counter()
            if fn is None:
                out = None
            elif thread:
                out = await asyncio.to_thread(fn)
            else:
                out = fn()
            timings[timing_key] = max(1, int((time.perf_counter() - t0) * 1000))
            return out

        clip_bytes: bytes = await stage(
            "downloading", "download", 0.05, lambda: self._download(request), thread=True
        )

        frames, frame_ids, eff_fps, frames_decoded, clip_fps = await stage(
            "decoding", "decode", 0.15, lambda: self._decode(request, clip_bytes), thread=True
        )

        # Golf = body scan only: person-detect one frame, no serve refinement,
        # no metrics/tips/tracking — just the SAM 3D reconstruction + GLB.
        golf = request.sport == "golf"

        # segmenting — SAM 3 is not used in the v1 local path; this stage now
        # carries the YOLO tracking + dense 2D pose pass (the phase-2 work).
        tracks: tracking.Tracks = await stage(
            "segmenting",
            "segment",
            0.30,
            (
                lambda: self._track_golf(
                    request, frames, frame_ids, eff_fps, clip_fps, frames_decoded
                )
            )
            if golf
            else (lambda: self._track(frames, frame_ids, eff_fps)),
            thread=True,
        )

        contact, phases, ball, picks, contact_pick = await stage(
            "selecting_keyframe",
            "keyframe",
            0.45,
            (
                lambda: self._select_keyframes_golf(
                    request, frame_ids, clip_fps, frames_decoded
                )
            )
            if golf
            else (
                lambda: self._select_keyframes(
                    request, tracks, frame_ids, eff_fps, clip_fps, frames_decoded
                )
            ),
            thread=True,
        )

        recon_out = await stage(
            "reconstructing",
            "reconstruct",
            0.55,
            lambda: self._reconstruct_keyframes(frames, tracks, picks, contact_pick),
            thread=True,
        )
        contact_recon, kp3_stack, stack_picks, contact_stack_index, faces = recon_out
        keypoints: List[Keypoint] = _to_keypoints(contact_recon["kp3d"])

        keypoints = await stage(
            "filtering", "filter", 0.76, lambda: one_euro_passthrough(keypoints)
        )

        window_t0_ms = int(round(frame_ids[0] * 1000.0 / clip_fps))
        metrics = await stage(
            "computing_metrics",
            "metrics",
            0.80,
            (lambda: {})  # golf: every metric key stays null
            if golf
            else (
                lambda: self._assemble_metrics(
                    request, contact_recon, kp3_stack, contact_stack_index,
                    phases, ball, eff_fps, window_t0_ms, tracks,
                )
            ),
        )

        tips = await stage(
            "generating_tips",
            "tips",
            0.86,
            lambda: [] if golf else generate_tips(metrics, self._settings.thresholds),
        )

        mesh_key = f"meshes/{job_id}/contact.glb"

        def upload_mesh() -> MeshInfo:
            glb_bytes = self._export_glb(contact_recon["vertices"], faces)
            self._storage.put_object(mesh_key, glb_bytes, "model/gltf-binary")
            glb_url, glb_expires_at = self._storage.mint_download(mesh_key)
            return MeshInfo(
                glb_url=glb_url,
                glb_expires_at=glb_expires_at,
                vertex_count=int(len(contact_recon["vertices"])),
                up_axis="Y",
                units="meters",
                root_translation=(0.0, 0.0, 0.0),
            )

        mesh: MeshInfo = await stage(
            "uploading_mesh", "upload_mesh", 0.95, upload_mesh, thread=True
        )

        wall_s = time.perf_counter() - t_serve0
        log.info(
            "%s %s analyzed in %.1fs (window %d frames, %d SAM3D keyframes, "
            "ball %s, phases %s)",
            request.sport, job_id, wall_s, len(frames), len(stack_picks),
            "tracked" if ball else "none", "detected" if phases else "none",
        )

        result = ServeResult(
            handedness=request.handedness,
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
            tracking=self._tracking_block(ball, clip_fps),
            tips=[Tip.model_validate(t) for t in tips],
            diagnostics=Diagnostics(
                frames_decoded=frames_decoded,
                frames_masked=0,  # SAM 3 not used in the v1 local path
                mask_coverage_at_contact=0.0,
                model_versions=ModelVersions(
                    sam3=None,  # SAM 3 not used in the v1 local path
                    sam3d_body=SAM3D_BODY_VERSION,
                    metric_engine=METRIC_ENGINE_VERSION,
                    tip_engine=TIP_ENGINE_VERSION,
                ),
                timings_ms=TimingsMs(**timings),
            ),
        )
        return PipelineOutput(result=result, mesh_object_key=mesh_key)


def _to_keypoints(kp3d) -> List[Keypoint]:
    """Serialize MHR70 keypoints, converting SAM3D's camera convention
    (+Y down) to the contract's +Y up (180° about X, matching _export_glb).
    Internal metric code keeps consuming the raw y-down arrays — angles are
    rotation-invariant, and temporal.py negates y itself."""
    return [
        Keypoint(
            index=i,
            name=SAM3D_BODY_70_JOINTS[i],
            xyz=(float(kp3d[i, 0]), -float(kp3d[i, 1]), -float(kp3d[i, 2])),
            score=DEFAULT_KEYPOINT_SCORE,
        )
        for i in range(70)
    ]
