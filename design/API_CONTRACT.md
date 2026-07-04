# API_CONTRACT

> **This is the authoritative interface.** Where any other doc disagrees with this one about payload shapes, this doc wins. Implementers should be able to build both sides without guessing. All examples are literal.

## 0. Conventions

- **Base URL:** `https://<ec2-host>/` (or `http://<ec2-host>:8000/` in local dev). All app endpoints are under `/v1`.
- **Content type:** `application/json; charset=utf-8` for all request/response bodies except the S3 clip upload (binary) and GLB download (binary).
- **Auth:** every `/v1/*` endpoint requires header `X-API-Key: <key>`. Missing/invalid → `401`. The S3 presigned PUT/GET requests do **not** use this header (they carry their own signature). `GET /v1/health` also requires the key (see note in §1).
- **IDs:** `job_id` is a UUIDv4 string. `object_key` is an S3 key string.
- **Timestamps:** all wall-clock times are ISO-8601 UTC with `Z` (e.g. `"2026-07-04T18:22:05.123Z"`). All *media* offsets are integer **milliseconds from clip start** (`contact_timestamp_ms`).
- **Angles:** degrees, floating point, one decimal place of precision is the max meaningful resolution (see product stance in `OVERVIEW.md §5`).
- **Coordinates:** 3D keypoints are in **meters**, model/camera space, right-handed, +Y up (as emitted by SAM 3D Body — confirm axis convention against the checkpoint in `MODELS.md §output`). Origin is the model root joint.
- **Enums are closed.** Unknown enum values are a client/server contract violation, not a soft warning.
- **Versioning:** breaking changes bump the path prefix (`/v2`). Additive fields do not.

### Standard error envelope

Every non-2xx response (except raw S3 responses) uses:

```json
{
  "error": {
    "code": "invalid_handedness",
    "message": "handedness must be one of: right, left",
    "field": "handedness",
    "request_id": "b1f2c3d4-0000-4000-8000-000000000000"
  }
}
```

`field` is `null` when the error is not tied to a specific field. `request_id` is always present and echoes the `X-Request-Id` response header.

### Error codes (closed set for v1)

| HTTP | `code` | When |
|---|---|---|
| 400 | `invalid_request` | Malformed JSON / missing required field. |
| 400 | `invalid_handedness` | `handedness` not in `{right,left}`. |
| 400 | `invalid_object_key` | `object_key` not a key this API minted, or wrong prefix. |
| 400 | `invalid_timestamp` | `contact_timestamp_ms` < 0 or > `duration_ms`. |
| 401 | `unauthorized` | Missing/invalid `X-API-Key`. |
| 404 | `job_not_found` | `job_id` unknown. |
| 409 | `clip_not_found` | `object_key` has no uploaded object in S3 yet. |
| 413 | `clip_too_large` | Clip exceeds `MAX_CLIP_BYTES` (see `INFRA.md`). |
| 422 | `unprocessable_clip` | Clip decoded but unusable (0 frames, corrupt). |
| 429 | `busy` | Worker queue full (v1: a job is already running and queue depth cap hit). |
| 500 | `internal_error` | Unexpected server fault. |
| 503 | `models_not_ready` | Instance up but models still loading into VRAM. |

---

## 1. `GET /v1/health`

Liveness + model readiness. The client calls this before offering "Analyze" so it can tell the user whether the cloud tier is up.

**Request:** headers only (`X-API-Key`). No body.

**Response `200`:**

```json
{
  "status": "ok",
  "instance_up": true,
  "models_ready": true,
  "models": {
    "sam3": { "loaded": true, "checkpoint": "sam3_hiera_large" },
    "sam3d_body": { "loaded": true, "checkpoint": "sam-3d-body-dinov3" }
  },
  "gpu": { "name": "NVIDIA A10G", "vram_total_mb": 23028, "vram_used_mb": 8123 },
  "queue_depth": 0,
  "server_time": "2026-07-04T18:22:05.123Z",
  "api_version": "v1"
}
```

- `models_ready=false` + `status="starting"` while checkpoints load. Client should treat this as "warming up," not "down."
- If the instance is **off**, the client's request simply fails at the network layer (timeout/DNS/connection refused). There is no "instance down" body — inability to reach `/health` **is** the down signal. The UI maps this to "Cloud analysis unavailable — start the instance."

---

## 2. `POST /v1/uploads` — mint a presigned clip-upload URL

Client calls this **before** uploading a clip. Server generates an S3 object key and a presigned `PUT` URL.

**Request:**

```json
{
  "content_type": "video/webm",
  "byte_size": 1843200,
  "duration_ms": 3200,
  "fps": 30,
  "width": 1280,
  "height": 720
}
```

