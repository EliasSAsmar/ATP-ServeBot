import { LM, type Landmarks } from "../pose/poseEngine";
import type { ServeDetection } from "./serveDetector";

/**
 * On-device golf-swing auto-detect heuristic (companion to ServeDetector).
 *
 * A golf swing looks nothing like a serve on the landmark stream, so this
 * watches the WRIST-MIDPOINT SPEED with a hands-on-club gate:
 *
 *   1. ARM: both wrists moving fast while CLOSE TOGETHER (hands gripping the
 *      club) arms a swing — walking/gesturing swings the arms in opposition,
 *      so the together-gate kills most false positives.
 *   2. PEAK: while swinging, track the maximum hand speed — for a real swing
 *      that is the downswing whipping through impact. Accumulate the total
 *      path the hands travel (a swing sweeps a long arc; jitter doesn't).
 *   3. CONFIRM: when the speed collapses below half of the peak for
 *      `peakSettleMs` (the follow-through decelerating into the finish), the
 *      peak-speed moment is taken as IMPACT and the detection fires if the
 *      peak speed, arc length, and hands-together gates all pass.
 *
 * Speeds are in normalized image heights/second (scale-invariant); the
 * detection reuses the ServeDetection shape so capture/upload paths are
 * shared with tennis. Handedness is irrelevant — both hands hold the club.
 */

export interface GolfDetectorParams {
  /** Min hand speed to arm a swing, normalized image heights/sec. */
  minArmSpeed: number;
  /** Min peak hand speed for a confirmed swing (downswing through impact). */
  minPeakSpeed: number;
  /** Hands count as "on the club" when wrist separation ≤ factor × shoulder width. */
  handsTogetherFactor: number;
  /** Min total wrist-midpoint travel over the swing, normalized units. */
  minTravel: number;
  /** How long the speed must stay collapsed after the peak to confirm. */
  peakSettleMs: number;
  /** Refractory period after a detection. */
  cooldownMs: number;
  /** Landmark visibility gate. */
  minVisibility: number;
  /** Abort a swing that never settles within this window. */
  maxSwingMs: number;
  /** Smoothing window for the speed estimate. */
  velocityWindowMs: number;
}

export const DEFAULT_GOLF_PARAMS: GolfDetectorParams = {
  minArmSpeed: 1.1,
  minPeakSpeed: 1.8,
  handsTogetherFactor: 0.9,
  minTravel: 0.35,
  peakSettleMs: 220,
  cooldownMs: 4000,
  minVisibility: 0.5,
  maxSwingMs: 2500,
  velocityWindowMs: 100,
};

export const GOLF_DETECTOR_VERSION = "golf-swing-heuristic-1";

interface Sample {
  t: number;
  x: number;
  y: number;
}

type Phase = "idle" | "swinging" | "cooldown";

export class GolfSwingDetector {
  params: GolfDetectorParams;
  private phase: Phase = "idle";
  private history: Sample[] = [];
  private cooldownUntil = 0;
  private swingStartT = 0;
  private travel = 0;
  private prev: Sample | null = null;
  private peakSpeed = 0;
  private peakT = 0;
  private peakTogether = false;
  private peakElevationDeg = 0;
  /** Last swing peak (gated or not) — contact estimate for manual captures. */
  lastPeakTimeMs: number | null = null;

  constructor(params: Partial<GolfDetectorParams> = {}) {
    this.params = { ...DEFAULT_GOLF_PARAMS, ...params };
  }

  reset(): void {
    this.phase = "idle";
    this.history = [];
    this.prev = null;
  }

  /**
   * Feed one landmark frame. Returns a detection (impact = peak-speed moment)
   * when a swing is confirmed, else null.
   */
  update(landmarks: Landmarks | null, tMs: number, videoHeightPx: number): ServeDetection | null {
    const p = this.params;
    if (this.phase === "cooldown" && tMs >= this.cooldownUntil) this.phase = "idle";
    if (!landmarks) {
      if (this.phase === "swinging") this.phase = "idle";
      this.history = [];
      this.prev = null;
      return null;
    }

    const lw = landmarks[LM.left_wrist];
    const rw = landmarks[LM.right_wrist];
    const ls = landmarks[LM.left_shoulder];
    const rs = landmarks[LM.right_shoulder];
    if (!lw || !rw || !ls || !rs) return null;
    const visible =
      (lw.visibility ?? 1) >= p.minVisibility && (rw.visibility ?? 1) >= p.minVisibility;

    // --- hands midpoint + on-the-club gate ---------------------------------
    const cur: Sample = { t: tMs, x: (lw.x + rw.x) / 2, y: (lw.y + rw.y) / 2 };
    const handsSep = Math.hypot(lw.x - rw.x, lw.y - rw.y);
    const shoulderW = Math.hypot(ls.x - rs.x, ls.y - rs.y) || 0.1;
    const together = handsSep <= p.handsTogetherFactor * shoulderW;

    // --- speed over a short trailing window --------------------------------
    this.history.push(cur);
    const cutoff = tMs - p.velocityWindowMs;
    while (this.history.length > 2 && this.history[0].t < cutoff) this.history.shift();
    const first = this.history[0];
    const dt = (tMs - first.t) / 1000;
    const speed = dt > 0.02 ? Math.hypot(cur.x - first.x, cur.y - first.y) / dt : 0;

    // --- elevation of shoulders-mid → hands-mid (diagnostics only) ---------
    const sx = (ls.x + rs.x) / 2;
    const sy = (ls.y + rs.y) / 2;
    const len = Math.hypot(cur.x - sx, cur.y - sy) || 1;
    const cosDown = (cur.y - sy) / len; // y is DOWN in image coords
    const elevationDeg = (Math.acos(Math.min(1, Math.max(-1, cosDown))) * 180) / Math.PI;

    if (this.phase === "idle") {
      if (visible && together && speed >= p.minArmSpeed) {
        this.phase = "swinging";
        this.swingStartT = tMs;
        this.travel = 0;
        this.prev = cur;
        this.peakSpeed = speed;
        this.peakT = tMs;
        this.peakTogether = together;
        this.peakElevationDeg = elevationDeg;
      }
      return null;
    }

    if (this.phase === "swinging") {
      if (this.prev) this.travel += Math.hypot(cur.x - this.prev.x, cur.y - this.prev.y);
      this.prev = cur;
      if (speed > this.peakSpeed) {
        this.peakSpeed = speed;
        this.peakT = tMs;
        this.peakTogether = together;
        this.peakElevationDeg = elevationDeg;
      }

      const settled = tMs - this.peakT >= p.peakSettleMs && speed < 0.5 * this.peakSpeed;
      const expired = tMs - this.swingStartT > p.maxSwingMs;

      if (settled || expired) {
        this.lastPeakTimeMs = this.peakT;
        const accepted =
          !expired &&
          this.peakSpeed >= p.minPeakSpeed &&
          this.travel >= p.minTravel &&
          this.peakTogether;
        if (accepted) {
          this.phase = "cooldown";
          this.cooldownUntil = tMs + p.cooldownMs;
          return {
            contactTimeMs: this.peakT,
            confidence: this.confidence(),
            peakWristVelocityPxS: this.peakSpeed * videoHeightPx,
            armElevationDeg: Math.round(this.peakElevationDeg * 10) / 10,
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
    // speed margin: at 2× threshold → full credit; arc margin likewise
    const vel = Math.min(1, this.peakSpeed / (2 * p.minPeakSpeed));
    const arc = Math.min(1, this.travel / (2 * p.minTravel));
    return Math.round(Math.min(1, 0.6 * vel + 0.4 * arc) * 100) / 100;
  }
}
