# app/ — Live tier (React)

The edge tier that runs entirely in the browser. **No GPU, no hard cloud dependency.**

- Camera capture (`getUserMedia`) + MediaPipe Pose Landmarker (~30fps) → skeleton overlay
- Serve auto-detect heuristic (hitting-wrist vertical velocity + arm elevation)
- Clip capture via a `MediaRecorder` ring buffer (two staggered recorders) + `contact_timestamp_ms` at the peak of the reach
- Upload orchestration → job polling → three.js GLB render of the contact-frame mesh

Built against [`../design/UI.md`](../design/UI.md) and [`../design/API_CONTRACT.md`](../design/API_CONTRACT.md)
(Milestone v1 Step 1 + the Step 6 UI shell).

## Run it

```bash
cd app
npm install        # also copies the MediaPipe WASM runtime into public/
npm run dev        # → http://localhost:5173
```

`npm install` runs a postinstall that copies the MediaPipe WASM runtime out of
`node_modules` into `public/mediapipe/wasm`. The pose model itself is fetched with:

```bash
npm run fetch-pose-model   # downloads pose_landmarker_lite.task into public/models/
```

If the local model file is missing, the app falls back to Google's model CDN at
runtime — so `npm install && npm run dev` works either way.

Other scripts:

```bash
npm run build      # type-check + production build → dist/
npm run preview    # serve the production build
node scripts/generate-mock-glb.mjs   # regenerate the mock placeholder mesh
```

## Mock API vs real backend

The app talks to the backend through one interface (`src/api/types.ts`) with two
implementations:

- **Mock mode (default)** — `src/api/mock.ts` simulates the entire
  `API_CONTRACT.md §6` sequence in the browser (presigned upload → PUT →
  create job → `queued`/`running` stage progression → `succeeded` result with a
  bundled placeholder GLB). The whole story — camera → live skeleton → serve
  capture → Analyzing → rotatable 3D result + elbow angle + tip — works with
  **no backend and no network**.
- **Real mode** — `src/api/real.ts` calls the FastAPI service in `../server/`
  (`X-API-Key` on every `/v1` call, direct-to-S3 presigned PUT/GET).

Toggle at runtime in **Settings → Backend → Mock API mode**, or set the default
via env vars.

## Configuration (env vars)

Create `app/.env.local` (see `.env.example`):

| Var | Default | Meaning |
|---|---|---|
| `VITE_API_BASE_URL` | `http://localhost:8000` | Backend base URL (real mode). |
| `VITE_API_KEY` | *(empty)* | `X-API-Key` sent on all `/v1` calls (real mode). |
| `VITE_MOCK_API` | `true` | Start in mock mode (`"false"` to default to the real backend). |

All three are only **defaults** — the Settings screen can override them per
browser (persisted in `localStorage`).

## Source layout

```
src/
  types/api.ts        TypeScript mirror of API_CONTRACT.md (payload shapes)
  api/                ServeApi interface + real (HTTP) and mock implementations
  flow/analysis.ts    the §6 client sequence: upload → PUT → create → poll (backoff)
  pose/               MediaPipe Pose Landmarker wrapper + canvas skeleton drawing
  detect/             serve auto-detect heuristic (tunable thresholds in one object)
  capture/            MediaRecorder ring buffer (two staggered recorders)
  screens/            Setup / Live / Analyzing / Result / Settings (UI.md §1)
  components/         cloud status chip, GLB viewer, metric card, tip cards
  assets/             mock-contact.glb — placeholder mesh for mock mode
scripts/              wasm copy, pose-model fetch, mock-GLB generator
```

## Product stance (do not regress)

Every 3D/metric surface keeps the **"inferred, not measured"** framing
(`OVERVIEW.md §5`): the viewer carries a persistent *"AI 3D estimate — single
camera"* chip, angles render as `~{value}°` with at most one decimal, and copy
stays directional — never clinical.