| Field | Type | Req | Notes |
|---|---|---|---|
| `content_type` | string enum | yes | One of `video/webm`, `video/mp4`. Must match what the client will `PUT`. |
| `byte_size` | int | yes | Intended upload size in bytes. Rejected with `413 clip_too_large` if > `MAX_CLIP_BYTES`. |
| `duration_ms` | int | yes | Clip length. |
| `fps` | number | yes | Nominal capture frame rate. |
| `width` | int | yes | Pixel width. |
| `height` | int | yes | Pixel height. |

**Response `200`:**

```json
{
  "object_key": "clips/2026/07/04/9f8e7d6c-1a2b-4c3d-8e9f-000000000000.webm",
  "upload_url": "https://tennis-serve-clips.s3.amazonaws.com/clips/2026/07/04/9f8e...webm?X-Amz-Algorithm=...",
  "upload_method": "PUT",
  "upload_headers": { "Content-Type": "video/webm" },
  "expires_at": "2026-07-04T18:27:05.123Z"
}
```

- The client MUST `PUT` with exactly the headers in `upload_headers` (S3 signature covers `Content-Type`).
- `upload_url` expires at `expires_at` (default 5 min — see `INFRA.md`).
- `object_key` is what the client passes back to `POST /v1/serves`. The server validates that a submitted `object_key` was minted by it (prefix + signature bookkeeping) → else `400 invalid_object_key`.

### 2b. The S3 upload itself (not a FastAPI endpoint)

```
PUT {upload_url}
Content-Type: video/webm
<binary clip bytes>
```

- Success: S3 returns `200`/`204` with an `ETag`. No app body.
- The client does **not** send `X-API-Key` here.
- This transport is documented in `INFRA.md §upload`.

---

## 3. `POST /v1/serves` — create an analysis job

Called after the clip is in S3.

**Request:**

```json
{
  "object_key": "clips/2026/07/04/9f8e7d6c-1a2b-4c3d-8e9f-000000000000.webm",
  "handedness": "right",
  "contact_timestamp_ms": 1840,
  "clip": {
    "duration_ms": 3200,
    "fps": 30,
    "width": 1280,
    "height": 720,
    "content_type": "video/webm"
  },
  "edge_detect": {
    "detector_version": "serve-heuristic-1",
    "contact_confidence": 0.71,
    "peak_wrist_velocity_px_s": 2140.5,
    "arm_elevation_deg_at_contact": 156.2
  },
  "client": {
    "app_version": "0.1.0",
    "platform": "web",
    "user_label": "practice-serve-1"
  }
}
```

| Field | Type | Req | Notes |
|---|---|---|---|
| `object_key` | string | yes | From `POST /v1/uploads`. Must reference an uploaded object → else `409 clip_not_found`. |
| `handedness` | enum `right\|left` | yes | Client-supplied. Selects which side's joints metrics use. |
| `contact_timestamp_ms` | int | yes | Edge estimate of contact, ms from clip start. `0 ≤ x ≤ duration_ms`. The cloud **refines** this within a window (`MODELS.md §keyframe`). |
| `clip` | object | yes | Echo of clip metadata. Used for decode sanity checks. |
| `edge_detect` | object | no | Diagnostics from the on-device heuristic. `contact_confidence` ∈ [0,1]. Purely informational in v1; stored, not required. |
| `client` | object | no | App/version tagging. `user_label` is a free-text tag surfaced in the UI, not an identity. |

**Response `202 Accepted`:**

```json
{
  "job_id": "3c1a2b4d-5e6f-4a7b-8c9d-000000000000",
  "status": "queued",
  "created_at": "2026-07-04T18:22:06.001Z",
  "poll_url": "/v1/serves/3c1a2b4d-5e6f-4a7b-8c9d-000000000000",
  "poll_after_ms": 1500
}
```

- `poll_after_ms` is a **hint** for the client's first poll delay. The client should back off (e.g. 1.5s → 2s → 3s, capped) on subsequent polls.
- If the worker queue is full: `429 busy` with the standard error envelope and header `Retry-After: <seconds>`.

---

## 4. `GET /v1/serves/{job_id}` — poll job status / fetch result

The single polling endpoint. Same URL for every status.

### Job status lifecycle (closed enum)

```
queued ──▶ running ──▶ succeeded
   │           │
   └────────── └──▶ failed
```

`status` ∈ `{queued, running, succeeded, failed}`. There is no separate cancel state in v1.

### `stage` (informational sub-state, only while `running`)

Closed enum, monotonic, for progress UI:

`downloading → decoding → segmenting → selecting_keyframe → reconstructing → filtering → computing_metrics → generating_tips → uploading_mesh`

### 4a. Response while `queued`

