"""Placeholder GLB (binary glTF 2.0) generator for the stub pipeline.

Produces a small but fully valid GLB (a colored tetrahedron, +Y up, meters)
so the client's GLTFLoader path is exercised end-to-end before the real
SAM 3D Body mesh export (MODELS.md §4.3) replaces it.
"""

from __future__ import annotations

import json
import struct

GLB_VERTEX_COUNT = 4  # truthful vertex_count for the placeholder mesh

_POSITIONS = [
    (0.0, 0.0, 0.0),
    (0.3, 0.0, 0.0),
    (0.15, 0.5, 0.1),
    (0.0, 0.0, 0.3),
]
_INDICES = [0, 2, 1, 0, 1, 3, 0, 3, 2, 1, 2, 3]


def placeholder_glb() -> bytes:
    pos_bytes = b"".join(struct.pack("<fff", *p) for p in _POSITIONS)
    idx_bytes = struct.pack(f"<{len(_INDICES)}H", *_INDICES)
    bin_chunk = pos_bytes + idx_bytes
    bin_chunk += b"\x00" * (-len(bin_chunk) % 4)

    mins = [min(p[i] for p in _POSITIONS) for i in range(3)]
    maxs = [max(p[i] for p in _POSITIONS) for i in range(3)]

    gltf = {
        "asset": {"version": "2.0", "generator": "servebot-stub-pipeline"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "contact_placeholder"}],
        "meshes": [
            {"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}
        ],
        "materials": [
            {
                "pbrMetallicRoughness": {
                    "baseColorFactor": [0.55, 0.65, 0.85, 1.0],
                    "metallicFactor": 0.0,
                    "roughnessFactor": 0.9,
                },
                "doubleSided": True,
            }
        ],
        "buffers": [{"byteLength": len(bin_chunk)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos_bytes), "target": 34962},
            {
                "buffer": 0,
                "byteOffset": len(pos_bytes),
                "byteLength": len(idx_bytes),
                "target": 34963,
            },
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,  # FLOAT
                "count": len(_POSITIONS),
                "type": "VEC3",
                "min": mins,
                "max": maxs,
            },
            {
                "bufferView": 1,
                "componentType": 5123,  # UNSIGNED_SHORT
                "count": len(_INDICES),
                "type": "SCALAR",
            },
        ],
    }

    json_chunk = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_chunk += b" " * (-len(json_chunk) % 4)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack("<I", len(data)) + tag + data

    body = chunk(b"JSON", json_chunk) + chunk(b"BIN\x00", bin_chunk)
    header = struct.pack("<4sII", b"glTF", 2, 12 + len(body))
    return header + body
