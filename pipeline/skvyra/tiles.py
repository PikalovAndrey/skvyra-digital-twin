"""Tile generation: per tile and LOD, one GLB (terrain, buildings, roads, water, rail) plus one JSON with
feature metadata and tree instances. Tiles are rebuilt only when their inputs change.

Ownership: a building belongs to the tile containing its representative point (never split, never
duplicated); line/area features are clipped at tile borders and draped onto the shared terrain lattice,
so they meet seamlessly. Tile ids encode the grid cell: t+3-2 = cell (3, -2) of `tile_size_m` metres.
"""
import hashlib
import json
import math
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import box, shape

from . import acquire, buildings as B, config as C, heights as H, landmarks as LM, overrides as OV, surfaces as S
from .geo import Frame
from .log import log
from .mesh import Soup, hex_rgb, srgb_to_linear, write_glb
from .terrain import FlatTerrain, Terrain, prepare_dtm

OUT = C.GENERATED / "tiles"
LODS = {0: {"step": 10.0, "tex": 1024, "q": 80}, 1: {"step": 25.0, "tex": 512, "q": 78}, 2: {"step": 50.0, "tex": 128, "q": 75}}
OFFSET = {0: 0.12, 1: 0.25, 2: 0.6}  # overlay lift above terrain per LOD (plus polygonOffset in the viewer)
WATERWAY_W = {"river": 8.0, "canal": 5.0, "stream": 2.5, "ditch": 1.5, "drain": 1.5}
LIN = lambda h: srgb_to_linear(hex_rgb(h)).tolist()
MATERIALS = {
    "terrain": {"name": "terrain", "rough": 1.0, "texture": "ground"},
    "walls": {"name": "walls", "rough": 0.9},
    "roofs": {"name": "roofs", "rough": 0.7},
    "details": {"name": "details", "rough": 0.9},
    "road_paved": {"name": "road_paved", "rough": 0.95},
    "road_unpaved": {"name": "road_unpaved", "rough": 1.0},
    "road_foot": {"name": "road_foot", "rough": 1.0},
    "bridge": {"name": "bridge", "rough": 0.9},
    "rail": {"name": "rail", "rough": 1.0},
    "water": {"name": "water", "rough": 0.1, "metal": 0.0},
    "road_shoulder": {"name": "road_shoulder", "rough": 1.0},
    "decor_fence": {"name": "decor_fence", "rough": 0.7, "double": True},  # decorative layer, not data
}
COLORS = {"road_paved": LIN("#5c5c5e"), "road_unpaved": LIN("#968265"), "road_foot": LIN("#aaa296"),
          "bridge": LIN("#8a8784"), "rail": LIN("#6e645c"), "water": LIN("#3e5c6e"), "terrain": [1, 1, 1],
          "details": LIN("#7a5244"), "road_shoulder": LIN("#8a7e68")}
WATER_DEEP, WATER_SHALLOW = np.array(LIN("#1f3d4c")), np.array(LIN("#55603f"))

_CTX = {}


def tile_id(ix, iy):
    return f"t{ix:+d}{iy:+d}"


def code_version() -> str:
    h = hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob("*.py")):
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def _load_layer(name, frame):
    feats = json.loads((C.PROCESSED / name).read_text())["features"]
    return [(frame.geom_to_local(shape(f["geometry"])), f["properties"]) for f in feats if f.get("geometry")]


