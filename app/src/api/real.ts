import type {
  CreateServeRequest,
  CreateServeResponse,
  ErrorEnvelope,
  HealthResponse,
  JobResponse,
  UploadRequest,
  UploadResponse,
} from "../types/api";
import { ApiError, GlbExpiredError, NetworkUnreachableError, type ServeApi } from "./types";

/**
 * HTTP client for the real backend, exactly per API_CONTRACT.md:
 *  - every /v1 call carries X-API-Key
 *  - the S3 PUT/GET carry no app auth (presigned)
 *  - non-2xx /v1 responses use the standard error envelope
 */
export class RealServeApi implements ServeApi {
  constructor(private baseUrl: string, private apiKey: string) {}

  private url(path: string): string {
    return this.baseUrl.replace(/\/+$/, "") + path;
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    let res: Response;
    try {
      res = await fetch(this.url(path), {
        ...init,
        headers: {
          "X-API-Key": this.apiKey,
          ...(init?.body ? { "Content-Type": "application/json; charset=utf-8" } : {}),
          ...init?.headers,
        },
      });
    } catch (e) {
      // fetch throws only on network-layer failure — per contract §1 this is
      // the "instance down" signal.
      throw new NetworkUnreachableError();
    }
    if (res.ok) {
      return (await res.json()) as T;
    }
    let envelope: ErrorEnvelope | null = null;
    try {
      envelope = (await res.json()) as ErrorEnvelope;
    } catch {
      // non-JSON error body — fall through to generic error
    }
    const retryAfter = res.headers.get("Retry-After");
    throw new ApiError({
      code: envelope?.error?.code ?? "internal_error",
      message: envelope?.error?.message ?? `HTTP ${res.status}`,
      httpStatus: res.status,
      field: envelope?.error?.field ?? null,
      requestId: envelope?.error?.request_id ?? null,
      retryAfterSeconds: retryAfter ? Number(retryAfter) : null,
    });
  }

  health(): Promise<HealthResponse> {
    return this.request<HealthResponse>("/v1/health");
  }

  createUpload(req: UploadRequest): Promise<UploadResponse> {
    return this.request<UploadResponse>("/v1/uploads", {
      method: "POST",
      body: JSON.stringify(req),
    });
  }

  /**
   * §2b: PUT the clip directly to S3 with exactly the returned upload_headers.
   * XMLHttpRequest is used (not fetch) so we can surface upload progress.
   */
  putClip(
    upload: UploadResponse,
    clip: Blob,
    onProgress?: (fraction: number) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      signal?.addEventListener("abort", () => xhr.abort());
      xhr.open(upload.upload_method, upload.upload_url);
      for (const [k, v] of Object.entries(upload.upload_headers)) {
        xhr.setRequestHeader(k, v);
      }
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
      };
      xhr.onload = () => {
        if (xhr.status === 200 || xhr.status === 204) {
          onProgress?.(1);
          resolve();
        } else {
          reject(
            new ApiError({
              code: "upload_failed",
              message: `Clip upload failed (S3 returned ${xhr.status})`,
              httpStatus: xhr.status,
            }),
          );
        }
      };
      xhr.onerror = () => reject(new NetworkUnreachableError("Clip upload failed at the network layer."));
      xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
      xhr.send(clip);
    });
  }

  createServe(req: CreateServeRequest): Promise<CreateServeResponse> {
    return this.request<CreateServeResponse>("/v1/serves", {
      method: "POST",
      body: JSON.stringify(req),
    });
  }

  getServe(jobId: string): Promise<JobResponse> {
    return this.request<JobResponse>(`/v1/serves/${jobId}`);
  }

  async fetchGlb(glbUrl: string): Promise<ArrayBuffer> {
    let res: Response;
    try {
      res = await fetch(glbUrl); // presigned GET — no X-API-Key (§0)
    } catch {
      throw new NetworkUnreachableError("Could not download the 3D mesh.");
    }
    if (res.status === 403) throw new GlbExpiredError();
    if (!res.ok) {
      throw new ApiError({ code: "mesh_fetch_failed", message: `Mesh download failed (${res.status})`, httpStatus: res.status });
    }
    return res.arrayBuffer();
  }
}
