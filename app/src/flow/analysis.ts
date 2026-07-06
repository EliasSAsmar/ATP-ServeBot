import { ApiError, NetworkUnreachableError, type ServeApi } from "../api";
import { APP_VERSION } from "../config";
import type {
  ClipMeta,
  CreateServeRequest,
  EdgeDetectDiagnostics,
  Handedness,
  JobResponse,
  JobStage,
  Sport,
} from "../types/api";

/** A finalized clip from the ring buffer, ready to submit. */
export interface CapturedClip {
  blob: Blob;
  meta: ClipMeta;
  contactTimestampMs: number;
  /** Present when auto-detect produced the clip; omitted for pure manual grabs. */
  edgeDetect?: EdgeDetectDiagnostics;
  capturedAt: number; // Date.now()
  source: "auto" | "manual";
}

export type AnalysisPhase =
  | { kind: "uploading"; progress: number }
  | { kind: "queued" } // job queued, or 429 busy → waiting for a slot
  | { kind: "running"; stage: JobStage | null; progress: number }
  | { kind: "succeeded"; job: JobResponse }
  | {
      kind: "failed";
      code: string;
      message: string;
      retriable: boolean;
      /** true when a re-POST /v1/serves with the same object_key is possible. */
      canRetrySameClip: boolean;
    }
  | { kind: "cancelled" };

// UI.md §4: poll_after_ms for the first wait, then 1.5s → 2s → 3s cap.
const POLL_BACKOFF_MS = [1500, 2000, 3000];
const MAX_POLL_NETWORK_RETRIES = 3;
const MAX_BUSY_RETRIES = 5;

/**
 * Runs the API_CONTRACT.md §6 client sequence for one captured clip:
 *   POST /v1/uploads → PUT clip → POST /v1/serves → poll → done.
 * Reports progress via `onPhase`. Supports cancel (AbortSignal) and, after a
 * retriable failure, re-submitting the same uploaded clip via `retry()`.
 */
export class AnalysisRun {
  private objectKey: string | null = null;
  private aborted = false;

  constructor(
    private api: ServeApi,
    private clip: CapturedClip,
    private handedness: Handedness,
    private sport: Sport,
    private onPhase: (phase: AnalysisPhase) => void,
    private signal?: AbortSignal,
  ) {
    signal?.addEventListener("abort", () => {
      this.aborted = true;
    });
  }

  async start(): Promise<void> {
    try {
      this.onPhase({ kind: "uploading", progress: 0 });
      const upload = await this.api.createUpload({
        content_type: this.clip.meta.content_type,
        byte_size: this.clip.blob.size,
        duration_ms: this.clip.meta.duration_ms,
        fps: this.clip.meta.fps,
        width: this.clip.meta.width,
        height: this.clip.meta.height,
      });
      if (this.checkAborted()) return;

      await this.api.putClip(
        upload,
        this.clip.blob,
        (fraction) => this.onPhase({ kind: "uploading", progress: fraction }),
        this.signal,
      );
      if (this.checkAborted()) return;

      this.objectKey = upload.object_key;
      await this.submitAndPoll();
    } catch (e) {
      this.reportError(e);
    }
  }

  /** Re-POST /v1/serves with the same object_key (UI.md §4 failed+retriable). */
  async retry(): Promise<void> {
    if (!this.objectKey) {
      // upload never completed — run the whole sequence again
      return this.start();
    }
    try {
      await this.submitAndPoll();
    } catch (e) {
      this.reportError(e);
    }
  }

  private async submitAndPoll(): Promise<void> {
    const request: CreateServeRequest = {
      object_key: this.objectKey!,
      handedness: this.handedness,
      sport: this.sport,
      contact_timestamp_ms: this.clip.contactTimestampMs,
      clip: this.clip.meta,
      ...(this.clip.edgeDetect ? { edge_detect: this.clip.edgeDetect } : {}),
      client: { app_version: APP_VERSION, platform: "web" },
    };

    // POST /v1/serves — on 429 busy, wait Retry-After and try again.
    let created = null;
    for (let attempt = 0; created === null; attempt++) {
      try {
        created = await this.api.createServe(request);
      } catch (e) {
        if (e instanceof ApiError && e.httpStatus === 429 && attempt < MAX_BUSY_RETRIES) {
          this.onPhase({ kind: "queued" });
          await this.wait((e.retryAfterSeconds ?? 2) * 1000);
          if (this.checkAborted()) return;
          continue;
        }
        throw e;
      }
    }
    if (this.checkAborted()) return;
    this.onPhase({ kind: "queued" });

    // Poll loop with backoff.
    let waitMs = created.poll_after_ms ?? POLL_BACKOFF_MS[0];
    let backoffIdx = 0;
    let networkFailures = 0;
    for (;;) {
      await this.wait(waitMs);
      if (this.checkAborted()) return;

      let job: JobResponse;
      try {
        job = await this.api.getServe(created.job_id);
        networkFailures = 0;
      } catch (e) {
        if (e instanceof NetworkUnreachableError && networkFailures < MAX_POLL_NETWORK_RETRIES) {
          networkFailures++;
          waitMs = POLL_BACKOFF_MS[Math.min(backoffIdx++, POLL_BACKOFF_MS.length - 1)];
          continue;
        }
        throw e;
      }

      if (job.status === "succeeded") {
        this.onPhase({ kind: "succeeded", job });
        return;
      }
      if (job.status === "failed") {
        this.onPhase({
          kind: "failed",
          code: job.error?.code ?? "internal_error",
          message: job.error?.message ?? "Analysis failed.",
          retriable: job.error?.retriable ?? false,
          canRetrySameClip: (job.error?.retriable ?? false) && this.objectKey !== null,
        });
        return;
      }
      if (job.status === "queued") {
        this.onPhase({ kind: "queued" });
      } else {
        this.onPhase({ kind: "running", stage: job.stage, progress: job.progress });
      }
      waitMs = POLL_BACKOFF_MS[Math.min(backoffIdx++, POLL_BACKOFF_MS.length - 1)];
    }
  }

  private reportError(e: unknown): void {
    if (this.aborted || (e instanceof DOMException && e.name === "AbortError")) {
      this.onPhase({ kind: "cancelled" });
      return;
    }
    if (e instanceof NetworkUnreachableError) {
      this.onPhase({
        kind: "failed",
        code: "network_unreachable",
        message: "Cloud analysis unavailable — the instance could not be reached. Your live view still works.",
        retriable: true,
        canRetrySameClip: this.objectKey !== null,
      });
      return;
    }
    if (e instanceof ApiError) {
      this.onPhase({
        kind: "failed",
        code: e.code,
        message: e.message,
        // 5xx and models_not_ready are worth retrying; 4xx contract errors are not.
        retriable: e.httpStatus >= 500 || e.code === "models_not_ready" || e.code === "busy",
        canRetrySameClip: this.objectKey !== null && e.code !== "clip_not_found" && e.code !== "invalid_object_key",
      });
      return;
    }
    this.onPhase({
      kind: "failed",
      code: "client_error",
      message: e instanceof Error ? e.message : "Unexpected error.",
      retriable: false,
      canRetrySameClip: false,
    });
  }

  private checkAborted(): boolean {
    if (this.aborted) this.onPhase({ kind: "cancelled" });
    return this.aborted;
  }

  private wait(ms: number): Promise<void> {
    return new Promise((resolve) => {
      const t = setTimeout(resolve, ms);
      this.signal?.addEventListener("abort", () => {
        clearTimeout(t);
        resolve();
      });
    });
  }
}
