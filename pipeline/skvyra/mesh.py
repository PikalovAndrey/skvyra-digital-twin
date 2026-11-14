"""Triangle-soup mesh accumulation and a minimal, dependency-free GLB 2.0 writer.

Meshes are non-indexed triangle soups with flat (per-face) normals: buildings and roofs are faceted
anyway, and it keeps per-face attributes (colour, feature id) trivial. Vertices are float32 in the
local frame; conversion to glTF's Y-up axes happens here and only here.
"""
import json
import os
import struct

import numpy as np

from .geo import to_gltf


class Soup:
    def __init__(self):
        self.tris, self.cols, self.uvs, self.fids, self.styles = [], [], [], [], []

    def add(self, tris, color, uv=None, fid: float = -1.0, style: float = 0.0):
        """tris: (n,3,3) local xyz; color: rgb in 0..1 (one per call or (n,3)); uv: (n,3,2) or None."""
        tris = np.asarray(tris, np.float64).reshape(-1, 3, 3)
        if not len(tris):
            return
        n = len(tris)
        c = np.asarray(color, np.float32)
        self.tris.append(tris)
        self.cols.append(np.broadcast_to(c.reshape(-1, 1, 3) if c.ndim == 2 else c, (n, 3, 3)).astype(np.float32))
        self.uvs.append(np.zeros((n, 3, 2), np.float32) if uv is None else np.asarray(uv, np.float32).reshape(n, 3, 2))
        self.fids.append(np.full((n, 3), fid, np.float32))
        self.styles.append(np.full((n, 3), style, np.float32))

    def __len__(self):
        return sum(len(t) for t in self.tris)

    def arrays(self, drop_degenerate=True):
        if not self.tris:
            return None
        t = np.concatenate(self.tris)
        c, uv, f = np.concatenate(self.cols), np.concatenate(self.uvs), np.concatenate(self.fids)
        st = np.concatenate(self.styles)
        n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        ln = np.linalg.norm(n, axis=1)
        if drop_degenerate:
            keep = ln > 1e-6
            t, c, uv, f, st, n, ln = t[keep], c[keep], uv[keep], f[keep], st[keep], n[keep], ln[keep]
        n = n / ln[:, None]
        return {"pos": t.reshape(-1, 3), "nrm": np.repeat(n, 3, axis=0), "col": c.reshape(-1, 3),
                "uv": uv.reshape(-1, 2), "fid": f.reshape(-1), "style": st.reshape(-1)}