def init_ctx(cfg):
    """Load all processed layers in local coordinates (once per worker process)."""
    frame = Frame(cfg["crs_metric"], cfg["origin_utm"])
    dem = dem_path()
    terrains = {}
    for lod, spec in LODS.items():
        if dem.exists():
            terrains[lod] = Terrain(dem, frame, step=spec["step"], verbose=lod == 0)
        else:
            terrains[lod] = FlatTerrain()
            terrains[lod].step = spec["step"]
    bld = _load_layer("buildings.geojson", frame)
    roads = _load_layer("roads.geojson", frame)
    areas = _load_layer("areas.geojson", frame)
    waterways = _load_layer("waterways.geojson", frame)
    rails = _load_layer("railways.geojson", frame)
    trees = _load_layer("trees.geojson", frame)
    quarantine = [(g, p) for g, p in ((frame.geom_to_local(shape(f["geometry"])), f["properties"])
                                      for f in json.loads((C.PROCESSED / "quarantine.geojson").read_text())["features"]
                                      if f.get("geometry"))]
    ov, ov_report = OV.match(OV.load(), bld, frame)
    pts = [g.representative_point() for g, _ in bld]
    lon, lat = frame.to_geo(np.array([p.x for p in pts]), np.array([p.y for p in pts]))
    evidence = H.evidence([(lo, la, g.area) for lo, la, (g, _) in zip(lon, lat, bld)], C.RAW / "ghsl_anbh_2018.tif")
    streets = {}
    for g, p in roads:
        if p.get("name"):
            streets.setdefault(p["name"], []).append(g)
    landmarks = LM.match(bld, frame, streets={k: shapely.union_all(v) for k, v in streets.items()})
    s2 = None
    s2_path = C.RAW / "sentinel2_tci.tif"
    if s2_path.exists():
        import rasterio
        with rasterio.open(s2_path) as src:
            rgb = S.grade_s2(np.moveaxis(src.read(), 0, -1))
            fp = [shapely.transform(g, lambda c: c + (frame.ox, frame.oy)) for g, _ in bld]
            rgb, _ = S.remove_roofs(rgb, src.transform, fp)
            s2 = (rgb, ~src.transform)
    # still water (ponds, reservoirs): flat surface at a level derived from the shoreline terrain; the OSM
    # outline is kept exactly. Flowing water (rivers/streams, elongated polygons) stays draped on the terrain.
    still = []
    t0 = terrains[0]
    for g, p in areas:
        if p["cover"] != "water" or g.area < 3000:
            continue
        c_, u_, v_, L_, W_ = B.obb(g)
        if L_ / max(W_, 1e-3) > 8:
            continue
        ring = np.asarray(shapely.segmentize(g.exterior, 10.0).coords)
        level = float(np.percentile(t0.z(ring[:, 0], ring[:, 1]), 20))
        still.append((g, level))
    water_parts = [g for g, p in areas if p["cover"] == "water"]
    water_parts += [g.buffer(WATERWAY_W.get(p["waterway"], 2.0) / 2, quad_segs=4) for g, p in waterways
                    if p.get("tunnel") not in ("yes", "culvert")]
    water = shapely.union_all(water_parts) if water_parts else None
    rail_polys = [g.buffer(2.5, cap_style="flat") for g, p in rails if p.get("railway") != "abandoned"]
    area_tree = shapely.STRtree([g for g, _ in areas])
    _CTX.update(cfg=cfg, frame=frame, terrains=terrains, bld=bld, roads=roads, areas=areas, water=water,
                rail_polys=rail_polys, trees=trees, quarantine=quarantine, overrides=ov, ov_report=ov_report,
                evidence=evidence, landmarks=landmarks, s2=s2, still_water=still,
                s2_sha=acquire.load_manifest().get("sentinel2_tci.tif", {}).get("sha256"),
                bld_tree=shapely.STRtree([g.representative_point() for g, _ in bld]),
                bld_geom_tree=shapely.STRtree([g for g, _ in bld]),
                road_tree=shapely.STRtree([g for g, _ in roads]), area_tree=area_tree,
                rail_tree=shapely.STRtree(rail_polys) if rail_polys else None,
                tree_tree=shapely.STRtree([g for g, _ in trees]) if trees else None,
                dem_sha=acquire.sha256(dem) if dem.exists() else "flat",
                ghsl_sha=acquire.load_manifest().get("ghsl_anbh_2018.tif", {}).get("sha256"))


def dem_path():
    """The DTM derived from the DSM (prepare_dtm) when present, else the raw DSM."""
    dtm = C.PROCESSED / "dtm.tif"
    return dtm if dtm.exists() else C.RAW / "dem_copernicus_glo30.tif"


def cover_at(pt):
    areas = _CTX["areas"]
    best = None
    for i in _CTX["area_tree"].query(pt, predicate="within"):
        g, p = areas[i]
        if best is None or g.area < best[0]:
            best = (g.area, p["cover"])
    return best[1] if best else None


def tile_inputs(ix, iy):
    size = _CTX["cfg"]["tile_size_m"]
    b = (ix * size, iy * size, (ix + 1) * size, (iy + 1) * size)
    tb = box(*b)
    bidx = [i for i in _CTX["bld_tree"].query(tb) if tb.contains(_CTX["bld"][i][0].representative_point())
            or (tb.touches(_CTX["bld"][i][0].representative_point()) and
                _CTX["bld"][i][0].representative_point().x < b[2] and _CTX["bld"][i][0].representative_point().y < b[3])]
    ridx = list(_CTX["road_tree"].query(tb.buffer(20)))
    aidx = list(_CTX["area_tree"].query(tb))
    return b, sorted(bidx), sorted(ridx), sorted(aidx)


def tile_hash(ix, iy, b, bidx, ridx, aidx) -> str:
    h = hashlib.sha256()
    cfg = _CTX["cfg"]
    h.update(json.dumps([code_version(), _CTX["dem_sha"], _CTX["ghsl_sha"], _CTX["s2_sha"], b, cfg["seed"], cfg["floor_height_m"],
                         cfg.get("decorative_trees", True)]).encode())
    for i in bidx:
        g, p = _CTX["bld"][i]
        h.update(shapely.to_wkb(g) + json.dumps(p, sort_keys=True).encode())
        h.update(json.dumps(_CTX["overrides"].get(p["source_id"]), sort_keys=True).encode())
        h.update(json.dumps([_CTX["evidence"][i], _CTX["landmarks"].get(p["source_id"])], sort_keys=True).encode())
    for i in ridx:
        h.update(shapely.to_wkb(_CTX["roads"][i][0]) + json.dumps(_CTX["roads"][i][1], sort_keys=True).encode())
    for i in aidx:
        h.update(shapely.to_wkb(_CTX["areas"][i][0]) + json.dumps(_CTX["areas"][i][1], sort_keys=True).encode())
    if _CTX["water"] is not None:
        h.update(shapely.to_wkb(shapely.clip_by_rect(_CTX["water"], *b)))
    return h.hexdigest()[:20]


