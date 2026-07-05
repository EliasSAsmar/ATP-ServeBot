import type {
  CreateServeRequest,
  CreateServeResponse,
  ElbowBand,
  HealthResponse,
  JobResponse,
  JobStage,
  Keypoint3D,
  ServeResult,
  Tip,
  UploadRequest,
  UploadResponse,
} from "../types/api";
import { JOB_STAGES } from "../types/api";
import { ApiError, type ServeApi } from "./types";
import mockGlbUrl from "../assets/mock-contact.glb?url";

/**
 * In-browser mock backend implementing the exact API_CONTRACT.md §6 sequence
 * (uploads → PUT → serves → queued/running/succeeded polling → GLB) with
 * canned-but-schema-valid payloads, so the full UI is demonstrable with no
 * backend at all. The "GLB" is a bundled low-poly humanoid placeholder.
 *
 * The mock's metric is computed from its canned keypoints with the real
 * METRICS.md §1 elbow formula, and the tip comes from the real §9.2 rule
 * table, so every number on screen is self-consistent.
 */

const QUEUED_MS = 1200; // time spent "queued"
const STAGE_MS = 650; // time per pipeline stage while "running"
const GLB_TTL_MS = 15 * 60 * 1000; // contract default: 15 min presigned GET

interface MockJob {
  jobId: string;
  createdAtMs: number; // performance-independent wall clock
  request: CreateServeRequest;
}

function uuid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-8xxx-xxxxxxxxxxxx".replace(/x/g, () => ((Math.random() * 16) | 0).toString(16));
}

function iso(ms: number): string {
  return new Date(ms).toISOString();
}

// --- METRICS.md §1: angle at joint ------------------------------------------
type Vec3 = [number, number, number];

function angleAtJoint(A: Vec3, J: Vec3, C: Vec3): number {
  const u: Vec3 = [A[0] - J[0], A[1] - J[1], A[2] - J[2]];
  const w: Vec3 = [C[0] - J[0], C[1] - J[1], C[2] - J[2]];
  const dot = u[0] * w[0] + u[1] * w[1] + u[2] * w[2];
  const nu = Math.hypot(...u);
  const nw = Math.hypot(...w);
  const cos = Math.min(1, Math.max(-1, dot / (nu * nw)));
  return (Math.acos(cos) * 180) / Math.PI;
}

// METRICS.md §1 bands
function elbowBand(value: number): ElbowBand {
  if (value >= 165) return "straight";
  if (value >= 150) return "nearly_straight";
  if (value >= 120) return "slightly_bent";
  if (value >= 90) return "bent";
  return "very_bent";
}

// METRICS.md §9.2 — evaluated in order, first match wins
function elbowTip(value: number): Tip {
  const v = Math.round(value * 10) / 10;
  const shown = `~${Number.isInteger(v) ? v.toFixed(0) : v.toFixed(1)}°`;
  if (v >= 150) {
    return {
      id: "elbow_good_extension",
      metric: "elbow_angle_deg",
      severity: "info",
      title: "Good extension",
      message: `Nice — your hitting arm was well extended at contact (${shown}). Keep reaching up through the ball.`,
      triggered_by: { value: v, threshold: 150.0, comparator: "gte" },
    };
  }
  if (v >= 120) {
    return {
      id: "elbow_slightly_bent",
      metric: "elbow_angle_deg",
      severity: "suggestion",
      title: "Reach a little higher",
      message: `Your hitting elbow was slightly bent at contact (${shown}). Try reaching to a straighter arm for more height and easier power.`,
      triggered_by: { value: v, threshold: 150.0, comparator: "lt" },
    };
  }
  return {
    id: "elbow_too_bent",
    metric: "elbow_angle_deg",
    severity: "suggestion",
    title: "Extend at contact",
    message: `Your hitting elbow was quite bent at contact (${shown}). Focus on hitting up and out to a straighter arm at contact.`,
    triggered_by: { value: v, threshold: 120.0, comparator: "lt" },
  };
}

