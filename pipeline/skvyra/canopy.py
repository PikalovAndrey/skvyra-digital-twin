"""Real tree candidates from the Meta / WRI High Resolution Canopy Height Map v2 (CC BY 4.0).

The CHM is a raster of *canopy height* (uint8 metres, ~1.19 m Web-Mercator pixels = ~0.77 m on the ground at
Skvyra), predicted by an ML model from Maxar imagery of a known date per area. It contains no tree objects, so
individual trees are *candidates*: local maxima of the smoothed canopy surface with a height-dependent search
window (merged crowns give one candidate per dominant top, not one per pixel), with the crown radius measured by
8 rays down the crown flanks. Everything here is deterministic (no randomness).

Known failure modes handled explicitly:
* model fill artefacts: large plateaus of one constant height (10 m at Skvyra) -> "unreliable" blocks, where the
  previous land-cover rules are kept as fallback;
* roofs and other non-vegetation: tops on buildings / roads / water / rail are rejected;
* change since the imagery date (2011-2015 at Skvyra): candidates on present-day buildings are rejected; every
  tree keeps its imagery date and a confidence;
* leaf-off imagery (April): lower confidence.
"""
import json
import math

import numpy as np

from . import config as C
from .log import log

SOURCE = "Meta/WRI Canopy Height Map v2 (CC BY 4.0)"
MIN_TREE_H = 3.0     # below: shrubs/hedges/noise, not modelled as trees
PX = 0.75            # local analysis grid (m), close to the native ground resolution
BLOCK = 32           # artefact test block (px of the local grid = 24 m)


def _maxf(a, r):
    """Square max filter of radius r (separable, edge-padded)."""
    if r <= 0:
        return a
    from numpy.lib.stride_tricks import sliding_window_view as sw
    p = np.pad(a, ((r, r), (0, 0)), mode="edge")
    a = sw(p, 2 * r + 1, axis=0).max(axis=-1)
    p = np.pad(a, ((0, 0), (r, r)), mode="edge")
    return sw(p, 2 * r + 1, axis=1).max(axis=-1)


def _minf(a, r):
    return -_maxf(-a, r)


def _blur(a, sigma=1.0):
    k = np.exp(-0.5 * (np.arange(-2, 3) / sigma) ** 2)
    k /= k.sum()
    p = np.pad(a, 2, mode="edge")
    p = sum(k[i] * p[i:i + p.shape[0] - 4, :] for i in range(5))
    return sum(k[i] * p[:, i:i + p.shape[1] - 4] for i in range(5))


def load_local(chm_path, frame, bounds, px=PX):
    """Reproject the CHM window onto a north-up local grid. -> (heights float32 [row 0 = north], x0, y1, px)."""
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject
    x0, y0, x1, y1 = bounds
    w, h = int(math.ceil((x1 - x0) / px)), int(math.ceil((y1 - y0) / px))
    dst = np.zeros((h, w), np.float32)
    with rasterio.open(chm_path) as src:
        reproject(rasterio.band(src, 1), dst, dst_transform=from_origin(x0 + frame.ox, y1 + frame.oy, px, px),
                  dst_crs=frame.crs, resampling=Resampling.bilinear, src_nodata=255, dst_nodata=0)
    # the artefact plateaus are exactly constant in the *source* values; detect them before resampling blurs them
    flat = np.zeros((h, w), np.float32)
    with rasterio.open(chm_path) as src:
        a = src.read(1).astype(np.int16)
        f = ((_maxf(a, 3) - _minf(a, 3)) == 0) & (a >= MIN_TREE_H)
        reproject(f.astype(np.float32), flat, src_transform=src.transform, src_crs=src.crs, src_nodata=-1, dst_nodata=-1,
                  dst_transform=from_origin(x0 + frame.ox, y1 + frame.oy, px, px), dst_crs=frame.crs,
                  resampling=Resampling.average)
    return dst, flat, x0, y1, px


