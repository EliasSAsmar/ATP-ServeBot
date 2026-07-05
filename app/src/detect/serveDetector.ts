import { LM, type Landmarks } from "../pose/poseEngine";
import type { Handedness } from "../types/api";

/**
 * On-device serve auto-detect heuristic (UI.md §3, ARCHITECTURE.md §3 step 1).
 *
 * Watches the HITTING-WRIST VERTICAL VELOCITY and ARM ELEVATION on the
 * MediaPipe landmark stream:
 *
 *   1. ARM: a fast upward wrist movement that starts above shoulder height
 *      arms a swing (velocity threshold, in body-heights/second — normalized
 *      image coords make this roughly scale-invariant).
 *   2. PEAK: while swinging, track the wrist's highest point — the peak of
 *      the reach. When the wrist stops rising for `peakSettleMs` (or clearly
 *      descends), the peak is taken as the CONTACT moment.
 *   3. GATE: the detection is accepted only if, at the peak, the arm was
 *      elevated at least `minArmElevationDeg` (shoulder→wrist vs straight
 *      down; 180° = reaching straight up) and the wrist was above head level.
 *
 * All thresholds are in `ServeDetectorParams` (tunable in one place).
 * Everything runs on-device; no measurement is implied — this only decides
 * WHEN a serve happened, the cloud refines contact later.
 */

export interface ServeDetectorParams {
  /** Min upward wrist velocity to arm a swing, in normalized image heights/sec. */
  minUpwardVelocity: number;
  /** Min shoulder→wrist elevation at the peak, degrees (0 = down, 180 = straight up). */
  minArmElevationDeg: number;
  /** Wrist must be above the nose by this normalized margin at the peak. */
  wristAboveHeadMargin: number;
  /** How long the wrist must stop rising before the peak counts as contact. */
  peakSettleMs: number;
  /** Descent below the peak that also confirms the peak (normalized units). */
  descentConfirm: number;
  /** Refractory period after a detection. */
  cooldownMs: number;
  /** Landmark visibility gate. */
  minVisibility: number;
  /** Abort a swing that never settles within this window. */
  maxSwingMs: number;
  /** Smoothing window for the velocity estimate. */
  velocityWindowMs: number;
}

export const DEFAULT_DETECTOR_PARAMS: ServeDetectorParams = {
  minUpwardVelocity: 0.9,
  minArmElevationDeg: 130,
  wristAboveHeadMargin: 0.0,
  peakSettleMs: 160,
  descentConfirm: 0.04,
  cooldownMs: 4000,
  minVisibility: 0.5,
  maxSwingMs: 2000,
  velocityWindowMs: 120,
};

export const DETECTOR_VERSION = "serve-heuristic-1"; // matches API_CONTRACT.md §3 example

export interface ServeDetection {
  /** Contact moment (peak of the reach) in performance.now() time. */
  contactTimeMs: number;
  /** Heuristic confidence ∈ [0,1] — sent as edge_detect.contact_confidence. */
  confidence: number;
  /** Peak upward wrist velocity in px/s (needs video height). */
  peakWristVelocityPxS: number;
  /** Arm elevation at the peak, degrees. */
  armElevationDeg: number;
}

interface Sample {
  t: number;
  y: number;
}

type Phase = "idle" | "swinging" | "cooldown";

export class ServeDetector {
  params: ServeDetectorParams;
  private phase: Phase = "idle";
  private history: Sample[] = [];
  private cooldownUntil = 0;
  private swingStartT = 0;
  private peakY = 1;
  private peakT = 0;
  private peakElevationDeg = 0;
  private peakWristAboveHead = false;
  private peakUpVelocity = 0;
  /** Last reach peak (gated or not) — used to estimate contact for manual captures. */
  lastPeakTimeMs: number | null = null;

  constructor(
    private handedness: Handedness,
    params: Partial<ServeDetectorParams> = {},
  ) {
    this.params = { ...DEFAULT_DETECTOR_PARAMS, ...params };
  }

  setHandedness(h: Handedness): void {
    this.handedness = h;
    this.reset();
  }

  reset(): void {
    this.phase = "idle";
    this.history = [];
  }