// --- canned 70-keypoint set ---------------------------------------------------
// Serving-side arm keypoints placed to yield a "slightly bent" elbow (~140°),
// which exercises the suggestion-severity tip path in the UI.
function buildKeypoints(handedness: "right" | "left"): Keypoint3D[] {
  const mirror = handedness === "left" ? -1 : 1;
  const side = handedness;
  const other = handedness === "right" ? "left" : "right";

  const named: Record<number, { name: string; xyz: Vec3; score: number }> = {
    0: { name: "nose", xyz: [0.01 * mirror, 1.58, 0.08], score: 0.95 },
    11: { name: `${other}_shoulder`, xyz: [-0.17 * mirror, 1.4, 0.01], score: 0.92 },
    12: { name: `${side}_shoulder`, xyz: [0.17 * mirror, 1.41, -0.02], score: 0.94 },
    13: { name: `${other}_elbow`, xyz: [-0.3 * mirror, 1.14, 0.06], score: 0.88 },
    14: { name: `${side}_elbow`, xyz: [0.34 * mirror, 1.62, -0.02], score: 0.9 },
    15: { name: `${other}_wrist`, xyz: [-0.36 * mirror, 0.92, 0.1], score: 0.84 },
    16: { name: `${side}_wrist`, xyz: [0.335 * mirror, 1.9, -0.02], score: 0.86 },
    23: { name: `${other}_hip`, xyz: [-0.1 * mirror, 0.95, 0.0], score: 0.9 },
    24: { name: `${side}_hip`, xyz: [0.1 * mirror, 0.95, 0.0], score: 0.91 },
    25: { name: `${other}_knee`, xyz: [-0.11 * mirror, 0.52, 0.01], score: 0.87 },
    26: { name: `${side}_knee`, xyz: [0.12 * mirror, 0.5, -0.01], score: 0.88 },
    27: { name: `${other}_ankle`, xyz: [-0.12 * mirror, 0.07, 0.02], score: 0.85 },
    28: { name: `${side}_ankle`, xyz: [0.14 * mirror, 0.06, -0.02], score: 0.86 },
  };

  const points: Keypoint3D[] = [];
  for (let i = 0; i < 70; i++) {
    if (named[i]) {
      points.push({ index: i, name: named[i].name, xyz: named[i].xyz, score: named[i].score });
    } else {
      // deterministic filler points inside the body volume
      const t = i / 70;
      points.push({
        index: i,
        name: `aux_${i}`,
        xyz: [Math.sin(i * 2.399) * 0.14, 0.15 + t * 1.4, Math.cos(i * 1.703) * 0.08],
        score: 0.55 + 0.35 * Math.abs(Math.sin(i * 0.917)),
      });
    }
  }
  return points;
}