def face_up(tris):
    """Make triangles counter-clockwise seen from above (front faces, like the terrain grid). Vertical ones are
    left as they are. Delaunay output has no guaranteed winding: flat ponds came out clockwise and were culled."""
    u, v = tris[:, 1, :2] - tris[:, 0, :2], tris[:, 2, :2] - tris[:, 0, :2]
    cw = u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0] < -1e-9
    tris = tris.copy()
    tris[cw] = tris[cw][:, ::-1]
    return tris


def add_water(soup, water, ter, b, off, tb):
    """Ponds flat at their level, flowing water draped; per-vertex colour shallow -> deep."""
    still = [(shapely.clip_by_rect(g, *b), lvl) for g, lvl in _CTX["still_water"] if g.intersects(tb)]
    still = [(g, l) for g, l in still if not g.is_empty]
    flowing = water.difference(shapely.union_all([g for g, _ in still])) if still else water
    flowing = shapely.union_all([q for q in shapely.get_parts(shapely.make_valid(flowing))
                                 if q.geom_type == "Polygon" and q.area > 0.5]) if not flowing.is_empty else flowing
    shore = _CTX["water"].boundary
    # colour: flat ponds have vertices only on the shore (no interior points), so a distance fade would make
    # them all "shallow" olive -> one fixed mostly-deep tone; streams fade over 2 m and are never fully shallow,
    # otherwise a 10 m wide stream stays olive and vanishes into the grass
    parts, pond = [], []
    for g, lvl in still:
        t2 = B.triangulate(g)
        if len(t2):
            flat = t2.reshape(-1, 2)
            parts.append(face_up(np.concatenate([flat, np.full((len(flat), 1), lvl + 0.05)], 1).reshape(-1, 3, 3)))
            pond.append(np.ones(len(flat), bool))
    if not flowing.is_empty:
        parts.append(S.drape(flowing, ter, b, off * 0.5))
        pond.append(np.zeros(len(parts[-1]) * 3, bool))
    if not parts:
        return
    tris = np.concatenate(parts)
    v = tris.reshape(-1, 3)
    d = shapely.distance(shapely.points(v[:, 0], v[:, 1]), shore)
    k = np.where(np.concatenate(pond), 0.8, np.clip(0.55 + d / 2.0, 0, 1))[:, None]
    col = (WATER_SHALLOW * (1 - k) + WATER_DEEP * k).reshape(-1, 3, 3)
    soup.add(tris, col)


def cut_ponds(tris, ponds, ter, b):
    """LOD0: cut the terrain exactly along each pond outline (the OSM outline is kept as is) and add a low bank
    wall from the original ground down to the water. Without it, grid triangles that straddle the shoreline rise
    above the flat water inside the outline and show as saw teeth at close range."""
    u, v = tris[:, 1, :2] - tris[:, 0, :2], tris[:, 2, :2] - tris[:, 0, :2]
    flat = np.abs(u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]) > 1e-9  # skip vertical skirts
    keep, add = np.ones(len(tris), bool), []
    for g, lvl in ponds:
        tp = shapely.polygons(tris[:, :, :2])
        hit = flat & shapely.intersects(tp, g) & keep
        for k in np.flatnonzero(hit):
            keep[k] = False
            rest = B.triangulate(tp[k].difference(g))
            if len(rest):
                add.append(np.concatenate([rest, ter.z(rest[..., 0], rest[..., 1])[..., None]], -1))
        edge = shapely.segmentize(shapely.clip_by_rect(g.boundary, *b), ter.step / 2)
        for line in shapely.get_parts(edge):
            q = np.asarray(line.coords)
            if len(q) < 2:
                continue
            top = ter.z(q[:, 0], q[:, 1])
            if top.max() <= lvl + 0.05:
                continue
            t0, t1 = np.c_[q, top], np.c_[q, np.full(len(q), lvl - 0.1)]
            w = np.concatenate([np.stack([t0[:-1], t1[:-1], t1[1:]], 1), np.stack([t0[:-1], t1[1:], t0[1:]], 1)])
            add += [w, w[:, ::-1]]  # both faces: the bank is seen from across the pond
    return np.concatenate([tris[keep]] + [face_up(a) for a in add])


