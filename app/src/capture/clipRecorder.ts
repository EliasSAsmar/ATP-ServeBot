import type { ClipContentType } from "../types/api";

/**
 * MediaRecorder ring buffer (UI.md §0, MILESTONE_V1 Step 1).
 *
 * WebM chunks are only decodable from the segment's first chunk (the init
 * segment), so a naive "drop old chunks" ring buffer would produce broken
 * clips. Instead we run TWO staggered MediaRecorders over the same stream:
 *
 *   slot A: |========|========|========|
 *   slot B:     |========|========|====
 *              stagger↑   each segment = 2 × stagger
 *
 * At any instant the older slot holds between `staggerMs` and `2×staggerMs`
 * of decodable history — that is the ring buffer. On capture we let the
 * recorder run `tailMs` past the contact moment, stop the older slot, and
 * emit its whole segment as the clip (pre-roll + contact + tail, ~2.5–6s).
 *
 * `contact_timestamp_ms` is simply `contactTime − segmentStartTime`.
 */

export interface CaptureResult {
  blob: Blob;
  /** performance.now() when the emitted segment started recording. */
  startTimeMs: number;
  /** Wall duration of the emitted segment. */
  durationMs: number;
  contentType: ClipContentType;
}

const MIME_CANDIDATES = [
  "video/webm;codecs=vp9",
  "video/webm;codecs=vp8",
  "video/webm",
  "video/mp4",
];

interface Slot {
  recorder: MediaRecorder;
  chunks: Blob[];
  startTimeMs: number;
}

export class ClipRecorder {
  readonly mimeType: string;
  readonly contentType: ClipContentType;
  private slots: (Slot | null)[] = [null, null];
  private rotateTimer: number | null = null;
  private running = false;
  private capturing = false;

  constructor(
    private stream: MediaStream,
    private staggerMs = 2500,
    private tailMs = 1200,
  ) {
    const supported = MIME_CANDIDATES.find(
      (m) => typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(m),
    );
    this.mimeType = supported ?? "";
    this.contentType = this.mimeType.startsWith("video/mp4") ? "video/mp4" : "video/webm";
  }

  static isSupported(): boolean {
    return typeof MediaRecorder !== "undefined";
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.startSlot(0);
    this.rotateTimer = window.setInterval(() => this.rotate(), this.staggerMs);
  }

  stop(): void {
    this.running = false;
    if (this.rotateTimer !== null) window.clearInterval(this.rotateTimer);
    this.rotateTimer = null;
    for (let i = 0; i < this.slots.length; i++) {
      const slot = this.slots[i];
      if (slot && slot.recorder.state !== "inactive") {
        try {
          slot.recorder.stop();
        } catch {
          // recorder already stopped — nothing to clean up
        }
      }
      this.slots[i] = null;
    }
  }

  private startSlot(i: number): void {
    const recorder = new MediaRecorder(
      this.stream,
      this.mimeType ? { mimeType: this.mimeType } : undefined,
    );
    const slot: Slot = { recorder, chunks: [], startTimeMs: performance.now() };
    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) slot.chunks.push(e.data);
    };
    recorder.start(250); // timeslice so chunks accumulate steadily
    this.slots[i] = slot;
  }

  private rotate(): void {
    if (!this.running || this.capturing) return;
    // First tick after start(): bring the second slot online (staggered).
    const emptyIdx = this.slots.findIndex((s) => s === null);
    if (emptyIdx !== -1) {
      this.startSlot(emptyIdx);
      return;
    }
    // Restart whichever slot has completed a full segment (2 × stagger).
    const oldestIdx = this.oldestSlotIndex();
    const oldest = this.slots[oldestIdx]!;
    if (performance.now() - oldest.startTimeMs >= this.staggerMs * 2 - 50) {
      try {
        oldest.recorder.stop();
      } catch {
        // best-effort stop of the retiring recorder
      }
      this.startSlot(oldestIdx);
    }
  }

  private oldestSlotIndex(): number {
    const [a, b] = this.slots;
    if (!a) return 1;
    if (!b) return 0;
    return a.startTimeMs <= b.startTimeMs ? 0 : 1;
  }

  /**
   * Freeze the ring buffer into a clip containing `contactTimeMs`
   * (performance.now() domain). Waits `tailMs` past contact so the moment is
   * not at the clip's edge, then emits the older slot's full segment.
   */
  async capture(contactTimeMs: number): Promise<CaptureResult> {
    if (!this.running || this.capturing) throw new Error("Recorder not ready to capture");
    this.capturing = true;
    try {
      const waitMs = Math.max(0, contactTimeMs + this.tailMs - performance.now());
      if (waitMs > 0) await sleep(waitMs);

      // Prefer the slot with the most pre-roll that still contains contact.
      let idx = this.oldestSlotIndex();
      const oldest = this.slots[idx];
      if (!oldest || oldest.startTimeMs > contactTimeMs) {
        const otherIdx = idx === 0 ? 1 : 0;
        if (this.slots[otherIdx] && this.slots[otherIdx]!.startTimeMs <= contactTimeMs) idx = otherIdx;
      }
      const slot = this.slots[idx];
      if (!slot) throw new Error("No active recording segment");

      const blob = await stopAndCollect(slot);
      const endTimeMs = performance.now();
      this.slots[idx] = null;
      this.startSlot(idx); // resume buffering immediately

      return {
        blob,
        startTimeMs: slot.startTimeMs,
        durationMs: Math.round(endTimeMs - slot.startTimeMs),
        contentType: this.contentType,
      };
    } finally {
      this.capturing = false;
    }
  }
}

function stopAndCollect(slot: Slot): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const { recorder } = slot;
    recorder.onstop = () => {
      const type = recorder.mimeType || "video/webm";
      resolve(new Blob(slot.chunks, { type }));
    };
    recorder.onerror = () => reject(new Error("MediaRecorder error while finalizing clip"));
    try {
      recorder.stop(); // flushes a final dataavailable before onstop
    } catch (e) {
      reject(e instanceof Error ? e : new Error("Failed to stop recorder"));
    }
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
