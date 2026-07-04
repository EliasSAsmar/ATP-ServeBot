# server/ — Heavy tier (FastAPI)

The cloud inference service (target: EC2 g5.xlarge). **GPU is never in the live loop** — this handles per-serve, async analysis only.

Responsibilities:
- Presigned S3 URL minting + async job lifecycle (`X-API-Key` auth)
- Pipeline: download clip → decode → **SAM 3** mask → refine contact keyframe → **SAM 3D Body** mesh + 70 keypoints → One-Euro filter → metrics → rule-based tips → upload GLB

**Build against:** [`../design/API_CONTRACT.md`](../design/API_CONTRACT.md) (interface), [`../design/MODELS.md`](../design/MODELS.md) (models), [`../design/METRICS.md`](../design/METRICS.md) (formulas + tips).
Build order: [`../design/MILESTONE_V1.md`](../design/MILESTONE_V1.md) Steps 2–5.

## Status: Milestone v1 Steps 2 + 5 ✅

Implemented and runnable **with zero AWS and zero GPU**:

- **Contract-exact API** — `GET /v1/health`, `POST /v1/uploads`, `POST /v1/serves`, `GET /v1/serves/{job_id}`, `GET /v1/serves` (dev list). Standard error envelope, the closed v1 error-code set, `X-Request-Id` on every response, `X-API-Key` auth, CORS.
- **Async job model** — in-memory job store, one in-process worker, one job at a time; `queued → running → succeeded/failed` with the contract's `stage` sub-states and `progress`; `429 busy` + `Retry-After` when the queue cap is hit.
- **Stub analysis pipeline** (`servebot/pipeline.py`) — walks every contract stage and returns a fully schema-valid `succeeded` result: a valid placeholder GLB, a well-formed 70-keypoint pose (serving-arm joints geometrically consistent), and…
- **The REAL elbow-angle metric + tip engine** (Step 5, `servebot/metrics.py` + `servebot/tips.py`) — angle-at-joint per `METRICS.md §1`, serving-side selection, bands, `min`-of-scores confidence, the full nullability rule, the ordered `§9.2` tip rules with the low-confidence guard, and the `§9.3` thresholds as a single config object.
- **Storage seam** (`servebot/storage/`) — `LocalDiskStorage` serves HMAC-"presigned" PUT/GET via `/local-s3/*` routes (outside the `/v1` auth boundary, mirroring S3); the real boto3 `S3Storage` is written to `INFRA.md §3` but is a **TODO/unverified stub** until deployed with credentials.

Not implemented here (later steps): real SAM 3 / SAM 3D Body inference (Steps 3–4) — the `AnalysisPipeline` interface in `pipeline.py` is the drop-in seam.

**Hard gate before shipping real reconstructions:** confirm the SAM 3D Body 70-joint index→name map against the real checkpoint (the six arm joints especially) — see `MODELS.md §4.4`. The map in `servebot/skeleton.py` is a clearly marked placeholder.

## Quickstart

```bash
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # then edit SERVEBOT_API_KEY etc.
set -a; source .env; set +a

uvicorn servebot.main:app --host 127.0.0.1 --port 8000
```

Run the full client sequence (API_CONTRACT.md §6) with curl:

```bash
KEY='X-API-Key: dev-local-key'; B=http://127.0.0.1:8000

curl -s -H "$KEY" $B/v1/health | jq .models_ready

UP=$(curl -s -H "$KEY" -H 'Content-Type: application/json' -d '{
  "content_type":"video/webm","byte_size":200000,
  "duration_ms":3200,"fps":30,"width":1280,"height":720}' $B/v1/uploads)
URL=$(echo "$UP" | jq -r .upload_url); OK=$(echo "$UP" | jq -r .object_key)

head -c 200000 /dev/urandom > clip.webm     # stand-in clip bytes
curl -s -X PUT -H 'Content-Type: video/webm' --data-binary @clip.webm "$URL"

JOB=$(curl -s -H "$KEY" -H 'Content-Type: application/json' -d "{
  \"object_key\":\"$OK\",\"handedness\":\"right\",\"contact_timestamp_ms\":1840,
  \"clip\":{\"duration_ms\":3200,\"fps\":30,\"width\":1280,\"height\":720,\"content_type\":\"video/webm\"}}" \
  $B/v1/serves | jq -r .job_id)

curl -s -H "$KEY" $B/v1/serves/$JOB | jq '{status, stage, progress}'   # repeat until succeeded
curl -s -H "$KEY" $B/v1/serves/$JOB | jq '.result.metrics.elbow_angle_deg, .result.tips'
```

The succeeded result carries a real computed `elbow_angle_deg` (177.6° for the stub pose → band `straight` → the `elbow_good_extension` tip) and a `glb_url` that serves a valid binary glTF.

## Tests

```bash
cd server && ./.venv/bin/python -m pytest
```

Covers: `angle_at_joint` (90°, 180°, the §1 worked example), band boundaries, nullability paths (`null` vs `value: null` + `compute_error`), golden metric→band→tips tests, the tip rules + low-confidence guard, skeleton/GLB sanity, and an end-to-end API test of the full upload→serve→poll→GLB sequence plus every contract error code path.

## Layout

```
servebot/
  main.py        app factory, middleware (X-Request-Id, CORS), error envelope
  api.py         /v1 routes (health, uploads, serves)
  jobs.py        in-memory job store + single async worker
  pipeline.py    AnalysisPipeline seam + StubAnalysisPipeline (Steps 3-4 drop in here)
  metrics.py     REAL elbow-angle metric engine (METRICS.md §1)
  tips.py        REAL rule-based tip engine (METRICS.md §9)
  skeleton.py    placeholder 70-joint map [confirm vs checkpoint!] + stub pose
  glb.py         valid placeholder GLB generator
  schemas.py     Pydantic models for every request/response payload
  config.py      Settings (env) + Thresholds (METRICS.md §9.3)
  errors.py      ApiError + closed error-code set
  storage/       StorageBackend seam: local.py (dev, /local-s3 routes), s3.py (boto3 TODO)
tests/           pytest suite (unit + golden + end-to-end)
```

## Spec decisions made here (documented ambiguities)

- **Worked-example value:** `METRICS.md §1` prints `177.9°`, but that follows from its *rounded* intermediates (`cos = -0.99930`). Full-precision arithmetic over the same keypoints — "the exact arithmetic an implementer must reproduce" — gives `cos = -0.99915` → **177.6°**. The engine computes at full precision; tests assert 177.6 and document the drift.
- **Confidence aggregation:** `min` of joint scores (METRICS.md §0 `[CONFIRM]` → resolved to min).
- **`triggered_by.threshold`:** always the good-band boundary (150.0) with `gte`/`lt`, matching the literal examples in `API_CONTRACT.md §4c` and `METRICS.md §9.1` (where `elbow_too_bent` at 118.3° reports `threshold: 150.0`).
- **Low-confidence guard:** the optional `elbow_low_confidence` **info** tip *is* emitted (product choice left open in `METRICS.md §9.2`); no corrective tip fires.
- **Tip severities/copy:** from `METRICS.md §9.2` (authoritative for rules) — `elbow_too_bent` is `suggestion`, though the older API-contract example shows `info`.
- **Keyframe refinement:** stub uses the `MODELS.md §3.2` no-2D-keypoints fallback — trust `contact_timestamp_ms` verbatim, `contact_confidence` = edge value (else 0.5).
- **`/v1/health` gpu block:** truthful `{"name": "none (local dev stub)", ...}` until the real pipeline reports the A10G.
