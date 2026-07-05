import type {
  CreateServeRequest,
  CreateServeResponse,
  HealthResponse,
  JobResponse,
  UploadRequest,
  UploadResponse,
} from "../types/api";

/**
 * The client-side face of API_CONTRACT.md. Implemented twice:
 *  - api/real.ts — HTTP against the FastAPI backend (+ direct-to-S3 PUT/GET)
 *  - api/mock.ts — canned in-browser implementation of the exact §6 sequence
 */
export interface ServeApi {
  /** §1 GET /v1/health */
  health(): Promise<HealthResponse>;
  /** §2 POST /v1/uploads */
  createUpload(req: UploadRequest): Promise<UploadResponse>;
  /** §2b PUT clip bytes to the presigned URL (no X-API-Key). */
  putClip(
    upload: UploadResponse,
    clip: Blob,
    onProgress?: (fraction: number) => void,
    signal?: AbortSignal,
  ): Promise<void>;
  /** §3 POST /v1/serves */
  createServe(req: CreateServeRequest): Promise<CreateServeResponse>;
  /** §4 GET /v1/serves/{job_id} */
  getServe(jobId: string): Promise<JobResponse>;
  /** §6 last step — GET the presigned GLB. Throws GlbExpiredError on 403. */
  fetchGlb(glbUrl: string): Promise<ArrayBuffer>;
}

/** Non-2xx /v1 response, parsed from the standard error envelope (§0). */
export class ApiError extends Error {
  readonly code: string;
  readonly httpStatus: number;
  readonly field: string | null;
  readonly requestId: string | null;
  readonly retryAfterSeconds: number | null;

  constructor(opts: {
    code: string;
    message: string;
    httpStatus: number;
    field?: string | null;
    requestId?: string | null;
    retryAfterSeconds?: number | null;
  }) {
    super(opts.message);
    this.name = "ApiError";
    this.code = opts.code;
    this.httpStatus = opts.httpStatus;
    this.field = opts.field ?? null;
    this.requestId = opts.requestId ?? null;
    this.retryAfterSeconds = opts.retryAfterSeconds ?? null;
  }
}

/** Network-level failure (instance off, DNS, CORS, timeout). Contract §1: this IS the "cloud down" signal. */
export class NetworkUnreachableError extends Error {
  constructor(message = "Cloud analysis unavailable — could not reach the instance.") {
    super(message);
    this.name = "NetworkUnreachableError";
  }
}

/** Presigned GLB GET was rejected (expired signature). Client should re-poll the job for a fresh URL. */
export class GlbExpiredError extends Error {
  constructor() {
    super("Mesh URL expired");
    this.name = "GlbExpiredError";
  }
}