def door_edge(g, attrs, i):
    """Street-facing exterior edge for a decorative door: the edge (>= 2.5 m) whose midpoint is closest to
    the nearest road within 30 m. Position along the edge seeded per building. None if no road nearby."""
    if attrs["facade"] in ("none", "industrial") or attrs["kind"] in ("outbuilding", "industrial"):
        return None
    c = g.centroid
    j = _CTX["road_tree"].nearest(c)
    road = _CTX["roads"][j][0]
    if road.distance(g) > 30:
        return None
    ring = np.asarray(shapely.orient_polygons(g, exterior_cw=False).exterior.coords)
    best, bd = None, 1e9
    for a, b in zip(ring[:-1], ring[1:]):
        if np.hypot(*(b - a)) < 2.5:
            continue
        d = road.distance(shapely.Point((a + b) / 2))
        if d < bd:
            best, bd = (tuple(a), tuple(b)), d
    return (*best, 0.3 + 0.4 * attrs["rand2"]) if best else None


def confidence(props, ov) -> str:
    if ov and ov.get("verified"):
        return "verified"
    return "source" if props["source"] == "osm" else "inferred"


def build_tile(ix, iy, force=False) -> dict:
    t0 = time.time()
    cfg, frame = _CTX["cfg"], _CTX["frame"]
    tid = tile_id(ix, iy)
    b, bidx, ridx, aidx = tile_inputs(ix, iy)
    digest = tile_hash(ix, iy, b, bidx, ridx, aidx)
    meta_path = OUT / f"{tid}.json"
    if not force and meta_path.exists():
        old = json.loads(meta_path.read_text())
        if old.get("hash") == digest and all((OUT / f["u"]).exists() for f in old["files"].values()):
            return {**old["index"], "cached": True}

    tb = box(*b)
    floor_h = cfg["floor_height_m"]
    # ---- buildings: attributes once, geometry per LOD ----
    blds, metas = [], []
    for i in bidx:
        g, p = _CTX["bld"][i]
        ov = _CTX["overrides"].get(p["source_id"])
        if ov and ov.get("suppress"):
            continue
        c, u, v, L, W = B.obb(g)
        obb_area = 4 * L * W
        ev = _CTX["evidence"][i]
        lm = _CTX["landmarks"].get(p["source_id"])
        attrs = B.resolve(p, g.area, L / max(W, 1e-3), g.area / max(obb_area, 1e-9),
                          cover_at(g.representative_point()), cfg, ov, ev=ev, landmark=lm)
        parts = B.plan(shapely.orient_polygons(g, exterior_cw=False), attrs)
        attrs["door"] = door_edge(g, attrs, i)
        ridge = max(pt.eave + pt.rh for pt in parts[0])
        blds.append((g, p, attrs, ov, parts))
        rp = g.representative_point()
        lon, lat = frame.to_geo(rp.x, rp.y)
        tags = p.get("tags", {})
        metas.append({k: v for k, v in {
            "id": p["id"], "sid": p["source_id"], "src": p["source"], "conf": confidence(p, ov),
            "kind": attrs["kind"], "kindM": attrs["kind_method"], "type": tags.get("building"),
            "h": round(ridge, 2), "eave": attrs["eave_height"], "hM": attrs["height_method"], "hC": attrs["height_conf"],
            "lv": attrs["levels"], "roof": attrs["roof"], "roofM": attrs["roof_method"], "roofMat": attrs["roof_mat"],
            "parts": len(parts[0]) if len(parts[0]) > 1 else None, "facade": attrs["facade"],
            "inb": bool(p.get("in_boundary")), "poi": p.get("poi"),
            # GHSL cell value shown as context for every building (not only where it set the height)
            "ghsl": {k: ev[k] for k in ("anbh", "share") if ev.get(k) is not None} or None,
            "landmark": {k: lm[k] for k in ("name", "wikidata", "evidence", "photos", "license", "match_distance_m", "match", "address", "heritage") if lm.get(k) not in (None, "", [])} if lm else None,
            "name": tags.get("name"), "addr": " ".join(x for x in (tags.get("addr:street"), tags.get("addr:housenumber")) if x) or None,
            "ll": [round(float(lon), 6), round(float(lat), 6)], "area": p["area_m2"], "ts": p.get("source_timestamp"),
            "flags": p.get("flags"), "note": ov.get("note") if ov else None,
            "model": ov.get("model") if ov else None,
            "ml_conf": p.get("ml_confidence") if p["source"] == "ms_ml" and (p.get("ml_confidence") or -1) >= 0 else None,
        }.items() if v is not None}
        )

    # ---- surfaces in this tile ----
    roads_local = [_CTX["roads"][i] for i in ridx]
    road_polys, bridges = S.road_polygons(roads_local)
    road_polys = {k: shapely.clip_by_rect(v, *b) for k, v in road_polys.items()}
    paved_areas = [shapely.clip_by_rect(_CTX["areas"][i][0], *b) for i in aidx if _CTX["areas"][i][1]["cover"] == "paved"]
    if paved_areas:
        road_polys["paved"] = shapely.union_all([road_polys.get("paved", shapely.Polygon()), *paved_areas])
    areas_local = [(shapely.clip_by_rect(_CTX["areas"][i][0], *b), _CTX["areas"][i][1]["cover"]) for i in aidx]
    areas_local = [(g, c) for g, c in areas_local if not g.is_empty]
    water = shapely.clip_by_rect(_CTX["water"], *b) if _CTX["water"] is not None else None
    if water is not None and water.is_empty:
        water = None
    rails = [shapely.clip_by_rect(_CTX["rail_polys"][i], *b) for i in _CTX["rail_tree"].query(tb)] if _CTX["rail_tree"] else []
    rails = [r for r in rails if not r.is_empty]
    # buildings are observed, road widths are inferred: never pave through a footprint (QA: 59 overlaps)
    near_idx = list(_CTX["bld_geom_tree"].query(tb.buffer(5)))
    near_fp = shapely.union_all([_CTX["bld"][j][0] for j in near_idx]) if near_idx else None
    if near_fp is not None:
        road_polys = {k: v.difference(near_fp) for k, v in road_polys.items()}
    for k in list(road_polys):  # keep water visible where a road crosses on a bridge (handled separately)
        if road_polys[k].is_empty:
            del road_polys[k]
    # gravel shoulders along paved roads (inferred, 1 m): never under buildings, water or other surfaces
    if "paved" in road_polys:
        taken = shapely.union_all(list(road_polys.values()) + ([near_fp] if near_fp is not None else []) +
                                  ([water] if water is not None else []))
        sh = shapely.clip_by_rect(road_polys["paved"].buffer(1.0, quad_segs=2).difference(taken), *b)
        if not sh.is_empty:
            road_polys["shoulder"] = sh
    # decorative yard layer: street-side fences of houses (separate mesh, switchable, never presented as data)
    roads_union = shapely.union_all([v for k, v in road_polys.items()]) if road_polys else shapely.Polygon()
    vehicle = [g for g, p in roads_local if p.get("highway") not in S.FOOT]
    house_polys = [(g, p["id"]) for g, p, a, _, _ in blds if a["kind"] == "house"]
    fences = S.yard_fences(house_polys, vehicle, roads_union, near_fp.buffer(0.6) if near_fp is not None else shapely.Polygon(),
                           f"{cfg['seed']}") if cfg.get("decorative_trees", True) else []

    # ---- trees (LOD0 only in the viewer; stored in tile meta) ----
    # trees must avoid *all* buildings touching the tile, including those owned by a neighbouring tile
    blocked = shapely.union_all([_CTX["bld"][j][0].buffer(1.5) for j in near_idx] + list(road_polys.values()) + rails +
                                ([water] if water is not None else []) + [p for p, *_ in bridges]) if (near_idx or road_polys or rails or water) else None
    mapped = [(g.x, g.y) for g, _ in (_CTX["trees"][i] for i in _CTX["tree_tree"].query(tb))] if _CTX["tree_tree"] else []
    mapped = [(x, y) for x, y in mapped if b[0] <= x < b[2] and b[1] <= y < b[3]]
    houses = [((g.centroid.x, g.centroid.y), math.sqrt(g.area / math.pi)) for g, p, a, _, _ in blds if a["kind"] == "house"]
    trees = S.place_trees(b, areas_local, blocked, houses, mapped, f"{cfg['seed']}{tid}", cfg.get("decorative_trees", True))
    t0z = _CTX["terrains"][0]
    tz = t0z.z(np.array([t[0] for t in trees]), np.array([t[1] for t in trees])) if trees else []
    tree_arr = [[round(t[0], 2), round(t[1], 2), round(float(z), 2), round(t[2], 1), round(t[3], 1), t[4], t[5]]
                for t, z in zip(trees, tz)]

    issues = []
    for g, p in _CTX["quarantine"]:
        pt = g.representative_point()
        if tb.contains(pt):
            issues.append({"sid": p["source_id"], "reason": p["reason"], "x": round(pt.x, 2), "y": round(pt.y, 2),
                           "z": round(float(t0z.z(pt.x, pt.y)), 2), "name": p.get("tags", {}).get("name")})

    bidx_set = set(bidx)
    files, zr, sizes = {}, None, {}
    for lod, spec in LODS.items():
        ter = _CTX["terrains"][lod]
        soups = {k: Soup() for k in MATERIALS}
        clamp = [(g_, lvl - 0.6) for g_, lvl in _CTX["still_water"] if g_.intersects(tb)]
        tris, zr_l = ter.grid_mesh(*b, clamp=clamp)
        if lod == 0 and clamp:
            tris = cut_ponds(tris, [(g_, lvl) for g_, lvl in _CTX["still_water"] if g_.intersects(tb)], ter, b)
        zr = zr_l if lod == 0 else zr
        uv = np.stack([(tris[..., 0] - b[0]) / (b[2] - b[0]), (b[3] - tris[..., 1]) / (b[3] - b[1])], -1)
        soups["terrain"].add(tris, COLORS["terrain"], uv)
        for fi, (g, p, attrs, ov, parts) in enumerate(blds):
            m = B.building_mesh(g, attrs, ter.z, floor_h, lod, parts=None if lod == 2 else parts)
            if m is None:
                continue
            soups["walls"].add(m["walls"][0], attrs["wall_color"], m["walls"][1], fid=fi, style=B.FACADE[attrs["facade"]])
            soups["roofs"].add(m["roof"][0], attrs["roof_color"], m["roof"][1], fid=fi, style=B.ROOF_MAT[attrs["roof_mat"]])
            if len(m["extra"]):
                soups["details"].add(m["extra"], m["extra_col"], fid=fi)
        if lod < 2:
            off = OFFSET[lod]
            for k, mat in (("paved", "road_paved"), ("unpaved", "road_unpaved"), ("foot", "road_foot"), ("shoulder", "road_shoulder")):
                if k in road_polys and not (k == "shoulder" and lod > 0):  # shoulders: mesh close up, texture beyond
                    drape = S.drape_thin if k == "shoulder" else S.drape
                    soups[mat].add(drape(road_polys[k], ter, b, off + (0.02 if k == "paved" else 0)), COLORS[mat])
            if water is not None:
                add_water(soups["water"], water, ter, b, off, tb)
            if lod == 0:
                for line, col in fences:
                    soups["decor_fence"].add(S.fence_tris(line, ter), LIN(col))
            for r in rails:
                soups["rail"].add(S.drape(r, ter, b, off), COLORS["rail"])
            for poly, line, p in bridges:
                soups["bridge"].add(S.bridge_tris(poly, line, ter, b), COLORS["bridge"])
        base = S.sample_base(_CTX["s2"], b, spec["tex"], (frame.ox, frame.oy)) if _CTX["s2"] is not None else None
        near = [g for g, *_ in blds] + [_CTX["bld"][j][0] for j in _CTX["bld_tree"].query(tb.buffer(5)) if j not in bidx_set]
        tex = S.ground_texture(b, spec["tex"], areas_local, water, road_polys, rails, spec["q"], f"{tid}{lod}", base,
                               footprints=near if lod < 2 else None)
        prims = [{"name": k, "soup": s, "material": {**MATERIALS[k], "color": [1, 1, 1]},
                  "uv": k in ("terrain", "walls", "roofs"), "fid": k in ("walls", "roofs", "details"),
                  "style": k in ("walls", "roofs")}
                 for k, s in soups.items() if len(s)]
        name = f"{tid}.lod{lod}.glb"
        sizes[lod] = write_glb(OUT / name, prims, images={"ground": tex},
                               extras={"tile": tid, "lod": lod, "bounds": b, "frame": frame.as_dict()})
        files[str(lod)] = {"u": name, "s": sizes[lod]}

    index_entry = {"id": tid, "ix": ix, "iy": iy, "b": list(b), "z": [round(zr[0], 2), round(zr[1], 2)],
                   "n": {"buildings": len(metas), "trees": len(tree_arr), "issues": len(issues)},
                   "files": files, "hash": digest, "secs": round(time.time() - t0, 2)}
    meta = {"id": tid, "hash": digest, "bounds": list(b), "buildings": metas, "trees": tree_arr,
            "issues": issues, "files": files, "index": index_entry}
    tmp = meta_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")))
    tmp.replace(meta_path)  # meta written last: a crash mid-tile leaves no "complete" marker
    return index_entry


