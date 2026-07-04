# INFRA

EC2, S3, presigned upload flow, and on-demand/auto-stop for the heavy tier. Single instance, single user, walking-skeleton scale. Values marked **[CONFIRM]** are sensible defaults to finalize at implementation.

## 1. Compute — EC2 g5.xlarge (locked)

| Item | Value |
|---|---|
| Instance type | `g5.xlarge` — 1× NVIDIA **A10G (24GB)**, 4 vCPU, 16GB RAM |
| Region | **[CONFIRM]** — same region as the S3 bucket (avoid cross-region latency/egress). |
| OS / AMI | Deep Learning AMI (Ubuntu, NVIDIA drivers + CUDA preinstalled) **[CONFIRM version]**. |
| Runtime | Python 3.11 **[CONFIRM]**, PyTorch + CUDA (matched to the AMI's CUDA), FastAPI + uvicorn. |
| Process | Single uvicorn process; models loaded once into VRAM (`MODELS.md §5`); one in-process async worker. |
| Lifecycle | **On-demand / spot**, started and stopped around use (§4). |

**Storage on instance:** root EBS large enough for OS + model checkpoints (**[CONFIRM] ~100GB gp3**). Checkpoints for SAM 3 and SAM 3D Body baked into the AMI or cached on EBS to avoid re-download on each boot.

**Ports / security group:**
- Inbound `443` (or `8000` in dev) from the app origin only **[CONFIRM]** — ideally front with a TLS termination (nginx/Caddy or an ALB) so the API is HTTPS. For a bare walking skeleton, `8000` restricted to the developer's IP is acceptable but document it as insecure.
- Inbound `22` (SSH) from admin IP only.
- Outbound `443` to S3 (and model hubs on first boot).

## 2. Storage — S3

Two logical prefixes, one or two buckets **[CONFIRM]**:

| Prefix / bucket | Contents | Direction | Lifecycle |
|---|---|---|---|
| `clips/` | Uploaded serve clips (webm/mp4) | client → S3 (presigned PUT) | Expire after **[CONFIRM] 7 days** (walking skeleton keeps nothing long-term). |
| `meshes/` | Returned GLB meshes | S3 ← worker; client GET (presigned) | Expire after **[CONFIRM] 7 days**. |

- **Encryption:** SSE-S3 (or SSE-KMS) at rest **[CONFIRM]**.
- **Public access:** **fully blocked.** All access is via presigned URLs only.
- **Key scheme:** `clips/YYYY/MM/DD/<uuid>.<ext>`, `meshes/<job_id>/contact.glb` (see `API_CONTRACT.md`).
- **Max clip size:** `MAX_CLIP_BYTES` = **[CONFIRM] 25 MB** (a 2–4s 720p clip is well under this). Enforced at `POST /v1/uploads` (`413`) and via presigned `content-length-range` condition.

## 3. Upload flow (presigned PUT) — the exact dance

```
Client                         FastAPI (EC2)                     S3
  │  POST /v1/uploads ─────────▶ │
  │      {content_type,size,...} │  boto3 generate_presigned_url  │
  │                              │    ('put_object', key, cond)   │
  │  ◀── {object_key, upload_url,│                                │
  │        upload_headers, exp}  │                                │
  │                                                               │
  │  PUT upload_url  ───────────────────────────────────────────▶│  store bytes
  │      Content-Type: video/webm                                 │
  │      <clip bytes>                                             │
  │  ◀────────────────────────────────────────── 200/204 + ETag ─│
  │                                                               │
  │  POST /v1/serves {object_key,...} ─▶ FastAPI  (HEAD object_key to verify exists → else 409)
```

- **Presigned PUT generation:** `boto3` `generate_presigned_url('put_object', Params={Bucket, Key, ContentType}, ExpiresIn=300)`. Include a `content-length-range` (or use presigned **POST** with conditions) so oversized/wrong-type uploads are rejected by S3 itself **[CONFIRM: PUT with ContentType vs POST with policy]**.
- **Expiry:** 5 min (`expires_at` in response). Client must upload promptly.
- **Verification:** on `POST /v1/serves`, the server does an S3 `HEAD` on `object_key`; missing → `409 clip_not_found`.
- **Presigned GET (mesh):** worker uploads GLB, then `generate_presigned_url('get_object', ExpiresIn=900)` → returned as `glb_url` (`glb_expires_at`). Client re-polls to refresh if it expires.
- **EC2 never proxies clip/mesh bytes** — all large-blob transfer is client↔S3 direct.

### IAM
- Instance role (or a dedicated user) with least-privilege: `s3:PutObject`, `s3:GetObject`, `s3:HeadObject`, `s3:DeleteObject` scoped to the two prefixes only; plus `s3:PutObject`/`GetObject` needed to *generate* presigned URLs (signing uses the instance credentials).
- No public bucket policy.

## 4. On-demand / auto-stop (cost control)

The instance is expensive; it must **not idle**. Strategy:

1. **Manual start/stop by owner** (stated preference): owner starts the instance before a session, stops it after. This is the primary control and why cold-start isn't a v1 concern (`ARCHITECTURE.md §4`).
2. **Auto-stop safety net (recommended):** a small idle watchdog on the instance stops it after **[CONFIRM] N minutes** (e.g. 20) with no jobs:
   - Track `last_job_finished_at` in the FastAPI process.
   - A cron/systemd-timer script checks: if `now - last_activity > idle_timeout` **and** `queue_depth == 0` → `aws ec2 stop-instances --instance-ids <self>` (instance needs `ec2:StopInstances` on itself).
   - `GET /v1/health` activity also resets the idle timer optionally **[CONFIRM]** so an open app doesn't get killed mid-session.
3. **Start UX:** the client cannot start the instance in v1 (no control-plane creds in the browser). It only *detects* up/down via `/v1/health`. Starting is out-of-band (owner via console/CLI). **[CONFIRM]** — a tiny always-on Lambda + API Gateway "start my instance" button is a possible convenience but is **out of v1 scope**.

## 5. Spot notes

- Spot g5.xlarge is meaningfully cheaper but **interruptible** (2-min warning).
- v1 mitigations:
  - Jobs are short (seconds); an interruption mid-job → the client's poll eventually fails/instance disappears → surface "cloud offline," user retries when instance is back.
  - No durable job store required for spot survival in v1 (single user tolerates a lost job). If desired later, persist the job store off-instance (DynamoDB/Redis) so jobs survive interruption.
  - Handle the spot interruption notice (poll instance metadata) to **drain gracefully**: stop accepting new jobs, let the current one finish if <2 min, upload result.
- **Recommendation:** run **on-demand** for v1 (simpler, no interruption handling), switch to spot once the pipeline is proven. Document both; default = on-demand.

## 6. CORS

- **API (FastAPI):** allow the web app origin; allow methods `GET, POST, OPTIONS`; allow headers `Content-Type, X-API-Key, X-Request-Id`; expose `X-Request-Id`.
- **S3 bucket CORS:** allow `PUT` (clips) and `GET` (meshes) and `HEAD` from the app origin; allow header `Content-Type`; expose `ETag`. Without this, browser uploads/downloads to S3 fail.

## 7. Secrets & config

| Config | Where |
|---|---|
| `API_KEY` (the `X-API-Key` value) | Instance env / secrets manager; also entered in the app Settings (`UI.md §7`). |
| S3 bucket names, region | Instance env. |
| Model checkpoint paths | Instance env / baked into AMI. |
| Thresholds (`METRICS.md §9.3`), `MAX_CLIP_BYTES`, timeouts | App config file/env on the instance. |

Never ship the API key in client source control; it's user-entered in v1.

## 8. Observability (minimal for v1)

- Structured logs per job: `job_id`, stages, `timings_ms` (mirrors `diagnostics`), errors.
- `GET /v1/health` for uptime/VRAM.
- **[CONFIRM]** ship logs to CloudWatch; a dashboard is optional for v1.

## 9. Rough cost sketch (non-binding)

- g5.xlarge on-demand ≈ **[CONFIRM current pricing]** ~$1/hr order-of-magnitude; the manual start/stop + idle watchdog keeps this to actual-use hours.
- S3 storage negligible at 7-day expiry and tiny objects.
- No GPU idle cost is the whole point of on-demand + auto-stop.
