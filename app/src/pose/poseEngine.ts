import { FilesetResolver, PoseLandmarker, type NormalizedLandmark } from "@mediapipe/tasks-vision";

/**
 * Thin wrapper around MediaPipe Tasks Pose Landmarker (on-device, ~30fps).
 * Loads the WASM runtime + model from local /public assets first (edge
 * independence — no CDN needed), with a CDN fallback for the model file.
 */

const LOCAL_WASM_PATH = `${import.meta.env.BASE_URL}mediapipe/wasm`;
const LOCAL_MODEL_URL = `${import.meta.env.BASE_URL}models/pose_landmarker_lite.task`;
const CDN_MODEL_URL =
  "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task";

// MediaPipe Pose landmark indices used by the app.
export const LM = {
  nose: 0,
  left_shoulder: 11,
  right_shoulder: 12,
  left_elbow: 13,
  right_elbow: 14,
  left_wrist: 15,
  right_wrist: 16,
  left_hip: 23,
  right_hip: 24,
  left_knee: 25,
  right_knee: 26,
  left_ankle: 27,
  right_ankle: 28,
} as const;

export type Landmarks = NormalizedLandmark[];

export interface PoseFrame {
  landmarks: Landmarks | null; // null → no pose in frame
  timestampMs: number; // performance.now() domain
}

export class PoseEngine {
  private landmarker: PoseLandmarker | null = null;
  private lastVideoTime = -1;

  async init(): Promise<void> {
    const fileset = await FilesetResolver.forVisionTasks(LOCAL_WASM_PATH);
    const options = {
      numPoses: 1,
      runningMode: "VIDEO" as const,
      baseOptions: { modelAssetPath: LOCAL_MODEL_URL, delegate: "GPU" as const },
    };
    try {
      this.landmarker = await PoseLandmarker.createFromOptions(fileset, options);
    } catch {
      // Local model missing (fetch-pose-model not run) or GPU delegate failed —
      // retry with the CDN model on CPU before giving up.
      this.landmarker = await PoseLandmarker.createFromOptions(fileset, {
        ...options,
        baseOptions: { modelAssetPath: CDN_MODEL_URL, delegate: "CPU" },
      });
    }
  }

  /** Detect on the current video frame. Returns null when the frame was already processed. */
  detect(video: HTMLVideoElement, nowMs: number): PoseFrame | null {
    if (!this.landmarker || video.readyState < 2) return null;
    if (video.currentTime === this.lastVideoTime) return null;
    this.lastVideoTime = video.currentTime;
    const result = this.landmarker.detectForVideo(video, nowMs);
    const landmarks = result.landmarks && result.landmarks.length > 0 ? result.landmarks[0] : null;
    return { landmarks, timestampMs: nowMs };
  }

  close(): void {
    this.landmarker?.close();
    this.landmarker = null;
  }
}