def unreliable_blocks(flat, block=BLOCK):
    """Blocks where > 35 % of pixels sit on a constant-height plateau (7x7 source px) = model fill, plus one
    block of margin. Real canopy is never that flat over 5 m (checked on the OSM forests at Skvyra)."""
    h, w = flat.shape
    hb, wb = -(-h // block), -(-w // block)
    p = np.zeros((hb * block, wb * block), np.float32)
    p[:h, :w] = flat
    frac = p.reshape(hb, block, wb, block).mean(axis=(1, 3))
    bad = _maxf((frac > 0.35).astype(np.uint8), 1).astype(bool)
    return bad


def _block_mean(a, block):
    h, w = a.shape
    hb, wb = -(-h // block), -(-w // block)
    p = np.zeros((hb * block, wb * block), np.float32)
    p[:h, :w] = a
    return p.reshape(hb, block, wb, block).mean(axis=(1, 3))


def outdated_blocks(chm, wc_path, frame, x0, y1, px, block=BLOCK):
    """Blocks where ESA WorldCover 2021 (newer than the 2011/2015 CHM imagery) sees > 60 % tree cover but the CHM
    has < 5 % canopy >= 3 m: vegetation grew (or was missed) after the CHM imagery -> CHM not trusted there.
    (The reverse - CHM trees where WorldCover has none - is kept: WorldCover 10 m misses single garden trees.)"""
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject
    wc = np.zeros(chm.shape, np.uint8)
    with rasterio.open(wc_path) as src:
        reproject(rasterio.band(src, 1), wc, dst_transform=from_origin(x0 + frame.ox, y1 + frame.oy, px, px),
                  dst_crs=frame.crs, resampling=Resampling.nearest)
    o = (_block_mean((wc == 10).astype(np.float32), block) > 0.6) & (_block_mean((chm >= MIN_TREE_H).astype(np.float32), block) < 0.05)
    # only coherent patches (a grown park or plantation), not isolated 24 m blocks of 10 m-classification noise:
    # keep a block when >= 4 of its 8 neighbours agree, then grow the patch by one block
    p = np.pad(o, 1).astype(np.uint8)
    nb = sum(p[1 + dy:p.shape[0] - 1 + dy, 1 + dx:p.shape[1] - 1 + dx] for dy in (-1, 0, 1) for dx in (-1, 0, 1)) - o
    core = o & (nb >= 4)
    return _maxf(core.astype(np.uint8), 1).astype(bool) & (o | _maxf(o.astype(np.uint8), 1).astype(bool))


def detect(chm, x0, y1, px, blocked_mask=None, bad_blocks=None, block=BLOCK):
    """Tree-top candidates. -> array (n, 4): x, y (local m), height (m), crown radius (m)."""
    s = _blur(chm, 1.2)
    # height-dependent local-maximum window: small trees ~1.5 m, tall ~3.75 m (a tree crown is wider than tall/4)
    tops = np.zeros(chm.shape, bool)
    for r, lo, hi in ((2, MIN_TREE_H, 6), (3, 6, 12), (4, 12, 18), (5, 18, 99)):
        m = (s >= lo) & (s < hi)
        tops |= m & (s >= _maxf(s, r) - 1e-4)
    if blocked_mask is not None:
        tops &= ~blocked_mask
    if bad_blocks is not None:
        rr, cc = np.nonzero(tops)
        keep = ~bad_blocks[rr // block, cc // block]
        tops[:] = False
        tops[rr[keep], cc[keep]] = True
    rr, cc = np.nonzero(tops)
    # plateaus inside one crown give several equal maxima: keep one per 3x3 px cluster (first in raster order)
    order = np.lexsort((cc, rr))
    rr, cc = rr[order], cc[order]
    seen, keep = set(), []
    for i, (r_, c_) in enumerate(zip(rr, cc)):
        k = (r_ // 3, c_ // 3)
        if k in seen or (r_ // 3, c_ // 3 - 1) in seen or (r_ // 3 - 1, c_ // 3) in seen:
            continue
        seen.add(k)
        keep.append(i)
    rr, cc = rr[keep], cc[keep]
    ht = s[rr, cc]  # smoothed: drives the crown-radius rays
    top_h = _maxf(chm, 1)[rr, cc]  # reported height: unsmoothed peak (smoothing shaves narrow crowns by 1-2 m)
    # crown radius: 8 rays down the flanks, stopping at half the top height, at 2 m, or where the surface climbs
    # again (a neighbouring crown)
    H, W = s.shape
    dirs = [(0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1)]
    lens = np.zeros((len(rr), len(dirs)), np.float32)
    for j, (dr, dc) in enumerate(dirs):
        alive = np.ones(len(rr), bool)
        prev = ht.copy()
        step = math.hypot(dr, dc) * px
        for k in range(1, 12):
            r2 = np.clip(rr + dr * k, 0, H - 1)
            c2 = np.clip(cc + dc * k, 0, W - 1)
            v = s[r2, c2]
            alive &= (v >= np.maximum(2.0, 0.5 * ht)) & (v <= prev + 0.3)
            lens[alive, j] += step
            prev = v
    # allometric cap: broadleaf crowns in open/garden growth rarely exceed ~0.35 h + 1 m in radius
    rad = np.clip(np.minimum(lens.mean(axis=1) + px / 2, 0.35 * ht + 1.0), 1.0, 7.5)
    x = x0 + (cc + 0.5) * px
    y = y1 - (rr + 0.5) * px
    return nms(np.column_stack([x, y, top_h, rad]).astype(np.float64))


def nms(c):
    """Tallest first; drop a candidate whose top lies inside 0.7 x the crown radius of an accepted taller tree
    (one dominant top per merged crown; flat low canopy no longer yields a tree every few pixels)."""
    order = np.argsort(-c[:, 2], kind="stable")
    cell = 6.0
    grid, keep = {}, []
    for i in order:
        x, y, h, r = c[i]
        gx, gy = int(math.floor(x / cell)), int(math.floor(y / cell))
        ok = True
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((gx + dx, gy + dy), ()):
                    if math.hypot(c[j, 0] - x, c[j, 1] - y) < 0.7 * max(c[j, 3], r):
                        ok = False
                        break
                if not ok:
                    break
            if not ok:
                break
        if ok:
            grid.setdefault((gx, gy), []).append(i)
            keep.append(i)
    return c[np.sort(keep)]


def imagery_dates(dates_geojson, frame):
    """[(local polygon, 'YYYY-MM-DD')] for the CHM source imagery."""
    from shapely.geometry import shape
    fc = json.loads(dates_geojson.read_text())
    return [(frame.geom_to_local(shape(f["geometry"])), f["properties"]["acq_date"]) for f in fc["features"]]


CONF_CODE = {"low": 1, "medium": 2}


def kind_for(x, y, h, cover, houses):
    """Crown *shape* for a CHM tree. The CHM says nothing about species: this is appearance only (inferred),
    deterministic per position. 0 deciduous, 1 conifer, 2 small fruit/garden tree."""
    u = (int(x * 10) * 73856093 ^ int(y * 10) * 19349663) % 1000 / 1000
    if cover == "forest":
        return 1 if u < 0.3 else 0
    if cover == "orchard" and h <= 8:
        return 2
    if h <= 7 and any(math.hypot(x - c[0], y - c[1]) < r + 20 for c, r in houses):
        return 2 if u < 0.6 else 0
    return 0


def confidence(h, date):
    """'medium' for leaf-on imagery and trees >= 5 m, otherwise 'low'. Never 'high': model heights, old imagery."""
    leaf_off = date is None or date[5:7] in ("11", "12", "01", "02", "03", "04")
    return "low" if leaf_off or h < 5 else "medium"


def build(cfg, frame, bounds, blocked, osm_trees, out_trees, out_mask, wooded=()):
    """Run detection over the AOI and write processed GeoJSON (local coordinates converted to WGS84):
    out_trees: points with h, r, conf, date, source; out_mask: area where the CHM is trusted (outside it the
    previous land-cover rules stay as fallback). wooded: OSM orchard/forest polygons (local) - where the CHM sees
    < 10 % canopy >= 3 m inside one, the human mapping is the newer evidence (planted after the 2011/2015 imagery,
    or trellis rows below what the CHM resolves) and the polygon is left to the land-cover rule."""
    import rasterio.features
    import shapely
    from rasterio.transform import from_origin
    from shapely.geometry import mapping
    chm_path, dates_path = C.RAW / "chm_meta_v2.tif", C.RAW / "chm_meta_v2_dates.geojson"
    chm, flat, x0, y1, px = load_local(chm_path, frame, bounds)
    fill = unreliable_blocks(flat)
    wc_path = C.RAW / "worldcover_2021.tif"
    old = outdated_blocks(chm, wc_path, frame, x0, y1, px) if wc_path.exists() else np.zeros_like(fill)
    bad = fill | old
    H, W = chm.shape
    tr = from_origin(x0, y1, px, px)
    newer = []
    for g in wooded:
        m = rasterio.features.rasterize([(g, 1)], out_shape=(H, W), transform=tr, dtype=np.uint8).astype(bool)
        if m.sum() > 400 and (chm[m] >= MIN_TREE_H).mean() < 0.10:
            newer.append(g)
    bmask = rasterio.features.rasterize([(g, 1) for g in shapely.get_parts(blocked)], out_shape=(H, W), transform=tr,
                                        dtype=np.uint8).astype(bool) if blocked is not None and not blocked.is_empty else None
    cand = detect(chm, x0, y1, px, bmask, bad)
    dates = imagery_dates(dates_path, frame)
    tree = shapely.STRtree([g for g, _ in dates])
    pts = shapely.points(cand[:, 0], cand[:, 1])
    date_of = np.full(len(cand), None, object)
    pi, gi = tree.query(pts, predicate="within")
    date_of[pi] = [dates[g][1] for g in gi]
    # one tree per mapped OSM tree: OSM position wins, CHM gives the height
    osm_xy = np.array([(g.x, g.y) for g in osm_trees]) if osm_trees else np.zeros((0, 2))
    drop = np.zeros(len(cand), bool)
    osm_h = []
    for ox, oy in osm_xy:
        d = np.hypot(cand[:, 0] - ox, cand[:, 1] - oy)
        j = int(np.argmin(d)) if len(d) else -1
        if j >= 0 and d[j] < 4.0:
            drop[j] = True
            osm_h.append((float(cand[j, 2]), float(cand[j, 3]), date_of[j]))
        else:
            osm_h.append(None)
    feats = []
    for (x, y, h, r), dt, dr in zip(cand, date_of, drop):
        if dr:
            continue
        lon, lat = frame.to_geo(x, y)
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(float(lon), 7), round(float(lat), 7)]},
                      "properties": {"h": round(float(h), 1), "r": round(float(r), 1), "date": dt, "conf": confidence(h, dt),
                                     "source": "chm_v2"}})
    out_trees.write_text(json.dumps({"type": "FeatureCollection", "source": SOURCE, "osm_heights": osm_h,
                                     "features": feats}))
    # unreliable blocks -> polygons (local -> geo)
    polys = []
    for r_, c_ in zip(*np.nonzero(bad)):
        polys.append(shapely.box(x0 + c_ * BLOCK * px, y1 - (r_ + 1) * BLOCK * px, x0 + (c_ + 1) * BLOCK * px, y1 - r_ * BLOCK * px))
    u = shapely.union_all(polys) if polys else shapely.Polygon()
    # trusted = the analysed window minus the model-fill blocks, shrunk by 2 m so no edge tree is half-trusted
    trusted = shapely.box(x0, y1 - H * px, x0 + W * px, y1).difference(u)
    if newer:
        trusted = trusted.difference(shapely.union_all(newer))
    trusted = trusted.buffer(-2.0)
    out_mask.write_text(json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature",
        "properties": {"meaning": "CHM v2 trusted area; outside: no coverage, model fill (constant-height plateaus) or outdated (WorldCover 2021 tree cover where the CHM has none)"},
        "geometry": mapping(frame.geom_to_geo(trusted))}]}))
    blk_km2 = (BLOCK * px) ** 2 / 1e6
    log.info("canopy: %d tree candidates (%d merged with OSM trees); untrusted: model fill %.2f km2, outdated vs "
             "WorldCover 2021 %.2f km2, %d OSM orchards/forests without CHM canopy (newer mapping) %.2f km2", len(feats),
             int(drop.sum()), fill.sum() * blk_km2, (old & ~fill).sum() * blk_km2, len(newer), sum(g.area for g in newer) / 1e6)
    return feats, u


