// Downloads the MediaPipe Pose Landmarker model into public/models/ so the
// app can run fully offline. If the file is already present this is a no-op.
// The app falls back to Google's model CDN at runtime if the local copy is
// missing, so this script is a convenience, not a hard requirement.
import { createWriteStream, existsSync, mkdirSync, statSync, unlinkSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { get } from "node:https";

const URL_ =
  "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task";
const root = dirname(dirname(fileURLToPath(import.meta.url)));
const dest = join(root, "public", "models", "pose_landmarker_lite.task");

if (existsSync(dest) && statSync(dest).size > 1_000_000) {
  console.log("[fetch-pose-model] already present:", dest);
  process.exit(0);
}
mkdirSync(dirname(dest), { recursive: true });

function download(url, redirects = 0) {
  if (redirects > 5) throw new Error("too many redirects");
  get(url, (res) => {
    if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
      download(res.headers.location, redirects + 1);
      return;
    }
    if (res.statusCode !== 200) {
      console.error("[fetch-pose-model] HTTP", res.statusCode, "- app will use the CDN fallback at runtime.");
      process.exit(0);
    }
    const out = createWriteStream(dest);
    res.pipe(out);
    out.on("finish", () => console.log("[fetch-pose-model] saved ->", dest));
    out.on("error", (e) => {
      try { unlinkSync(dest); } catch {}
      console.error("[fetch-pose-model] write failed:", e.message);
    });
  }).on("error", (e) => {
    console.error("[fetch-pose-model] download failed:", e.message, "- app will use the CDN fallback at runtime.");
  });
}
download(URL_);
