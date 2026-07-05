// Copies the MediaPipe Tasks Vision WASM runtime out of node_modules into
// public/ so the pose landmarker can load without any CDN (edge independence).
// Runs automatically on `npm install` (postinstall).
import { cpSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const src = join(root, "node_modules", "@mediapipe", "tasks-vision", "wasm");
const dest = join(root, "public", "mediapipe", "wasm");

if (!existsSync(src)) {
  console.warn("[copy-mediapipe-wasm] source not found (did install fail?):", src);
  process.exit(0);
}
mkdirSync(dest, { recursive: true });
cpSync(src, dest, { recursive: true });
console.log("[copy-mediapipe-wasm] copied wasm runtime ->", dest);
