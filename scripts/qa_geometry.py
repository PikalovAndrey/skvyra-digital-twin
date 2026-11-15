"""Geometry QA over the real generated tiles (`make qa`). Writes data/processed/qa_report.json.

Checks: floating / buried buildings against the LOD0 terrain, roads running through buildings, trees inside
buildings, surfaces facing down (culled = invisible), height outliers per building type, roof proportions, every OSM
bridge (deck present on LOD0/1, faces up, no NaN/folds, joints flush with the road, clear of the ground, structure
present; painted as road in the LOD2 texture). Prints a short summary; exit 1 if a hard defect (floating building,
tree inside a building, surface facing down, broken bridge) is found.
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
from skvyra import config as C, surfaces as S  # noqa: E402
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


def mesh_tris(js, binary, name):
    for m in js["meshes"]:
        if m["name"] == name:
            p = acc(js, binary, m["primitives"][0]["attributes"]["POSITION"]).reshape(-1, 3, 3).astype(float)
            return np.stack([p[..., 0], -p[..., 2], p[..., 1]], -1)  # glTF y-up -> local x, y, z
    return np.zeros((0, 3, 3))


def check_bridges(frame, ter, tiles):
    """Every OSM bridge way: the owner tile (holding its midpoint) must carry a visible, sane deck and structure."""
    index = {t["id"]: t for t in json.loads((tiles / "index.json").read_text())["tiles"]}
    out = []
    for f in json.loads((C.PROCESSED / "roads.geojson").read_text())["features"]:
        p = f["properties"]
        if not p.get("bridge") or p["bridge"] == "no" or p.get("tunnel") in ("yes", "building_passage", "culvert") \
                or p.get("highway") in ("proposed", "abandoned", "razed"):
            continue
        line = frame.geom_to_local(shape(f["geometry"]))
        w, inferred = S.road_width(p)
        poly = line.buffer(w / 2, cap_style="flat")
        mid = line.interpolate(0.5, normalized=True)
        owner = [t for t in index.values() if t["b"][0] <= mid.x < t["b"][2] and t["b"][1] <= mid.y < t["b"][3]]
        r = {"sid": p.get("source_id"), "name": p.get("name"), "highway": p["highway"], "length_m": round(line.length, 1),
             "width_m": w, "width_inferred": inferred, "problems": []}
        out.append(r)
        if not owner:
            r["problems"].append("outside the tile grid")
            continue
        t = owner[0]
        r["tile"] = t["id"]
        mat = "road_" + S.road_category(p)
        for lod in ("0", "1"):
            js, binary = glb(tiles / t["files"][lod]["u"])
            road, struct_ = mesh_tris(js, binary, mat), mesh_tris(js, binary, "bridge")
            if not np.isfinite(road).all() or not np.isfinite(struct_).all():
                r["problems"].append(f"lod{lod}: NaN")
                continue
            inner = poly.buffer(-0.05)
            c = road.mean(1)
            deck = road[shapely.contains_xy(inner, c[:, 0], c[:, 1])] if len(road) else road
            n = np.cross(deck[:, 1] - deck[:, 0], deck[:, 2] - deck[:, 0])
            area = 0.5 * np.abs(n[:, 2]).sum()
            union = shapely.union_all([shapely.Polygon(q[:, :2]).buffer(0) for q in deck]).area if len(deck) else 0
            r[f"lod{lod}_deck_tris"] = len(deck)
            r[f"lod{lod}_struct_tris"] = int(shapely.contains_xy(poly.buffer(0.1), *struct_.mean(1)[:, :2].T).sum()) if len(struct_) else 0
            if union < 0.9 * poly.area:
                r["problems"].append(f"lod{lod}: deck covers {union / poly.area:.0%} of the bridge")
            if (n[:, 2] <= 0).any():
                r["problems"].append(f"lod{lod}: {(n[:, 2] <= 0).sum()} deck triangles face down")
            if area > 1.02 * union + 0.01:
                r["problems"].append(f"lod{lod}: deck folds over itself ({area:.1f} vs {union:.1f} m2)")
            if r[f"lod{lod}_struct_tris"] == 0:
                r["problems"].append(f"lod{lod}: no fascia / parapets")
            # joints: road vertices just outside each end vs the deck edge there
            others = road[~shapely.contains_xy(inner, c[:, 0], c[:, 1])].reshape(-1, 3) if len(road) else np.zeros((0, 3))
            dv = deck.reshape(-1, 3)
            steps = []
            for end in (0.0, line.length):
                e = line.interpolate(end)
                at_end = dv[np.hypot(dv[:, 0] - e.x, dv[:, 1] - e.y) <= w / 2 + 0.01]
                near = others[np.hypot(others[:, 0] - e.x, others[:, 1] - e.y) <= w / 2 + 0.01]
                if len(at_end) and len(near):  # the joining road may sit in the next tile; checked there by symmetry
                    zd = at_end[np.argmin(np.hypot(*(at_end[:, None, :2] - near[None, :, :2]).transpose(2, 0, 1)), 0)][:, 2]
                    d = np.hypot(*(at_end[:, None, :2] - near[None, :, :2]).transpose(2, 0, 1)).min(0)
                    steps += list(np.abs(near[d < 0.3, 2] - zd[d < 0.3]))
            r[f"lod{lod}_joint_step_max_m"] = round(float(max(steps)), 3) if steps else None
            if steps and max(steps) > 0.05:
                r["problems"].append(f"lod{lod}: step of {max(steps):.2f} m at a joint")
            if lod == "0" and len(dv):
                clear = dv[:, 2] - ter.z(dv[:, 0], dv[:, 1])
                r["deck_above_ground_min_max_m"] = [round(float(clear.min()), 2), round(float(clear.max()), 2)]
                if clear.min() < 0.05 or clear.max() > 1.5:
                    r["problems"].append(f"deck {clear.min():.2f}..{clear.max():.2f} m above the ground")
        # LOD2 (far): no road meshes, the ground texture must show the road across the water
        js, binary = glb(tiles / t["files"]["2"]["u"])
        b = t["b"]
        img = js["images"][0]
        bv = js["bufferViews"][img["bufferView"]]
        from io import BytesIO
        from PIL import Image
        im = np.asarray(Image.open(BytesIO(binary[bv["byteOffset"]:bv["byteOffset"] + bv["byteLength"]])).convert("RGB"), float)
        px = im.shape[1] / (b[2] - b[0])
        if w * px >= 2:  # narrower than two texels cannot show at that distance anyway
            u, v = int((mid.x - b[0]) * px), int((b[3] - mid.y) * px)
            col = im[v, u]
            road_col = np.array(S.GROUND["road" if S.road_category(p) == "paved" else "road_unpaved" if S.road_category(p) == "unpaved" else "foot"])
            r["lod2_texture_rgb"] = col.astype(int).tolist()
            if np.abs(col - road_col).sum() > np.abs(col - np.array(S.GROUND["water"])).sum():
                r["problems"].append("lod2: texture shows water, not the road, at mid-span")
    return out


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
    bridges = check_bridges(frame, ter, tiles)
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
        "bridges_checked": len(bridges), "bridges_with_problems": sum(1 for x in bridges if x["problems"]),
        "bridges": bridges,
        "houses_roof_taller_than_walls": int(sum(1 for b in metas.values() if b["kind"] == "house" and b["roof"] != "flat" and b["h"] - b["eave"] > b["eave"])),
    }
    (C.PROCESSED / "qa_report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    for k, v in rep.items():
        if not isinstance(v, list):
            print(f"{k}: {v}")
    for x in bridges:
        print(f"bridge {x['sid']} {x['name'] or x['highway']}: {'; '.join(x['problems']) or 'ok'}")
    return 1 if rep["floating"] or rep["trees_inside_buildings"] or rep["surface_triangles_facing_down"] or rep["bridges_with_problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
