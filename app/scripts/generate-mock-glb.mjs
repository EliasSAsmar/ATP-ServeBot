// Generates src/assets/mock-contact.glb — a low-poly humanoid frozen at a
// serve-contact pose (serving arm extended overhead). Used ONLY by the mock
// API mode as a stand-in for the SAM 3D Body mesh. Meters, +Y up, feet at y=0
// (matches API_CONTRACT.md §0 coordinate conventions).
//
// Hand-rolls the GLB container (no deps): header + JSON chunk + BIN chunk.
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const positions = [];
const normals = [];
const indices = [];

function sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
function cross(a, b) {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}
function norm(a) {
  const l = Math.hypot(...a) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
}

/** Oriented box along segment start->end with square cross-section `thick`. */
function limb(start, end, thick) {
  const axis = norm(sub(end, start));
  // pick a helper not parallel to axis
  const helper = Math.abs(axis[1]) > 0.9 ? [1, 0, 0] : [0, 1, 0];
  const u = norm(cross(axis, helper));
  const v = norm(cross(axis, u));
  const h = thick / 2;
  const corners = [];
  for (const p of [start, end]) {
    for (const [su, sv] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
      corners.push([
        p[0] + u[0] * su * h + v[0] * sv * h,
        p[1] + u[1] * su * h + v[1] * sv * h,
        p[2] + u[2] * su * h + v[2] * sv * h,
      ]);
    }
  }
  // 6 quad faces of the hexahedron (corner indices)
  const faces = [
    [0, 3, 2, 1],       // start cap
    [4, 5, 6, 7],       // end cap
    [0, 1, 5, 4],
    [1, 2, 6, 5],
    [2, 3, 7, 6],
    [3, 0, 4, 7],
  ];
  for (const f of faces) {
    const base = positions.length / 3;
    const [a, b, c, d] = f.map((i) => corners[i]);
    const n = norm(cross(sub(b, a), sub(d, a)));
    for (const p of [a, b, c, d]) {
      positions.push(...p);
      normals.push(...n);
    }
    indices.push(base, base + 1, base + 2, base, base + 2, base + 3);
  }
}

function box(center, size) {
  const [cx, cy, cz] = center;
  const [sx, sy, sz] = size;
  limb([cx, cy - sy / 2, cz], [cx, cy + sy / 2, cz], Math.max(sx, sz));
}

// --- humanoid at contact: right (serving) arm reaching overhead -------------
limb([-0.10, 0.95, 0], [-0.12, 0.05, 0.02], 0.10);   // left leg
limb([0.10, 0.95, 0], [0.14, 0.05, -0.02], 0.10);    // right leg
limb([0, 0.92, 0], [0, 1.44, 0.02], 0.26);           // torso
box([0.01, 1.58, 0.03], [0.17, 0.19, 0.17]);         // head
limb([-0.17, 1.40, 0.01], [-0.30, 1.14, 0.06], 0.07); // left upper arm (toss arm coming down)
limb([-0.30, 1.14, 0.06], [-0.36, 0.92, 0.10], 0.06); // left forearm
limb([0.17, 1.41, 0.0], [0.32, 1.69, -0.03], 0.07);   // right upper arm (raised)
limb([0.32, 1.69, -0.03], [0.45, 1.97, -0.07], 0.06); // right forearm -> wrist at contact

// --- pack GLB ----------------------------------------------------------------
const posArr = new Float32Array(positions);
const nrmArr = new Float32Array(normals);
const idxArr = new Uint16Array(indices);

function pad4(n, fill = 0) { return (4 - (n % 4)) % 4; }

const binParts = [Buffer.from(posArr.buffer), Buffer.from(nrmArr.buffer), Buffer.from(idxArr.buffer)];
const views = [];
let off = 0;
for (const part of binParts) {
  views.push({ buffer: 0, byteOffset: off, byteLength: part.byteLength });
  off += part.byteLength + pad4(part.byteLength);
}
const bin = Buffer.alloc(off);
let o = 0;
for (const part of binParts) { part.copy(bin, o); o += part.byteLength + pad4(part.byteLength); }

const mins = [Infinity, Infinity, Infinity];
const maxs = [-Infinity, -Infinity, -Infinity];
for (let i = 0; i < posArr.length; i += 3) {
  for (let k = 0; k < 3; k++) {
    mins[k] = Math.min(mins[k], posArr[i + k]);
    maxs[k] = Math.max(maxs[k], posArr[i + k]);
  }
}

const gltf = {
  asset: { version: "2.0", generator: "atp-servebot mock-glb generator" },
  scene: 0,
  scenes: [{ nodes: [0] }],
  nodes: [{ mesh: 0, name: "mock_player_contact" }],
  meshes: [{ primitives: [{ attributes: { POSITION: 0, NORMAL: 1 }, indices: 2, material: 0 }] }],
  materials: [{
    name: "clay",
    pbrMetallicRoughness: { baseColorFactor: [0.83, 0.55, 0.38, 1.0], metallicFactor: 0.0, roughnessFactor: 0.85 },
  }],
  buffers: [{ byteLength: bin.byteLength }],
  bufferViews: views,
  accessors: [
    { bufferView: 0, componentType: 5126, count: posArr.length / 3, type: "VEC3", min: mins, max: maxs },
    { bufferView: 1, componentType: 5126, count: nrmArr.length / 3, type: "VEC3" },
    { bufferView: 2, componentType: 5123, count: idxArr.length, type: "SCALAR" },
  ],
};

let json = Buffer.from(JSON.stringify(gltf), "utf8");
if (json.byteLength % 4) json = Buffer.concat([json, Buffer.alloc(pad4(json.byteLength), 0x20)]);

const total = 12 + 8 + json.byteLength + 8 + bin.byteLength;
const out = Buffer.alloc(total);
out.writeUInt32LE(0x46546c67, 0); // 'glTF'
out.writeUInt32LE(2, 4);
out.writeUInt32LE(total, 8);
out.writeUInt32LE(json.byteLength, 12);
out.writeUInt32LE(0x4e4f534a, 16); // 'JSON'
json.copy(out, 20);
let p = 20 + json.byteLength;
out.writeUInt32LE(bin.byteLength, p);
out.writeUInt32LE(0x004e4942, p + 4); // 'BIN'
bin.copy(out, p + 8);

const dest = join(dirname(dirname(fileURLToPath(import.meta.url))), "src", "assets", "mock-contact.glb");
mkdirSync(dirname(dest), { recursive: true });
writeFileSync(dest, out);
console.log(`[generate-mock-glb] wrote ${dest} (${out.byteLength} bytes, ${posArr.length / 3} verts)`);
