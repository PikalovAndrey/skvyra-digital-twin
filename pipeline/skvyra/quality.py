"""Data-quality layer: residential streets where building outlines are probably incomplete.

This never creates buildings. It samples residential streets every 20 m inside the town boundary and asks two
questions: is any building outline (OSM or Microsoft ML) within 60 m, and if not, does ESA WorldCover 2021 (10 m land
cover - far too coarse to prove a single house) see built-up land nearby? The answer is a *suspicion* with reasons
and a confidence, shown as coloured lines along the street in the viewer (switchable, never drawn as buildings).

Classes for points without any outline within 60 m:
  suspect_gap   built-up >= 20 % within 40 m  -> outlines probably missing (confidence medium >= 40 %, else low)
  insufficient  5-20 % built-up                -> not enough information
  no_signs      < 5 % built-up                 -> no sign of buildings in 10 m land cover (not proof of absence)
Points with an outline within 60 m are 'covered'.
"""
import json

import numpy as np
import shapely
from shapely.geometry import shape

from . import config as C
from .log import log

STEP, NEAR, RADIUS = 20.0, 60.0, 40.0
RESIDENTIAL = {"residential", "living_street", "unclassified", "tertiary"}


def classify(built, near_building, canopy):
    """-> (class, confidence, reasons) for one street sample point."""
    if near_building:
        return "covered", None, []
    reasons = ["no building outline (OSM, Microsoft ML) within 60 m"]
    if built >= 0.2:
        reasons.append(f"ESA WorldCover 2021 marks {built:.0%} of the 40 m surroundings as built-up (10 m cells)")
        if canopy >= 0.2:
            reasons.append(f"tree canopy over {canopy:.0%} of the surroundings - ML footprint detection often misses houses under trees")
        return "suspect_gap", "medium" if built >= 0.4 else "low", reasons
    if built >= 0.05:
        reasons.append(f"only {built:.0%} built-up in 10 m land cover - not enough to tell")
        return "insufficient", "low", reasons
    reasons.append("no built-up land in 10 m land cover (open land likely, but a single house can be missed)")
    return "no_signs", "low", reasons


def _frac(arr, x0, y1, px, x, y, rad, test):
    c0, c1 = int((x - rad - x0) / px), int((x + rad - x0) / px) + 1
    r0, r1 = int((y1 - (y + rad)) / px), int((y1 - (y - rad)) / px) + 1
    a = arr[max(r0, 0):max(r1, 0), max(c0, 0):max(c1, 0)]
    return float(test(a).mean()) if a.size else 0.0


def build(cfg, frame, terrain_z):
    """Write assets/generated/quality_gaps.json (local coordinates, z on the terrain) and return the stats."""
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject
    load = lambda n: [(frame.geom_to_local(shape(f["geometry"])), f["properties"])
                      for f in json.loads((C.PROCESSED / n).read_text())["features"] if f.get("geometry")]
    boundary = frame.geom_to_local(shape(json.loads((C.PROCESSED / "boundary.geojson").read_text())["features"][0]["geometry"]))
    blds = [g for g, _ in load("buildings.geojson")]
    tree = shapely.STRtree(blds)
    x0, y0, x1, y1 = boundary.buffer(100).bounds
    px = 5.0
    W, H = int((x1 - x0) / px) + 1, int((y1 - y0) / px) + 1
    wc = np.zeros((H, W), np.uint8)
    wc_path = C.RAW / "worldcover_2021.tif"
    if wc_path.exists():
        with rasterio.open(wc_path) as src:
            reproject(rasterio.band(src, 1), wc, dst_transform=from_origin(x0 + frame.ox, y1 + frame.oy, px, px),
                      dst_crs=frame.crs, resampling=Resampling.nearest)
    chm = np.zeros((H, W), np.float32)
    if (C.RAW / "chm_meta_v2.tif").exists():
        with rasterio.open(C.RAW / "chm_meta_v2.tif") as src:
            reproject(rasterio.band(src, 1), chm, dst_transform=from_origin(x0 + frame.ox, y1 + frame.oy, px, px),
                      dst_crs=frame.crs, resampling=Resampling.average, src_nodata=255)
    runs, totals = [], {"covered": 0.0, "suspect_gap": 0.0, "insufficient": 0.0, "no_signs": 0.0}
    for g, p in load("roads.geojson"):
        if p.get("highway") not in RESIDENTIAL:
            continue
        for part in shapely.get_parts(g.intersection(boundary)):
            if part.geom_type != "LineString" or part.length < STEP / 2:
                continue
            n = max(1, int(round(part.length / STEP)))
            seg = part.length / n
            cur = None
            for k in range(n):
                pt = part.interpolate((k + 0.5) * seg)
                near = tree.query_nearest(pt, max_distance=NEAR)
                cls, conf, reasons = classify(_frac(wc, x0, y1, px, pt.x, pt.y, RADIUS, lambda a: a == 50), len(near) > 0,
                                              _frac(chm, x0, y1, px, pt.x, pt.y, RADIUS, lambda a: a >= 3))
                totals[cls] += seg
                if cls == "covered":
                    cur = None
                    continue
                a_, b_ = part.interpolate(k * seg), part.interpolate((k + 1) * seg)
                if cur and cur["cls"] == cls and cur["conf"] == conf:
                    cur["pts"].append((b_.x, b_.y))
                    cur["len"] += seg
                else:
                    cur = {"cls": cls, "conf": conf, "reasons": reasons, "pts": [(a_.x, a_.y), (b_.x, b_.y)], "len": seg,
                           "street": p.get("name")}
                    runs.append(cur)
    for r in runs:
        xy = np.array(r["pts"])
        z = terrain_z(xy[:, 0], xy[:, 1])
        r["pts"] = [[round(float(x), 1), round(float(y), 1), round(float(zz) + 1.5, 2)] for (x, y), zz in zip(xy, z)]
        r["len"] = round(r["len"], 1)
    total = sum(totals.values())
    stats = {"residential_street_km": round(total / 1000, 2),
             **{f"{k}_km": round(v / 1000, 2) for k, v in totals.items()},
             **{f"{k}_share": round(v / total, 3) if total else 0.0 for k, v in totals.items()},
             "method": "residential streets sampled every 20 m; outline within 60 m; ESA WorldCover 2021 built-up within 40 m",
             "sources": ["OpenStreetMap", "Microsoft Global ML Building Footprints", "ESA WorldCover 2021 (10 m)",
                         "Meta/WRI canopy height (tree cover)"],
             "checked_alternatives": "Overture Maps 2026-10-21.0 buildings over Skvyra = the same OSM + Microsoft outlines (1 extra OSM building); no other legal open footprint source found"}
    (C.GENERATED / "quality_gaps.json").write_text(json.dumps({"stats": stats, "runs": runs}, ensure_ascii=False, separators=(",", ":")))
    log.info("quality layer: %.1f km residential streets, suspect gaps %.1f%%, insufficient %.1f%%, no signs %.1f%%",
             total / 1000, 100 * stats["suspect_gap_share"], 100 * stats["insufficient_share"], 100 * stats["no_signs_share"])
    return stats