def _worker_init(cfg):
    init_ctx(cfg)


def _worker(ix, iy, force):
    try:
        return build_tile(ix, iy, force)
    except Exception as e:
        return {"id": tile_id(ix, iy), "ix": ix, "iy": iy, "error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc()}


def tile_grid(cfg, frame):
    boundary = frame.geom_to_local(shape(json.loads((C.PROCESSED / "boundary.geojson").read_text())["features"][0]["geometry"]))
    aoi = boundary.buffer(cfg["context_buffer_m"])
    size = cfg["tile_size_m"]
    x0, y0, x1, y1 = aoi.bounds
    cells = []
    for ix in range(math.floor(x0 / size), math.ceil(x1 / size)):
        for iy in range(math.floor(y0 / size), math.ceil(y1 / size)):
            if aoi.intersects(box(ix * size, iy * size, (ix + 1) * size, (iy + 1) * size)):
                cells.append((ix, iy))
    return cells, boundary


def build(cfg, only=None, force=False, workers=4):
    OUT.mkdir(parents=True, exist_ok=True)
    size = cfg["tile_size_m"]
    for spec in LODS.values():
        if size % spec["step"]:
            raise ValueError(f"tile_size_m {size} must be a multiple of every LOD terrain step ({[s['step'] for s in LODS.values()]})")
    frame = Frame(cfg["crs_metric"], cfg["origin_utm"])
    cells, boundary = tile_grid(cfg, frame)
    dsm = C.RAW / "dem_copernicus_glo30.tif"
    if dsm.exists():
        objs = [shape(f["geometry"]) for f in json.loads((C.PROCESSED / "buildings.geojson").read_text())["features"]]
        objs += [shape(f["geometry"]) for f in json.loads((C.PROCESSED / "areas.geojson").read_text())["features"]
                 if f["properties"]["cover"] in ("forest", "park", "orchard", "cemetery", "scrub")]
        rep = prepare_dtm(dsm, C.PROCESSED / "dtm.tif", objs)
        (C.PROCESSED / "terrain_report.json").write_text(json.dumps(rep, indent=1))
        log.info("terrain: DSM->DTM in %d masked cells, mean lowering %.2f m, max %.1f m", rep["masked_cells"],
                 rep["mean_lowering_in_mask_m"], rep["max_lowering_m"])
    if only:
        want = set(only)
        cells = [c for c in cells if tile_id(*c) in want]
        if not cells:
            raise ValueError(f"none of {only} is a tile of this AOI")
    log.info("building %d tiles x %d LODs with %d workers", len(cells), len(LODS), workers)
    results, t0 = [], time.time()
    if workers <= 1:
        init_ctx(cfg)
        for k, (ix, iy) in enumerate(cells):
            results.append(_worker(ix, iy, force))
            log.info("[%d/%d] %s", k + 1, len(cells), results[-1]["id"])
    else:
        with ProcessPoolExecutor(max_workers=workers, initializer=_worker_init, initargs=(cfg,)) as ex:
            futs = [ex.submit(_worker, ix, iy, force) for ix, iy in cells]
            for k, f in enumerate(as_completed(futs)):
                r = f.result()
                results.append(r)
                state = "FAILED" if "error" in r else ("cached" if r.get("cached") else f"{r['secs']}s")
                log.info("[%d/%d] %s %s", k + 1, len(cells), r["id"], state)
    context = build_context(cfg, frame, tile_grid(cfg, frame)[0])
    write_index(cfg, frame, results, boundary, partial=bool(only), context=context)
    attribute_report()
    failed = [r for r in results if "error" in r]
    for r in failed:
        log.error("tile %s failed: %s\n%s", r["id"], r["error"], r["trace"])
    log.info("tiles: %d ok (%d cached), %d failed in %.1fs", len(results) - len(failed),
             sum(1 for r in results if r.get("cached")), len(failed), time.time() - t0)
    if failed:
        raise RuntimeError(f"{len(failed)} tile(s) failed: {[r['id'] for r in failed]} - rerun `build` to retry only those")


def write_index(cfg, frame, results, boundary, partial=False, context=None):
    path = OUT / "index.json"
    prev = {t["id"]: t for t in json.loads(path.read_text())["tiles"]} if (partial and path.exists()) else {}
    for r in results:
        if "error" not in r:
            prev[r["id"]] = {k: v for k, v in r.items() if k not in ("cached", "secs")}
    failed = [{"id": r["id"], "error": r["error"]} for r in results if "error" in r]
    tiles = sorted(prev.values(), key=lambda t: (t["iy"], t["ix"]))
    manifest = acquire.load_manifest()
    norm = json.loads((C.PROCESSED / "normalize_report.json").read_text())
    idx = {"version": 1, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "code_version": code_version(), "frame": frame.as_dict(), "tile_size": cfg["tile_size_m"],
           "lods": {str(k): {"terrain_step_m": v["step"], "texture_px": v["tex"]} for k, v in LODS.items()},
           "boundary_local": boundary_line(boundary, frame),
           "tiles": tiles, "failed": failed,
           "sources": {k: {kk: v.get(kk) for kk in ("source", "license", "source_timestamp", "retrieved_at", "url")}
                       for k, v in manifest.items() if k.endswith((".json", ".tif", ".gz")) and k != "ms_dataset_links.csv"},
           "counts": norm["counts"], "context": context}
    path.write_text(json.dumps(idx, ensure_ascii=False, separators=(",", ":")))
    write_search(frame)


def boundary_line(boundary, frame):
    """Town boundary densified to 10 m with LOD0 terrain heights, so the viewer can drape it."""
    ring = boundary.exterior if boundary.geom_type == "Polygon" else max(shapely.get_parts(boundary), key=lambda g: g.area).exterior
    ring = shapely.segmentize(ring, 10.0)
    xy = np.asarray(ring.coords)
    dem = dem_path()
    z = Terrain(dem, frame, step=LODS[0]["step"], verbose=False).z(xy[:, 0], xy[:, 1]) if dem.exists() else np.zeros(len(xy))
    return [[round(float(x), 1), round(float(y), 1), round(float(h), 1)] for (x, y), h in zip(xy, z)]


def build_context(cfg, frame, cells=()) -> dict | None:
    """One low-resolution terrain sheet over the whole Sentinel-2 window (AOI + ~1 km), textured with the
    satellite colour, 1.5 m below the tiles: fills the ragged tile edge. Not part of the reconstruction."""
    s2_path = C.RAW / "sentinel2_tci.tif"
    if not s2_path.exists():
        return None
    import io
    import rasterio
    from PIL import Image
    with rasterio.open(s2_path) as src:
        b = src.bounds
        rgb = S.grade_s2(np.moveaxis(src.read(), 0, -1)).astype(np.uint8)
    step = 120.0
    x0 = math.ceil((b.left - frame.ox) / step) * step
    y0 = math.ceil((b.bottom - frame.oy) / step) * step
    x1 = math.floor((b.right - frame.ox) / step) * step
    y1 = math.floor((b.top - frame.oy) / step) * step
    ter = Terrain(dem_path(), frame, step=step, verbose=False)
    tris, _ = ter.grid_mesh(x0, y0, x1, y1, skirt=0)
    size, inset = cfg["tile_size_m"], step * 1.1
    c = tris.mean(axis=1)
    # drop the sheet under the built tiles (it would only poke through); keep a margin at the outer edge
    built = shapely.union_all([box(ix * size, iy * size, (ix + 1) * size, (iy + 1) * size) for ix, iy in cells]).buffer(-inset)
    under = shapely.contains_xy(built, c[:, 0], c[:, 1]) if not built.is_empty else np.zeros(len(tris), bool)
    tris = tris[~under].copy()
    tris[..., 2] -= 2.5
    uv = np.stack([(tris[..., 0] + frame.ox - b.left) / (b.right - b.left), (b.top - (tris[..., 1] + frame.oy)) / (b.top - b.bottom)], -1)
    soup = Soup()
    soup.add(tris, COLORS["terrain"], uv)
    img = Image.fromarray(rgb)
    img = img.resize((min(2048, img.width * 2), min(2048, img.height * 2)), Image.BICUBIC)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80, optimize=True)
    size = write_glb(OUT / "context.glb", [{"name": "context_ground", "soup": soup,
                                              "material": {**MATERIALS["terrain"], "color": [1, 1, 1]}, "uv": True}],
                     images={"ground": buf.getvalue()}, extras={"note": "context only (Sentinel-2 colour on DTM), not reconstructed"})
    return {"u": "context.glb", "s": size, "b": [x0, y0, x1, y1]}