def run(cfg):
    """normalize-stage step: processed/canopy_trees.geojson + processed/canopy_coverage.geojson."""
    import shapely
    from shapely.geometry import shape
    from . import surfaces as S
    from .geo import Frame
    if not (C.RAW / "chm_meta_v2.tif").exists():
        log.warning("canopy: CHM not acquired, trees stay rule-based")
        return
    frame = Frame(cfg["crs_metric"], cfg["origin_utm"])
    load = lambda n: [(frame.geom_to_local(shape(f["geometry"])), f["properties"])
                      for f in json.loads((C.PROCESSED / n).read_text())["features"] if f.get("geometry")]
    bld, areas, roads = load("buildings.geojson"), load("areas.geojson"), load("roads.geojson")
    # tops are rejected on present-day buildings (also catches change since the imagery date), open water and
    # the carriageway core (inferred width minus 1 m: street trees on verges stay)
    blocked = shapely.union_all([g.buffer(0.5) for g, _ in bld] + [g for g, p in areas if p["cover"] == "water"] +
                                [g.buffer(max(0.5, S.road_width(p)[0] / 2 - 1.0)) for g, p in roads if p.get("highway") not in S.FOOT])
    boundary = frame.geom_to_local(shape(json.loads((C.PROCESSED / "boundary.geojson").read_text())["features"][0]["geometry"]))
    build(cfg, frame, boundary.buffer(cfg["context_buffer_m"]).bounds, blocked, [g for g, _ in load("trees.geojson")],
          C.PROCESSED / "canopy_trees.geojson", C.PROCESSED / "canopy_coverage.geojson",
          wooded=[g for g, p in areas if p["cover"] in ("orchard", "forest")])
