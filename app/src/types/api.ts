/**
 * TypeScript mirror of design/API_CONTRACT.md — the authoritative interface.
 * Do not invent shapes here; every field maps 1:1 to the contract.
 */

export type Handedness = "right" | "left";

export type ClipContentType = "video/webm" | "video/mp4";

// ---------------------------------------------------------------------------
// §1 GET /v1/health
// ---------------------------------------------------------------------------

export interface HealthResponse {
  status: string; // "ok" | "starting"
  instance_up: boolean;
  models_ready: boolean;
  models?: Record<string, { loaded: boolean; checkpoint: string }>;
  gpu?: { name: string; vram_total_mb: number; vram_used_mb: number };
  queue_depth?: number;
  server_time?: string;
  api_version?: string;
}

// ---------------------------------------------------------------------------
// §2 POST /v1/uploads
// ---------------------------------------------------------------------------

export interface UploadRequest {
  content_type: ClipContentType;
  byte_size: number;
  duration_ms: number;
  fps: number;
  width: number;
  height: number;
}

export interface UploadResponse {
  object_key: string;
  upload_url: string;
  upload_method: "PUT";
  upload_headers: Record<string, string>;
  expires_at: string;
}

// ---------------------------------------------------------------------------
// §3 POST /v1/serves
// ---------------------------------------------------------------------------

export interface ClipMeta {
  duration_ms: number;
  fps: number;
  width: number;
  height: number;
  content_type: ClipContentType;
}

export interface EdgeDetectDiagnostics {
  detector_version: string;
  contact_confidence: number; // [0,1]
  peak_wrist_velocity_px_s: number;
  arm_elevation_deg_at_contact: number;
}

export interface CreateServeRequest {
  object_key: string;
  handedness: Handedness;
  contact_timestamp_ms: number;
  clip: ClipMeta;
  edge_detect?: EdgeDetectDiagnostics;
  client?: { app_version: string; platform: string; user_label?: string };
}

export interface CreateServeResponse {
  job_id: string;
  status: "queued";
  created_at: string;
  poll_url: string;
  poll_after_ms: number;
}

// ---------------------------------------------------------------------------
// §4 GET /v1/serves/{job_id}
// ---------------------------------------------------------------------------

export type JobStatus = "queued" | "running" | "succeeded" | "failed";

export type JobStage =
  | "downloading"
  | "decoding"
  | "segmenting"
  | "selecting_keyframe"
  | "reconstructing"
  | "filtering"
  | "computing_metrics"
  | "generating_tips"
  | "uploading_mesh";

export const JOB_STAGES: JobStage[] = [
  "downloading",
  "decoding",
  "segmenting",
  "selecting_keyframe",
  "reconstructing",
  "filtering",
  "computing_metrics",
  "generating_tips",
  "uploading_mesh",
];

export interface JobError {
  code: string;
  message: string;
  stage: string | null;
  retriable: boolean;
}

export interface Keypoint3D {
  index: number;
  name: string;
  xyz: [number, number, number]; // meters, model space, +Y up
  score: number; // [0,1]
}

export interface KeyframeMesh {
  glb_url: string;
  glb_expires_at: string;
  vertex_count: number;
  up_axis: "Y" | string;
  units: "meters" | string;
  root_translation: [number, number, number];
}

export interface Keyframe {
  role: "contact";
  timestamp_ms: number;
  frame_index: number;
  mesh: KeyframeMesh;
  keypoints_3d: {
    format: string;
    count: number;
    units: string;
    points: Keypoint3D[];
  };
}

export type ElbowBand = "straight" | "nearly_straight" | "slightly_bent" | "bent" | "very_bent";

/**
 * Implemented metric object. Contract nullability rule (§4c):
 *  - metric key === null            → not implemented yet ("coming soon"/hidden)
 *  - object with value === null    → implemented but failed for this clip
 *    (carries compute_error + missing)
 */
export interface MetricValue {
  value: number | null;
  unit: string;
  side?: Handedness;
  joints?: string[];
  keyframe_role?: string;
  confidence: number;
  band?: ElbowBand;
  reference_range_deg?: [number, number];
  compute_error?: string;
  missing?: string[];
}