def attribute_report() -> dict:
    """Provenance counts over all buildings in the built tiles (whole AOI and inside the town boundary)."""
    from collections import Counter
    rep = {}
    for scope in ("aoi", "boundary"):
        c = {k: Counter() for k in ("height_method", "height_conf", "roof_method", "roof_shape", "roof_material",
                                     "facade", "geometry_source", "kind")}
        n = multipart = landmarks = 0
        for f in sorted(OUT.glob("t*.json")):
            for b in json.loads(f.read_text())["buildings"]:
                if scope == "boundary" and not b.get("inb"):
                    continue
                n += 1
                for key, field in (("height_method", "hM"), ("height_conf", "hC"), ("roof_method", "roofM"),
                                   ("roof_shape", "roof"), ("roof_material", "roofMat"), ("facade", "facade"),
                                   ("geometry_source", "src"), ("kind", "kind")):
                    c[key][b.get(field)] += 1
                multipart += bool(b.get("parts"))
                landmarks += bool(b.get("landmark"))
        rep[scope] = {"buildings": n, "multipart_roofs": multipart, "landmarks": landmarks,
                      **{k: dict(v.most_common()) for k, v in c.items()}}
    (C.PROCESSED / "building_attributes_report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    return rep


def write_search(frame):
    """Street / landmark / address search index for the viewer (local coordinates)."""
    streets = {}
    for g, p in _CTX.get("roads") or _load_layer("roads.geojson", frame):
        n = p.get("name")
        if not n:
            continue
        s = streets.setdefault(n, {"name": n, "name_en": p.get("name:en"), "kind": "street", "parts": []})
        s["parts"].append(g)
    out = []
    for s in streets.values():
        u = shapely.union_all(s.pop("parts"))
        c = u.interpolate(0.5, normalized=True) if u.geom_type == "LineString" else u.representative_point()
        parts = [[round(v, 1) for xy in g.simplify(1.5).coords for v in xy] for g in shapely.get_parts(shapely.line_merge(u) if u.geom_type == "MultiLineString" else u)]
        out.append({**{k: v for k, v in s.items() if v}, "x": round(c.x, 1), "y": round(c.y, 1),
                    "bb": [round(v, 1) for v in u.bounds], "parts": parts})
    for f in json.loads((C.PROCESSED / "pois.geojson").read_text())["features"]:
        g = frame.geom_to_local(shape(f["geometry"]))
        p = f["properties"]
        out.append({"name": p["name"], **({"name_en": p["name_en"]} if p.get("name_en") else {}),
                    "kind": p["kind"], "x": round(g.x, 1), "y": round(g.y, 1)})
    for g, p in (_CTX.get("bld") or _load_layer("buildings.geojson", frame)):
        t = p.get("tags", {})
        if t.get("addr:housenumber") or t.get("name"):
            c = g.representative_point()
            label = t.get("name") or " ".join(x for x in (t.get("addr:street"), t.get("addr:housenumber")) if x)
            out.append({"name": label, "kind": "building", "id": p["id"], "x": round(c.x, 1), "y": round(c.y, 1)})
    out.sort(key=lambda s: (s["kind"] != "street", s["name"]))
    (C.GENERATED / "search.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
