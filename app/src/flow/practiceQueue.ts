import type { ServeApi } from "../api";
import type { Handedness, JobResponse, Sport } from "../types/api";
import { AnalysisRun, type AnalysisPhase, type CapturedClip } from "./analysis";

/**
 * Practice-session queue: the player fires serve after serve (or swing after
 * swing); every capture is enqueued locally and analyzed one at a time in the
 * background (the server runs one job at a time anyway), so capturing is
 * never blocked by analysis.
 *
 * Module-level singleton — it must outlive the Live screen so an in-flight
 * session keeps processing while the user browses results. Clip blobs are
 * disk-backed by the browser, so a long session doesn't sit in RAM.
 */

export type PracticeItemPhase = { kind: "waiting" } | AnalysisPhase;

export interface PracticeItem {
  id: number;
  /** 1-based serve/swing number within this session. */
  seq: number;
  capturedAt: number; // Date.now()
  sport: Sport;
  clip: CapturedClip;
  phase: PracticeItemPhase;
  /** Set when analysis succeeded. */
  job: JobResponse | null;
}

interface QueueContext {
  api: ServeApi;
  handedness: Handedness;
  sport: Sport;
}

/** Safety cap — beyond this, new captures are rejected until the queue drains. */
export const MAX_QUEUED = 200;

type Listener = () => void;

class PracticeQueue {
  private items: PracticeItem[] = [];
  private nextId = 1;
  private pumping = false;
  private context: QueueContext | null = null;
  private listeners = new Set<Listener>();
  /** Stable snapshot for useSyncExternalStore (rebuilt on every change). */
  private snapshot: PracticeItem[] = [];

  /** UI keeps this fresh so mid-session settings edits apply to later serves. */
  setContext(api: ServeApi, handedness: Handedness, sport: Sport): void {
    this.context = { api, handedness, sport };
  }

  /** Returns false when the queue is at capacity (capture should warn). */
  enqueue(clip: CapturedClip, sport: Sport): boolean {
    if (this.pendingCount() >= MAX_QUEUED) return false;
    this.items.push({
      id: this.nextId++,
      seq: this.items.length + 1,
      capturedAt: clip.capturedAt,
      sport,
      clip,
      phase: { kind: "waiting" },
      job: null,
    });
    this.emit();
    void this.pump();
    return true;
  }

  getItem(id: number): PracticeItem | undefined {
    return this.items.find((i) => i.id === id);
  }

  /** Drop finished/failed items; in-flight and waiting ones stay. */
  clearFinished(): void {
    this.items = this.items.filter(
      (i) => !(i.phase.kind === "succeeded" || i.phase.kind === "failed" || i.phase.kind === "cancelled"),
    );
    this.emit();
  }

  subscribe = (fn: Listener): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  getSnapshot = (): PracticeItem[] => this.snapshot;

  private pendingCount(): number {
    return this.items.filter(
      (i) => i.phase.kind === "waiting" || !this.isTerminal(i.phase),
    ).length;
  }

  private isTerminal(p: PracticeItemPhase): boolean {
    return p.kind === "succeeded" || p.kind === "failed" || p.kind === "cancelled";
  }

  private emit(): void {
    // Fresh array + fresh item objects so React sees the change.
    this.snapshot = this.items.map((i) => ({ ...i }));
    this.listeners.forEach((fn) => fn());
  }

  private async pump(): Promise<void> {
    if (this.pumping) return;
    this.pumping = true;
    try {
      for (;;) {
        const item = this.items.find((i) => i.phase.kind === "waiting");
        const ctx = this.context;
        if (!item || !ctx) break;
        await new Promise<void>((resolve) => {
          const run = new AnalysisRun(ctx.api, item.clip, ctx.handedness, item.sport, (p) => {
            item.phase = p;
            if (p.kind === "succeeded") item.job = p.job;
            this.emit();
            if (this.isTerminal(p)) resolve();
          });
          void run.start();
        });
      }
    } finally {
      this.pumping = false;
    }
  }
}

export const practiceQueue = new PracticeQueue();

/** Summary counts for the HUD strip. */
export function summarize(items: PracticeItem[]): {
  total: number;
  waiting: number;
  active: number;
  done: number;
  failed: number;
} {
  let waiting = 0,
    active = 0,
    done = 0,
    failed = 0;
  for (const i of items) {
    if (i.phase.kind === "waiting") waiting++;
    else if (i.phase.kind === "succeeded") done++;
    else if (i.phase.kind === "failed" || i.phase.kind === "cancelled") failed++;
    else active++;
  }
  return { total: items.length, waiting, active, done, failed };
}