  /**
   * Feed one landmark frame. Returns a detection when a serve is confirmed,
   * else null. `videoHeightPx` converts normalized velocity to px/s for the
   * edge_detect diagnostics.
   */
  update(landmarks: Landmarks | null, tMs: number, videoHeightPx: number): ServeDetection | null {
    const p = this.params;
    if (this.phase === "cooldown" && tMs >= this.cooldownUntil) this.phase = "idle";
    if (!landmarks) {
      // lost the pose — drop any in-flight swing
      if (this.phase === "swinging") this.phase = "idle";
      this.history = [];
      return null;
    }

    const wristIdx = this.handedness === "right" ? LM.right_wrist : LM.left_wrist;
    const shoulderIdx = this.handedness === "right" ? LM.right_shoulder : LM.left_shoulder;
    const wrist = landmarks[wristIdx];
    const shoulder = landmarks[shoulderIdx];
    const nose = landmarks[LM.nose];
    if (!wrist || !shoulder || !nose) return null;
    const visible =
      (wrist.visibility ?? 1) >= p.minVisibility && (shoulder.visibility ?? 1) >= p.minVisibility;

    // --- velocity over a short trailing window (y is DOWN in image coords) ---
    this.history.push({ t: tMs, y: wrist.y });
    const cutoff = tMs - p.velocityWindowMs;
    while (this.history.length > 2 && this.history[0].t < cutoff) this.history.shift();
    const first = this.history[0];
    const dt = (tMs - first.t) / 1000;
    const upVelocity = dt > 0.02 ? (first.y - wrist.y) / dt : 0; // + = moving up

    // --- arm elevation: angle of shoulder→wrist vs straight down ------------
    const vx = wrist.x - shoulder.x;
    const vy = wrist.y - shoulder.y;
    const len = Math.hypot(vx, vy) || 1;
    const cosDown = vy / len; // dot with (0, +1) i.e. down in image coords
    const elevationDeg = (Math.acos(Math.min(1, Math.max(-1, cosDown))) * 180) / Math.PI;

    if (this.phase === "idle") {
      if (visible && upVelocity >= p.minUpwardVelocity && wrist.y < shoulder.y) {
        this.phase = "swinging";
        this.swingStartT = tMs;
        this.peakY = wrist.y;
        this.peakT = tMs;
        this.peakElevationDeg = elevationDeg;
        this.peakWristAboveHead = wrist.y < nose.y - p.wristAboveHeadMargin;
        this.peakUpVelocity = upVelocity;
      }
      return null;
    }

    if (this.phase === "swinging") {
      this.peakUpVelocity = Math.max(this.peakUpVelocity, upVelocity);
      if (wrist.y < this.peakY) {
        this.peakY = wrist.y;
        this.peakT = tMs;
        this.peakElevationDeg = elevationDeg;
        this.peakWristAboveHead = wrist.y < nose.y - p.wristAboveHeadMargin;
      }

      const settled = tMs - this.peakT >= p.peakSettleMs;
      const descended = wrist.y > this.peakY + p.descentConfirm;
      const expired = tMs - this.swingStartT > p.maxSwingMs;

      if (settled || descended || expired) {
        this.lastPeakTimeMs = this.peakT;
        const accepted =
          this.peakElevationDeg >= p.minArmElevationDeg && this.peakWristAboveHead && !expired;
        if (accepted) {
          this.phase = "cooldown";
          this.cooldownUntil = tMs + p.cooldownMs;
          return {
            contactTimeMs: this.peakT,
            confidence: this.confidence(),
            peakWristVelocityPxS: this.peakUpVelocity * videoHeightPx,
            armElevationDeg: round1(this.peakElevationDeg),
          };
        }
        this.phase = "idle";
      }
      return null;
    }

    return null; // cooldown
  }

  private confidence(): number {
    const p = this.params;
    // velocity margin: at 2× threshold → full credit
    const vel = Math.min(1, this.peakUpVelocity / (2 * p.minUpwardVelocity));
    // elevation margin: from the gate up to fully overhead
    const elev = Math.min(
      1,
      Math.max(0, (this.peakElevationDeg - p.minArmElevationDeg) / (178 - p.minArmElevationDeg) + 0.3),
    );
    return Math.round(Math.min(1, 0.55 * vel + 0.45 * elev) * 100) / 100;
  }
}

function round1(x: number): number {
  return Math.round(x * 10) / 10;
}