def write_glb(path, primitives: list[dict], images: dict | None = None, extras: dict | None = None):
    """primitives: [{name, soup: Soup, material: {name, rough, metal, color, double, texture?, alpha?},
                     uv: bool, fid: bool}]. images: {name: png bytes}."""
    bin_chunks, buffer_views, accessors, meshes, nodes, materials, textures, imgs = [], [], [], [], [], [], [], []
    offset = 0

    def add_view(data: bytes, target=None):
        nonlocal offset
        pad = (-len(data)) % 4
        bin_chunks.append(data + b"\0" * pad)
        bv = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target:
            bv["target"] = target
        buffer_views.append(bv)
        offset += len(data) + pad
        return len(buffer_views) - 1

    def add_acc(arr: np.ndarray, typ: str, minmax=False, u16=False):
        if u16:  # normalized unsigned short (colours): half the size of float, no visible banding
            arr = np.ascontiguousarray(np.clip(np.round(np.asarray(arr) * 65535), 0, 65535), np.uint16)
            acc = {"bufferView": add_view(arr.tobytes(), 34962), "componentType": 5123, "normalized": True,
                   "count": len(arr), "type": typ}
            accessors.append(acc)
            return len(accessors) - 1
        arr = np.ascontiguousarray(arr, np.float32)
        acc = {"bufferView": add_view(arr.tobytes(), 34962), "componentType": 5126, "count": len(arr), "type": typ}
        if minmax:
            acc["min"] = arr.min(axis=0).tolist()
            acc["max"] = arr.max(axis=0).tolist()
        accessors.append(acc)
        return len(accessors) - 1

    img_index = {}
    for name, data in (images or {}).items():
        mime = "image/jpeg" if data[:2] == b"\xff\xd8" else "image/png"
        imgs.append({"bufferView": add_view(data), "mimeType": mime, "name": name})
        textures.append({"source": len(imgs) - 1, "sampler": 0})
        img_index[name] = len(textures) - 1

    for prim in primitives:
        a = prim["soup"].arrays()
        if a is None or not len(a["pos"]):
            continue
        m = prim["material"]
        mat = {"name": m["name"], "doubleSided": bool(m.get("double", False)),
               "pbrMetallicRoughness": {"baseColorFactor": list(m.get("color", [1, 1, 1])) + [m.get("alpha", 1.0)],
                                        "metallicFactor": m.get("metal", 0.0), "roughnessFactor": m.get("rough", 0.9)}}
        if m.get("texture") in img_index:
            mat["pbrMetallicRoughness"]["baseColorTexture"] = {"index": img_index[m["texture"]]}
        if m.get("alpha", 1.0) < 1.0:
            mat["alphaMode"] = "BLEND"
        materials.append(mat)
        attrs = {"POSITION": add_acc(to_gltf(a["pos"]), "VEC3", True),
                 "COLOR_0": add_acc(np.column_stack([a["col"], np.ones(len(a["col"]))]), "VEC4", u16=True)}
        if prim.get("normals", False):  # faceted surfaces: renderers derive flat normals (smaller, faster upload)
            attrs["NORMAL"] = add_acc(to_gltf(a["nrm"]), "VEC3")
        if prim.get("uv"):
            attrs["TEXCOORD_0"] = add_acc(a["uv"], "VEC2")
        if prim.get("fid"):
            attrs["_FEATURE_ID"] = add_acc(a["fid"], "SCALAR")
        if prim.get("style"):
            attrs["_STYLE"] = add_acc(a["style"], "SCALAR")  # facade type (walls) / roof material (roofs)
        meshes.append({"name": prim["name"], "primitives": [{"attributes": attrs, "material": len(materials) - 1, "mode": 4}]})
        nodes.append({"name": prim["name"], "mesh": len(meshes) - 1})

    gl = {"asset": {"version": "2.0", "generator": "skvyra-digital-twin pipeline"},
          "scene": 0, "scenes": [{"nodes": list(range(len(nodes))), "extras": extras or {}}],
          "nodes": nodes, "meshes": meshes, "materials": materials, "accessors": accessors,
          "bufferViews": buffer_views, "buffers": [{"byteLength": offset}]}
    if textures:
        gl.update(textures=textures, images=imgs,
                  samplers=[{"magFilter": 9729, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071}])
    js = json.dumps(gl, separators=(",", ":")).encode()
    js += b" " * ((-len(js)) % 4)
    binary = b"".join(bin_chunks)
    total = 12 + 8 + len(js) + 8 + len(binary)
    tmp = f"{path}.part"
    with open(tmp, "wb") as f:  # atomic: a reader (viewer, Blender) never sees a half-written tile
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
        f.write(struct.pack("<II", len(binary), 0x004E4942) + binary)
    os.replace(tmp, path)
    return total


def read_glb_json(path) -> dict:
    """Parse the JSON chunk of a GLB (used by tests and validation)."""
    with open(path, "rb") as f:
        magic, ver, total = struct.unpack("<III", f.read(12))
        if magic != 0x46546C67 or ver != 2:
            raise ValueError(f"{path}: not a glTF 2.0 binary")
        ln, typ = struct.unpack("<II", f.read(8))
        return json.loads(f.read(ln))


def hex_rgb(h: str):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]


def srgb_to_linear(c):
    c = np.asarray(c, np.float32)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