```json
{
  "job_id": "3c1a2b4d-5e6f-4a7b-8c9d-000000000000",
  "status": "queued",
  "stage": null,
  "progress": 0.0,
  "created_at": "2026-07-04T18:22:06.001Z",
  "started_at": null,
  "finished_at": null,
  "poll_after_ms": 1500,
  "result": null,
  "error": null
}
```

### 4b. Response while `running`

```json
{
  "job_id": "3c1a2b4d-5e6f-4a7b-8c9d-000000000000",
  "status": "running",
  "stage": "reconstructing",
  "progress": 0.55,
  "created_at": "2026-07-04T18:22:06.001Z",
  "started_at": "2026-07-04T18:22:06.900Z",
  "finished_at": null,
  "poll_after_ms": 1500,
  "result": null,
  "error": null
}
```

- `progress` ∈ [0,1], best-effort. Client must not assume linearity; it is for a progress bar only.

### 4c. Response when `succeeded` — **the full result payload**

```json
{
  "job_id": "3c1a2b4d-5e6f-4a7b-8c9d-000000000000",
  "status": "succeeded",
  "stage": null,
  "progress": 1.0,
  "created_at": "2026-07-04T18:22:06.001Z",
  "started_at": "2026-07-04T18:22:06.900Z",
  "finished_at": "2026-07-04T18:22:14.512Z",
  "poll_after_ms": null,
  "error": null,
  "result": {
    "schema_version": "serve-result-1",
    "handedness": "right",
    "contact": {
      "edge_timestamp_ms": 1840,
      "refined_timestamp_ms": 1867,
      "refined_frame_index": 56,
      "contact_confidence": 0.68,
      "refine_window_ms": 200
    },
    "keyframes": [
      {
        "role": "contact",
        "timestamp_ms": 1867,
        "frame_index": 56,
        "mesh": {
          "glb_url": "https://tennis-serve-meshes.s3.amazonaws.com/meshes/3c1a.../contact.glb?X-Amz-...",
          "glb_expires_at": "2026-07-04T18:37:14.512Z",
          "vertex_count": 10475,
          "up_axis": "Y",
          "units": "meters",
          "root_translation": [0.0, 0.0, 0.0]
        },
        "keypoints_3d": {
          "format": "sam3d-body-70",
          "count": 70,
          "units": "meters",
          "points": [
            { "index": 12, "name": "right_shoulder", "xyz": [0.182, 1.402, 0.031], "score": 0.94 },
            { "index": 14, "name": "right_elbow",    "xyz": [0.372, 1.560, -0.010], "score": 0.90 },
            { "index": 16, "name": "right_wrist",    "xyz": [0.540, 1.712, -0.048], "score": 0.86 }
          ]
        }
      }
    ],
    "metrics": {
      "elbow_angle_deg": {
        "value": 118.3,
        "unit": "degree",
        "side": "right",
        "joints": ["right_shoulder", "right_elbow", "right_wrist"],
        "keyframe_role": "contact",
        "confidence": 0.88,
        "band": "slightly_bent",
        "reference_range_deg": [150.0, 180.0]
      },
      "shoulder_angle_deg": null,
      "knee_flexion_deg": null,
      "kinetic_chain_sequence": null,
      "toss_placement": null,
      "toss_consistency": null,
      "contact_height": null,
      "phase_timing": null
    },
    "tips": [
      {
        "id": "elbow_too_bent",
        "metric": "elbow_angle_deg",
        "severity": "info",
        "title": "Extend at contact",
        "message": "Your hitting elbow was fairly bent at contact (~118°). Reaching to a straighter arm at contact can add height and power.",
        "triggered_by": { "value": 118.3, "threshold": 150.0, "comparator": "lt" }
      }
    ],
    "diagnostics": {
      "frames_decoded": 96,
      "frames_masked": 96,
      "mask_coverage_at_contact": 0.83,
      "model_versions": {
        "sam3": "sam3_hiera_large",
        "sam3d_body": "sam-3d-body-dinov3",
        "metric_engine": "metrics-1",
        "tip_engine": "tips-1"
      },
      "timings_ms": {
        "download": 210,
        "decode": 640,
        "segment": 3900,
        "keyframe": 120,
        "reconstruct": 2100,
        "filter": 30,
        "metrics": 5,
        "tips": 1,
        "upload_mesh": 260
      }
    }
  }
}
```

#### Field contract for `result`