function buildResult(req: CreateServeRequest, nowMs: number): ServeResult {
  const points = buildKeypoints(req.handedness);
  const side = req.handedness;
  const byName = (n: string) => points.find((p) => p.name === n)!;
  const S = byName(`${side}_shoulder`);
  const E = byName(`${side}_elbow`);
  const W = byName(`${side}_wrist`);

  const raw = angleAtJoint(S.xyz, E.xyz, W.xyz);
  const value = Math.round(raw * 10) / 10; // 1 decimal max (METRICS.md §0)
  const confidence = Math.min(S.score, E.score, W.score); // METRICS.md §0 [CONFIRM min]

  const refinedTs = Math.min(req.clip.duration_ms, req.contact_timestamp_ms + 27);
  const fps = req.clip.fps || 30;

  return {
    schema_version: "serve-result-1",
    handedness: req.handedness,
    contact: {
      edge_timestamp_ms: req.contact_timestamp_ms,
      refined_timestamp_ms: refinedTs,
      refined_frame_index: Math.round((refinedTs / 1000) * fps),
      contact_confidence: 0.68,
      refine_window_ms: 200,
    },
    keyframes: [
      {
        role: "contact",
        timestamp_ms: refinedTs,
        frame_index: Math.round((refinedTs / 1000) * fps),
        mesh: {
          // Bundled placeholder GLB; fake presigned-looking query string.
          glb_url: `${mockGlbUrl}?X-Mock-Signature=1&expires=${nowMs + GLB_TTL_MS}`,
          glb_expires_at: iso(nowMs + GLB_TTL_MS),
          vertex_count: 192,
          up_axis: "Y",
          units: "meters",
          root_translation: [0, 0, 0],
        },
        keypoints_3d: { format: "sam3d-body-70", count: 70, units: "meters", points },
      },
    ],
    metrics: {
      elbow_angle_deg: {
        value,
        unit: "degree",
        side,
        joints: [`${side}_shoulder`, `${side}_elbow`, `${side}_wrist`],
        keyframe_role: "contact",
        confidence,
        band: elbowBand(value),
        reference_range_deg: [150.0, 180.0],
      },
      shoulder_angle_deg: null,
      knee_flexion_deg: null,
      kinetic_chain_sequence: null,
      toss_placement: null,
      toss_consistency: null,
      contact_height: null,
      phase_timing: null,
    },
    tips: confidence >= 0.5 && value !== null ? [elbowTip(value)] : [],
    diagnostics: {
      frames_decoded: Math.round((req.clip.duration_ms / 1000) * fps),
      frames_masked: Math.round((req.clip.duration_ms / 1000) * fps),
      mask_coverage_at_contact: 0.83,
      model_versions: {
        sam3: "mock",
        sam3d_body: "mock",
        metric_engine: "metrics-1",
        tip_engine: "tips-1",
      },
      timings_ms: { download: 0, decode: 0, segment: 0, keyframe: 0, reconstruct: 0, filter: 0, metrics: 0, tips: 0, upload_mesh: 0 },
    },
  };
}

export class MockServeApi implements ServeApi {
  private uploads = new Map<string, { meta: UploadRequest; uploaded: boolean }>();
  private jobs = new Map<string, MockJob>();

  async health(): Promise<HealthResponse> {
    await delay(180); // feel like a network call
    return {
      status: "ok",
      instance_up: true,
      models_ready: true,
      models: {
        sam3: { loaded: true, checkpoint: "mock" },
        sam3d_body: { loaded: true, checkpoint: "mock" },
      },
      gpu: { name: "Mock A10G", vram_total_mb: 23028, vram_used_mb: 8123 },
      queue_depth: 0,
      server_time: iso(Date.now()),
      api_version: "v1",
    };
  }

  async createUpload(req: UploadRequest): Promise<UploadResponse> {
    await delay(150);
    const now = new Date();
    const ext = req.content_type === "video/mp4" ? "mp4" : "webm";
    const key = `clips/${now.getUTCFullYear()}/${String(now.getUTCMonth() + 1).padStart(2, "0")}/${String(now.getUTCDate()).padStart(2, "0")}/${uuid()}.${ext}`;
    this.uploads.set(key, { meta: req, uploaded: false });
    return {
      object_key: key,
      upload_url: `mock://s3/${key}`,
      upload_method: "PUT",
      upload_headers: { "Content-Type": req.content_type },
      expires_at: iso(Date.now() + 5 * 60 * 1000),
    };
  }

  async putClip(
    upload: UploadResponse,
    _clip: Blob,
    onProgress?: (fraction: number) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const key = upload.upload_url.replace("mock://s3/", "");
    const entry = this.uploads.get(key);
    if (!entry) {
      throw new ApiError({ code: "invalid_object_key", message: "Unknown mock upload URL", httpStatus: 400 });
    }
    // simulate a chunked upload with visible progress
    const steps = 8;
    for (let i = 1; i <= steps; i++) {
      await delay(90);
      if (signal?.aborted) throw new DOMException("Upload cancelled", "AbortError");
      onProgress?.(i / steps);
    }
    entry.uploaded = true;
  }

