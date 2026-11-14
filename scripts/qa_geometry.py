"""Geometry QA over the real generated tiles (`make qa`). Writes data/processed/qa_report.json.

Checks: floating / buried buildings against the LOD0 terrain, roads running through buildings, trees inside
buildings, surfaces facing down (culled = invisible), height outliers per building type, roof proportions. Prints a short summary; exit 1 if a hard
defect (floating building, tree inside a building, surface facing down) is found.
"""
import glob
import json
import struct
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
from skvyra import config as C  # noqa: E402
from skvyra.geo import Frame  # noqa: E402
from skvyra.terrain import Terrain  # noqa: E402


def glb(path):
    data = Path(path).read_bytes()
    jl = struct.unpack("<I", data[12:16])[0]
    js = json.loads(data[20:20 + jl])
    return js, data[20 + jl + 8:]


def acc(js, binary, i):
    a = js["accessors"][i]
    bv = js["bufferViews"][a["bufferView"]]
    n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
    arr = np.frombuffer(binary, np.float32, a["count"] * n, bv["byteOffset"])
    return arr.reshape(-1, n) if n > 1 else arr


def main():
    cfg = C.load()
    frame = Frame(cfg["crs_metric"], cfg["origin_utm"])
    dtm = C.PROCESSED / "dtm.tif"
    ter = Terrain(dtm if dtm.exists() else C.RAW / "dem_copernicus_glo30.tif", frame, step=10, verbose=False)
    tiles = C.GENERATED / "tiles"
    feats = json.loads((C.PROCESSED / "buildings.geojson").read_text())["features"]
    geom = {f["properties"]["id"]: frame.geom_to_local(shape(f["geometry"])) for f in feats}
    metas = {}
    floating, buried, wall_low = [], [], []
    for meta_path in sorted(tiles.glob("t*.json")):
        meta = json.loads(meta_path.read_text())
        for b in meta["buildings"]:
            metas[b["id"]] = b
        js, binary = glb(tiles / meta["files"]["0"]["u"])
        for m in js["meshes"]:
            if m["name"] != "walls":
                continue
            at = m["primitives"][0]["attributes"]
            pos, fid = acc(js, binary, at["POSITION"]), acc(js, binary, at["_FEATURE_ID"])
            for f in np.unique(fid):
                p = pos[fid == f]
                b = meta["buildings"][int(f)]
                g = geom[b["id"]]
                ring = np.asarray(g.exterior.coords)
                gz = ter.z(ring[:, 0], ring[:, 1])
                bottom = float(p[:, 1].min())
                if bottom > gz.min() + 1e-3:
                    floating.append(b["sid"])
                eave_z = gz.mean() + b["eave"]
                clearance = float(eave_z - gz.max())  # wall height on the uphill side
                if clearance < 1.8:
                    (buried if clearance < 0.5 else wall_low).append((b["sid"], round(clearance, 2), round(float(gz.max() - gz.min()), 2)))
    # roads through buildings (inferred widths) and trees inside buildings
    # check the *generated* road meshes (LOD0), not the source centre lines
    tree = shapely.STRtree(list(geom.values()))
    ids = list(geom)
    overlap = []
    for meta_path in sorted(tiles.glob("t*.json")):
        meta = json.loads(meta_path.read_text())
        js, binary = glb(tiles / meta["files"]["0"]["u"])
        tris = []
        for m in js["meshes"]:
            if m["name"] in ("road_paved", "road_unpaved"):
                pos = acc(js, binary, m["primitives"][0]["attributes"]["POSITION"])
                xy = np.column_stack([pos[:, 0], -pos[:, 2]]).reshape(-1, 3, 2)
                tris += [shapely.Polygon(t) for t in xy]
        if not tris:
            continue
        surf = shapely.union_all([t.buffer(0) for t in tris if t.area > 1e-6])
        for i in tree.query(surf, predicate="intersects"):
            a_ = geom[ids[i]].intersection(surf).area
            if a_ > 1.0:
                overlap.append((metas[ids[i]]["sid"], round(a_, 1), round(a_ / geom[ids[i]].area, 3)))
    # water: generated LOD0 water surface vs the source water polygons (OSM areas + buffered waterways)
    areas = [(frame.geom_to_local(shape(f["geometry"])), f["properties"]) for f in json.loads((C.PROCESSED / "areas.geojson").read_text())["features"]]
    src_water = shapely.union_all([g for g, p in areas if p["cover"] == "water"])
    water_src_area, water_mesh_area, water_outside = 0.0, 0.0, 0.0
    for meta_path in sorted(tiles.glob("t*.json")):
        meta = json.loads(meta_path.read_text())
        b = meta["bounds"]
        js, binary = glb(tiles / meta["files"]["0"]["u"])
        for m in js["meshes"]:
            if m["name"] != "water":
                continue
            pos = acc(js, binary, m["primitives"][0]["attributes"]["POSITION"])
            xy = np.column_stack([pos[:, 0], -pos[:, 2]]).reshape(-1, 3, 2)
            surf = shapely.union_all([shapely.Polygon(t).buffer(0) for t in xy])
            water_mesh_area += surf.area
            water_src_area += shapely.clip_by_rect(src_water, *b).area
            water_outside += surf.difference(src_water.buffer(5.0)).area  # beyond OSM areas + stream buffers
    # surfaces must face up: front faces are counter-clockwise from above; a clockwise ground/water/road triangle
    # is back-face culled in the viewer and simply disappears (flat ponds did, before 2026-11-12)
    facing_down = Counter()
    for meta_path in sorted(tiles.glob("t*.json")):
        meta = json.loads(meta_path.read_text())
        for lod, f in meta["files"].items():
            js, binary = glb(tiles / f["u"])
            for m in js["meshes"]:
                if m["name"] in ("terrain", "water", "road_paved", "road_unpaved", "road_foot", "road_shoulder", "rail"):
                    t = acc(js, binary, m["primitives"][0]["attributes"]["POSITION"]).reshape(-1, 3, 3)
                    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
                    ln = np.linalg.norm(n, axis=1)  # ignore float32 slivers (< 0.01 m2) and near-vertical skirts
                    facing_down[f"{m['name']}@lod{lod}"] += int(((n[:, 1] < -0.5 * ln) & (ln > 0.02)).sum())
    trees_in = 0
    for meta_path in sorted(tiles.glob("t*.json")):
        for x, y, *_ in json.loads(meta_path.read_text())["trees"]:
            pt = shapely.Point(x, y)
            if any(geom[ids[j]].contains(pt) for j in tree.query(pt)):
                trees_in += 1
    # heights and roof proportions
    hk = {}
    for b in metas.values():
        hk.setdefault(b["kind"], []).append(b["h"])
    rr = np.array([(b["h"] - b["eave"]) / max(b["eave"], 0.1) for b in metas.values() if b["roof"] != "flat"])
    rh = np.array([b["h"] - b["eave"] for b in metas.values() if b["roof"] != "flat" and b["kind"] == "house"])
    outliers = [(b["sid"], b["kind"], b["h"], b["hM"]) for b in metas.values()
                if (b["kind"] == "house" and b["h"] > 11) or (b["kind"] == "outbuilding" and b["h"] > 6)
                or (b["kind"] in ("public", "industrial", "commercial") and b["h"] < 3)]
    rep = {
        "buildings_checked": len(metas),
        "floating": len(floating), "floating_ids": floating[:20],
        "buried_eave_below_0_5m_uphill": len(buried), "buried": buried[:20],
        "low_uphill_wall_0_5_to_1_8m": len(wall_low), "low_wall_examples": wall_low[:20],
        "buildings_overlapping_paved_roads_gt_1m2": len(overlap),
        "road_overlap_worst": sorted(overlap, key=lambda o: -o[1])[:15],
        "trees_inside_buildings": trees_in,
        "surface_triangles_facing_down": sum(facing_down.values()),
        "facing_down_by_mesh": {k: v for k, v in facing_down.items() if v},
        "water_mesh_area_m2": round(water_mesh_area), "water_source_polygon_area_m2": round(water_src_area),
        "water_mesh_outside_source_plus_5m_m2": round(water_outside),
        "height_by_kind_p5_p50_p95": {k: [round(float(np.percentile(v, q)), 1) for q in (5, 50, 95)] for k, v in hk.items()},
        "height_outliers": outliers[:30], "height_outliers_count": len(outliers),
        "roof_rise_to_eave_ratio_p50_p95_max": [round(float(np.percentile(rr, q)), 2) for q in (50, 95)] + [round(float(rr.max()), 2)],
        "house_roof_rise_m_p50_p95_max": [round(float(np.percentile(rh, q)), 2) for q in (50, 95)] + [round(float(rh.max()), 2)],
        "houses_roof_taller_than_walls": int(sum(1 for b in metas.values() if b["kind"] == "house" and b["roof"] != "flat" and b["h"] - b["eave"] > b["eave"])),
    }
    (C.PROCESSED / "qa_report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    for k, v in rep.items():
        if not isinstance(v, list):
            print(f"{k}: {v}")
    return 1 if rep["floating"] or rep["trees_inside_buildings"] or rep["surface_triangles_facing_down"] else 0


if __name__ == "__main__":
    sys.exit(main())