| Path | Type | Notes |
|---|---|---|
| `schema_version` | string | `"serve-result-1"`. Bump on breaking result changes. |
| `handedness` | enum | Echo of request. |
| `contact.edge_timestamp_ms` | int | What the client sent. |
| `contact.refined_timestamp_ms` | int | Cloud-refined contact. May equal edge value. |
| `contact.refined_frame_index` | int | Index into decoded frames. |
| `contact.contact_confidence` | number [0,1] | Cloud's confidence in the contact estimate (pose-derived; racket/ball not tracked). |
| `keyframes[]` | array | v1: exactly one, `role="contact"`. Array shape reserved for more keyframes later. |
| `keyframes[].mesh.glb_url` | string (URL) | **Presigned S3 GET.** Client downloads directly. |
| `keyframes[].mesh.glb_expires_at` | ISO-8601 | GLB URL expiry (default 15 min). Re-poll to refresh if expired. |
| `keyframes[].keypoints_3d.points[]` | array | Each: `index` (int, model keypoint index), `name` (string, canonical — see `MODELS.md §skeleton`), `xyz` (`[x,y,z]` meters), `score` [0,1]. **All 70 points are returned**; the example truncates. |
| `metrics.<name>` | object or `null` | **Implemented metrics are objects; stubbed metrics are `null`.** In v1 only `elbow_angle_deg` is an object. Keys for all planned metrics are present with `null` so the client can render placeholders. |
| `metrics.elbow_angle_deg.band` | enum | Qualitative band (see `METRICS.md`). |
| `tips[]` | array | Zero or more. Empty array = no tip fired (metric in acceptable range). Never `null`. |
| `tips[].severity` | enum `info\|suggestion\|flag` | v1 uses `info`/`suggestion`. |
| `diagnostics` | object | Non-user-facing; for debugging/telemetry. Client may ignore. |

**Nullability rule (important):** a metric is `null` **iff not implemented**. A metric that is implemented but *could not be computed* for this clip is a non-null object with `value: null` and a `compute_error` field:

```json
"elbow_angle_deg": {
  "value": null,
  "unit": "degree",
  "side": "right",
  "compute_error": "missing_keypoint",
  "missing": ["right_wrist"],
  "confidence": 0.0
}
```

This lets the client distinguish "not built yet" (`null`) from "built but failed on this serve" (object with `value: null`).

### 4d. Response when `failed`

```json
{
  "job_id": "3c1a2b4d-5e6f-4a7b-8c9d-000000000000",
  "status": "failed",
  "stage": null,
  "progress": 1.0,
  "created_at": "2026-07-04T18:22:06.001Z",
  "started_at": "2026-07-04T18:22:06.900Z",
  "finished_at": "2026-07-04T18:22:09.100Z",
  "poll_after_ms": null,
  "result": null,
  "error": {
    "code": "unprocessable_clip",
    "message": "SAM 3 found no trackable person in the clip.",
    "stage": "segmenting",
    "retriable": false
  }
}
```

- `error.stage` names the pipeline stage that failed.
- `error.retriable` tells the client whether re-submitting the same clip could help (e.g. transient GPU OOM → `true`; no person in frame → `false`).

---

## 5. `GET /v1/serves` — list recent jobs (optional, dev/debug)

Not required for the walking skeleton UI, but cheap and useful. If implemented:

**Query params:** `limit` (default 20, max 100), `status` (optional filter).

**Response `200`:**

```json
{
  "jobs": [
    {
      "job_id": "3c1a2b4d-5e6f-4a7b-8c9d-000000000000",
      "status": "succeeded",
      "created_at": "2026-07-04T18:22:06.001Z",
      "handedness": "right",
      "user_label": "practice-serve-1"
    }
  ],
  "count": 1
}
```

---

## 6. Client sequence (reference)

```
GET  /v1/health                      → confirm instance_up && models_ready
POST /v1/uploads {clip meta}         → { object_key, upload_url }
PUT  {upload_url} <bytes>            → 200/204 (direct to S3)
POST /v1/serves {object_key, ...}    → { job_id, poll_url, poll_after_ms }
loop:
  GET /v1/serves/{job_id}            → status in {queued,running}? wait poll_after_ms, backoff, repeat
                                     → succeeded? break with result
                                     → failed? show error.message (respect retriable)
GET  {result...glb_url} <bytes>      → render mesh in three.js (direct from S3)
```

## 7. Non-normative notes for implementers

- **Idempotency:** re-`POST /v1/serves` with the same `object_key` creates a **new** job (no dedup in v1). The client should not auto-retry `POST /serves` on network timeout without first checking whether a job was created (v1 accepts the duplicate risk; it's a single-user app).
- **CORS:** the API must allow the web app's origin for all `/v1/*` methods and the `X-API-Key`, `X-Request-Id` headers. S3 bucket CORS must allow `PUT` (clips) and `GET` (meshes) from the app origin — see `INFRA.md §cors`.
- **Clock:** the client should not rely on its own clock for expiry; treat `expires_at`/`glb_expires_at` as server truth and re-request when a signed URL 403s.
- **Payload size:** the result payload with all 70 keypoints is a few KB — safe to poll. Meshes are **never** inlined; always via `glb_url`.