/**
 * Phase-2 angle metric (shoulder / knee). Same shape as MetricValue but the
 * band vocabulary is metric-specific, so it is typed as an open string.
 * reference_range_deg is present for shoulder, absent for knee.
 */
export interface AngleMetric {
  value: number | null;
  unit: string; // "degree"
  side?: string;
  band?: string;
  confidence: number;
  reference_range_deg?: [number, number];
  compute_error?: string;
  missing?: string[];
}

/** Contact height: wrist height at contact as a ratio of standing height. */
export interface ContactHeightMetric {
  value: number | null;
  unit: string; // "ratio"
  wrist_y_m?: number;
  standing_height_m?: number;
  compute_error?: string;
  missing?: string[];
}

/** Serve phase durations in ms (windup → trophy → acceleration → follow_through). */
export interface PhaseTimingMetric {
  unit: string; // "ms"
  contact_ms: number;
  phases: {
    windup?: number;
    trophy?: number;
    acceleration?: number;
    follow_through?: number;
  };
}

/** Kinetic chain: per-segment peak angular-velocity times (proximal → distal). */
export interface KineticChainSequence {
  segments: string[]; // e.g. ["pelvis","trunk","upper_arm","forearm","hand"]
  peak_times_ms: Record<string, number>;
  peak_deg_s: Record<string, number>;
  order_correct: boolean;
  gaps_ms: number[];
  note?: string;
}

/** Toss placement relative to a body reference (e.g. front foot). */
export interface TossPlacement {
  offset_forward_cm: number;
  offset_lateral_cm: number | null;
  apex_height_m: number;
  reference: string;
}

export interface ServeMetrics {
  elbow_angle_deg: MetricValue | null;
  shoulder_angle_deg: AngleMetric | null;
  knee_flexion_deg: AngleMetric | null;
  kinetic_chain_sequence: KineticChainSequence | null;
  toss_placement: TossPlacement | null;
  toss_consistency: null; // not implemented yet (needs multiple serves)
  contact_height: ContactHeightMetric | null;
  phase_timing: PhaseTimingMetric | null;
}

// ---------------------------------------------------------------------------
// §4d result.tracking — 2D ball / racket tracks (phase 2)
// ---------------------------------------------------------------------------

export interface BallTrackPoint {
  t_ms: number;
  x: number; // px, image space
  y: number; // px, image space (+y down)
  in_flight: boolean;
}

export interface RacketTrackPoint {
  t_ms: number;
  x: number;
  y: number;
}

export interface ServeTracking {
  ball: {
    points: BallTrackPoint[];
    apex: { t_ms: number; height_m: number };
  };
  racket: {
    peak_speed_m_s: number;
    points: RacketTrackPoint[];
  };
  contact: { t_ms: number; height_m: number };
  scale: { px_per_m: number; method: string };
}

export type TipSeverity = "info" | "suggestion" | "flag";

export interface Tip {
  id: string;
  metric: string;
  severity: TipSeverity;
  title: string;
  message: string;
  triggered_by?: { value: number; threshold: number; comparator: string };
}

export interface ServeResult {
  schema_version: string; // "serve-result-1"
  handedness: Handedness;
  contact: {
    edge_timestamp_ms: number;
    refined_timestamp_ms: number;
    refined_frame_index: number;
    contact_confidence: number;
    refine_window_ms: number;
  };
  keyframes: Keyframe[];
  metrics: ServeMetrics;
  /** 2D ball/racket tracking (phase 2). Absent or null when not computed. */
  tracking?: ServeTracking | null;
  tips: Tip[];
  diagnostics?: Record<string, unknown>;
}

export interface JobResponse {
  job_id: string;
  status: JobStatus;
  stage: JobStage | null;
  progress: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  poll_after_ms: number | null;
  result: ServeResult | null;
  error: JobError | null;
}

// ---------------------------------------------------------------------------
// §0 standard error envelope
// ---------------------------------------------------------------------------

export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    field: string | null;
    request_id: string;
  };
}