  async createServe(req: CreateServeRequest): Promise<CreateServeResponse> {
    await delay(160);
    if (req.handedness !== "right" && req.handedness !== "left") {
      throw new ApiError({
        code: "invalid_handedness",
        message: "handedness must be one of: right, left",
        httpStatus: 400,
        field: "handedness",
      });
    }
    if (req.contact_timestamp_ms < 0 || req.contact_timestamp_ms > req.clip.duration_ms) {
      throw new ApiError({
        code: "invalid_timestamp",
        message: "contact_timestamp_ms must be within [0, duration_ms]",
        httpStatus: 400,
        field: "contact_timestamp_ms",
      });
    }
    const upload = this.uploads.get(req.object_key);
    if (!upload) {
      throw new ApiError({ code: "invalid_object_key", message: "object_key was not minted by this API", httpStatus: 400, field: "object_key" });
    }
    if (!upload.uploaded) {
      throw new ApiError({ code: "clip_not_found", message: "No uploaded object for object_key yet", httpStatus: 409 });
    }
    const jobId = uuid();
    const createdAtMs = Date.now();
    this.jobs.set(jobId, { jobId, createdAtMs, request: req });
    return {
      job_id: jobId,
      status: "queued",
      created_at: iso(createdAtMs),
      poll_url: `/v1/serves/${jobId}`,
      poll_after_ms: 800,
    };
  }

  async getServe(jobId: string): Promise<JobResponse> {
    await delay(120);
    const job = this.jobs.get(jobId);
    if (!job) {
      throw new ApiError({ code: "job_not_found", message: `Unknown job_id ${jobId}`, httpStatus: 404 });
    }
    const now = Date.now();
    const elapsed = now - job.createdAtMs;
    const base = {
      job_id: job.jobId,
      created_at: iso(job.createdAtMs),
    };

    if (elapsed < QUEUED_MS) {
      return {
        ...base,
        status: "queued",
        stage: null,
        progress: 0,
        started_at: null,
        finished_at: null,
        poll_after_ms: 800,
        result: null,
        error: null,
      };
    }

    const runElapsed = elapsed - QUEUED_MS;
    const stageIndex = Math.floor(runElapsed / STAGE_MS);
    if (stageIndex < JOB_STAGES.length) {
      const stage: JobStage = JOB_STAGES[stageIndex];
      return {
        ...base,
        status: "running",
        stage,
        progress: Math.min(0.99, (stageIndex + (runElapsed % STAGE_MS) / STAGE_MS) / JOB_STAGES.length),
        started_at: iso(job.createdAtMs + QUEUED_MS),
        finished_at: null,
        poll_after_ms: 700,
        result: null,
        error: null,
      };
    }

    const finishedAt = job.createdAtMs + QUEUED_MS + JOB_STAGES.length * STAGE_MS;
    return {
      ...base,
      status: "succeeded",
      stage: null,
      progress: 1,
      started_at: iso(job.createdAtMs + QUEUED_MS),
      finished_at: iso(finishedAt),
      poll_after_ms: null,
      // Rebuilt per poll so glb_expires_at is always fresh — this is what makes
      // the client's mesh-expired → re-poll refresh path work against the mock.
      result: buildResult(job.request, now),
      error: null,
    };
  }

  async fetchGlb(glbUrl: string): Promise<ArrayBuffer> {
    const clean = glbUrl.split("?")[0];
    const res = await fetch(clean);
    if (!res.ok) {
      throw new ApiError({ code: "mesh_fetch_failed", message: `Mock mesh fetch failed (${res.status})`, httpStatus: res.status });
    }
    return res.arrayBuffer();
  }
}

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
