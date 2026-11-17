"""Coverage statistics, cross-source alignment check and 2D diagnostic maps.

Writes data/processed/audit.json and docs/img/{diagnostic_map,diagnostic_center,coverage_grid}.png.
Percentages are always "of mapped features", never "of real buildings": no authoritative count of real
buildings exists for Skvyra, so the ML layer is used only as an independent second opinion.
"""
import json
from collections import Counter

import numpy as np
import shapely
from shapely.geometry import shape

from . import config as C
from .geo import Frame
from .log import log

IMG = C.ROOT / "docs" / "img"
COVER_COLORS = {"water": "#7fb3d5", "wetland": "#a9cce3", "forest": "#7dab73", "scrub": "#a4c08c", "park": "#a8d08d",
                "pitch": "#b7dfa0", "grass": "#c5e3a6", "cemetery": "#b4c9a8", "orchard": "#cfe0a0",
                "farmland": "#efe9c4", "paved": "#d9d4cc", "industrial": "#e2d4e6", "commercial": "#f4d2cf",
                "residential": "#eeeae4", "institutional": "#f2e6c9"}


def load(name):
    return json.loads((C.PROCESSED / name).read_text())["features"]


def run(cfg: dict) -> dict:
    frame = Frame(cfg["crs_metric"], cfg["origin_utm"])
    L = lambda f: frame.geom_to_local(shape(f["geometry"]))
    boundary = frame.geom_to_local(shape(load("boundary.geojson")[0]["geometry"]))
    bld, roads, areas = load("buildings.geojson"), load("roads.geojson"), load("areas.geojson")
    waterways, trees, pois = load("waterways.geojson"), load("trees.geojson"), load("pois.geojson")
    inb = [f for f in bld if f["properties"]["in_boundary"]]
    osm_b = [f for f in inb if f["properties"]["source"] == "osm"]
    t = lambda k: sum(1 for f in osm_b if k in f["properties"]["tags"])
    road_geoms = [L(f) for f in roads]
    in_roads = [(g, f) for g, f in zip(road_geoms, roads) if g.intersects(boundary)]
    names = {f["properties"].get("name") for _, f in in_roads if f["properties"].get("name")}
    road_len = Counter()
    for g, f in in_roads:
        road_len[f["properties"]["highway"]] += g.intersection(boundary).length
    named_len = sum(g.intersection(boundary).length for g, f in in_roads if f["properties"].get("name"))
    vehicle = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
               "living_street", "service", "primary_link", "secondary_link", "tertiary_link"}
    veh_len = sum(v for k, v in road_len.items() if k in vehicle)
    veh_named = sum(g.intersection(boundary).length for g, f in in_roads
                    if f["properties"].get("name") and f["properties"]["highway"] in vehicle and f["properties"]["highway"] != "service")
    veh_nonservice = sum(v for k, v in road_len.items() if k in vehicle and k != "service")
    covers = Counter(f["properties"]["cover"] for f in areas)
    vegetation = sum(covers[c] for c in ("forest", "scrub", "park", "grass", "orchard", "wetland")) + len(trees)

    # --- independent cross-check: OSM vs Microsoft ML footprint alignment ---------------------------
    import gzip
    ms_paths = sorted(C.RAW.glob("ms_buildings_*.csv.gz"))
    offsets = []
    if ms_paths:
        osm_geoms = [L(f) for f in osm_b]
        tree = shapely.STRtree(osm_geoms)
        from shapely.prepared import prep
        bpre = prep(boundary)
        for line in (ln for p in ms_paths for ln in gzip.open(p, "rt")):
            g = frame.geom_to_local(shape(json.loads(line)["geometry"]))
            if not bpre.intersects(g):
                continue
            hits = tree.query(g, predicate="intersects")
            if len(hits) == 1:
                o = osm_geoms[hits[0]]
                if o.intersection(g).area / max(o.union(g).area, 1e-9) > 0.5:  # confident 1:1 match
                    d = np.array(g.centroid.coords[0]) - np.array(o.centroid.coords[0])
                    offsets.append(d)
    off = np.array(offsets) if offsets else np.zeros((0, 2))

    # --- coverage grid -------------------------------------------------------------------------------
    cell = 250.0
    minx, miny, maxx, maxy = boundary.bounds
    nx, ny = int(np.ceil((maxx - minx) / cell)), int(np.ceil((maxy - miny) / cell))
    grid_osm, grid_ms = np.zeros((ny, nx)), np.zeros((ny, nx))
    cents = [(L(f).centroid, f["properties"]["source"]) for f in inb]
    for c, src in cents:
        i, j = int((c.y - miny) // cell), int((c.x - minx) // cell)
        if 0 <= i < ny and 0 <= j < nx:
            (grid_osm if src == "osm" else grid_ms)[i, j] += 1
    total = grid_osm + grid_ms
    with np.errstate(invalid="ignore", divide="ignore"):
        share = np.where(total >= 5, grid_osm / total, np.nan)
    poor = int(np.sum(share < 0.2))

    dem_stats = None
    try:
        import rasterio
        with rasterio.open(C.RAW / "dem_copernicus_glo30.tif") as src:
            a = src.read(1, masked=True)
            dem_stats = {"min": float(a.min()), "max": float(a.max()), "mean": float(a.mean()),
                         "nodata_cells": int(np.ma.count_masked(a)), "shape": list(a.shape),
                         "res_deg": list(src.res)}
    except Exception as e:
        log.warning("DEM stats unavailable: %s", e)

    audit = {
        "boundary_area_km2": round(boundary.area / 1e6, 3),
        "buildings": {
            "total_in_boundary": len(inb), "osm": len(osm_b), "ms_ml_gap_fill": len(inb) - len(osm_b),
            "osm_with_height": t("height"), "osm_with_levels": t("building:levels"),
            "osm_with_roof_shape": t("roof:shape"), "osm_with_address": t("addr:housenumber"),
            "osm_with_name": t("name"),
            "pct_osm_with_height_or_levels": round(100 * sum(1 for f in osm_b if {"height", "building:levels"} & set(f["properties"]["tags"])) / max(1, len(osm_b)), 2),
            "osm_types": dict(Counter(f["properties"]["tags"].get("building") for f in osm_b).most_common()),
        },
        "osm_vs_ms_alignment": {
            "matched_pairs": int(len(off)),
            "median_offset_m": [round(float(v), 2) for v in np.median(off, axis=0)] if len(off) else None,
            "median_abs_offset_m": round(float(np.median(np.hypot(off[:, 0], off[:, 1]))), 2) if len(off) else None,
            "p90_abs_offset_m": round(float(np.percentile(np.hypot(off[:, 0], off[:, 1]), 90)), 2) if len(off) else None,
        },
        "roads": {"segments_in_boundary": len(in_roads), "named_streets_unique": len(names),
                  "length_km_by_class": {k: round(v / 1000, 2) for k, v in road_len.most_common()},
                  "total_km": round(sum(road_len.values()) / 1000, 2),
                  "pct_length_named": round(100 * named_len / max(1, sum(road_len.values())), 1),
                  "pct_vehicle_nonservice_length_named": round(100 * veh_named / max(1, veh_nonservice), 1),
                  "with_width": sum(1 for _, f in in_roads if "width" in f["properties"]),
                  "with_surface": sum(1 for _, f in in_roads if "surface" in f["properties"]),
                  "bridges": sum(1 for _, f in in_roads if f["properties"].get("bridge"))},
        "water": {"polygons": covers["water"], "waterway_lines": len(waterways)},
        "landuse_polygons": dict(covers.most_common()),
        "vegetation_features": vegetation, "mapped_trees": len(trees), "pois_named": len(pois),
        "coverage_grid": {"cell_m": cell, "cells_with_5plus_buildings": int(np.sum(total >= 5)),
                          "cells_osm_share_below_20pct": poor},
        "dem": dem_stats,
    }
    audit["building_reconciliation"] = reconcile(frame, boundary)
    (C.PROCESSED / "audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False))
    log.info("audit: %d buildings in boundary (%d OSM), %d named streets, OSM-ML median offset %s m",
             len(inb), len(osm_b), len(names), audit["osm_vs_ms_alignment"]["median_abs_offset_m"])
    draw(frame, boundary, bld, road_geoms, roads, areas, waterways, share, (minx, miny, cell), cfg)
    return audit


def reconcile(frame, boundary) -> dict:
    """Every building-like source record accounted for: accepted, rejected (with reason), generated (none)."""
    rep = json.loads((C.PROCESSED / "normalize_report.json").read_text())
    st, cnt = rep["stats"], rep["counts"]
    q = Counter(f["properties"]["reason"].split(":")[0] for f in load("quarantine.geojson")
                if f["properties"]["source_id"].startswith(("osm:", "ms:")))
    osm_q = sum(v for k, v in q.items() if k != "ml_footprint_on_road")
    bld = load("buildings.geojson")
    inb = Counter(f["properties"]["source"] for f in bld if f["properties"]["in_boundary"])
    tile_n = None
    tiles = C.GENERATED / "tiles"
    if (tiles / "index.json").exists():
        tile_n = sum(sum(not b.get("suppressed") for b in json.loads(p.read_text())["buildings"]) for p in tiles.glob("t*.json"))
    return {
        "osm": {"accepted_parts": cnt["buildings_osm"], "duplicates_dropped": st.get("osm_building_duplicate", 0),
                "quarantined": osm_q, "quarantine_reasons": {k: v for k, v in q.items() if k != "ml_footprint_on_road"},
                "repaired": st.get("osm_building_repaired", 0)},
        "microsoft_ml": {"in_aoi": st.get("ms_in_aoi", 0), "dropped_overlapping_osm": st.get("ms_matched_osm", 0),
                         "dropped_too_small": st.get("ms_too_small", 0), "dropped_duplicate": st.get("ms_duplicate", 0),
                         "dropped_unrepairable": st.get("ms_unrepairable", 0),
                         "quarantined_on_road": q.get("ml_footprint_on_road", 0), "accepted": cnt["buildings_ms"]},
        "accepted_total_aoi": cnt["buildings"], "accepted_in_boundary": dict(inb),
        "accepted_in_boundary_total": sum(inb.values()), "accepted_context_ring": cnt["buildings"] - sum(inb.values()),
        "generated_buildings": 0, "suppressed_by_override": 0, "meshed_in_tiles": tile_n,
        "note": "No building is ever generated; procedural content is limited to trees, ground detail and facade/roof appearance.",
    }


def draw(frame, boundary, bld, road_geoms, roads, areas, waterways, share, grid, cfg):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection, PolyCollection
    IMG.mkdir(parents=True, exist_ok=True)
    L = lambda f: frame.geom_to_local(shape(f["geometry"]))

    def polys(geoms):
        out = []
        for g in geoms:
            for p in shapely.get_parts(g):
                if p.geom_type == "Polygon":
                    out.append(np.asarray(p.exterior.coords))
        return out

    def plot(ax, extent=None, lw_scale=1.0):
        for cover, col in COVER_COLORS.items():
            ps = polys([L(f) for f in areas if f["properties"]["cover"] == cover])
            ax.add_collection(PolyCollection(ps, facecolors=col, edgecolors="none", zorder=1))
        ax.add_collection(LineCollection([np.asarray(L(f).coords) for f in waterways], colors="#3b83bd",
                                         linewidths=1.2 * lw_scale, zorder=2))
        major = {"primary", "secondary", "tertiary", "trunk"}
        for sel, col, lw in [(lambda h: h not in major, "#ffffff", 1.2), (lambda h: h in major, "#f5b041", 2.4)]:
            segs = [np.asarray(g.coords) for g, f in zip(road_geoms, roads) if sel(f["properties"]["highway"])]
            ax.add_collection(LineCollection(segs, colors="#888", linewidths=(lw + 0.8) * lw_scale, zorder=3))
            ax.add_collection(LineCollection(segs, colors=col, linewidths=lw * lw_scale, zorder=4))
        for src, col in [("osm", "#2e5c8a"), ("ms_ml", "#d35400")]:
            ax.add_collection(PolyCollection(polys([L(f) for f in bld if f["properties"]["source"] == src]),
                                             facecolors=col, edgecolors="none", zorder=5))
        bx, by = boundary.exterior.xy if boundary.geom_type == "Polygon" else boundary.convex_hull.exterior.xy
        ax.plot(bx, by, color="#c0392b", lw=1.5, ls="--", zorder=6)
        ax.set_aspect("equal")
        if extent:
            ax.set_xlim(extent[0], extent[2]); ax.set_ylim(extent[1], extent[3])
        else:
            ax.autoscale_view()
        ax.set_facecolor("#f6f4ef")
        ax.set_xlabel("east of origin, m"); ax.set_ylabel("north of origin, m")

    origin = frame.as_dict()["origin_wgs84"]
    fig, ax = plt.subplots(figsize=(14, 12), dpi=110)
    plot(ax)
    ax.set_title(f"Skvyra - diagnostic map (EPSG:32635, origin {origin[1]:.5f}N {origin[0]:.5f}E)\n"
                 "blue = OSM buildings, orange = Microsoft ML footprints (gap fill), dashed red = OSM town boundary")
    fig.text(0.01, 0.01, f"{cfg['osm_license']}; Microsoft ML footprints CDLA-Permissive-2.0; produced using Copernicus WorldDEM-30", fontsize=8)
    fig.savefig(IMG / "diagnostic_map.png", bbox_inches="tight"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 10), dpi=110)
    plot(ax, extent=(-300, -400, 500, 400), lw_scale=2.5)
    ax.set_title("Centre detail (local -300..500 E, -400..400 N)")
    fig.savefig(IMG / "diagnostic_center.png", bbox_inches="tight"); plt.close(fig)

    minx, miny, cell = grid
    fig, ax = plt.subplots(figsize=(10, 9), dpi=100)
    im = ax.imshow(share, origin="lower", cmap="RdYlGn", vmin=0, vmax=1,
                   extent=(minx, minx + share.shape[1] * cell, miny, miny + share.shape[0] * cell))
    bx, by = boundary.exterior.xy
    ax.plot(bx, by, color="k", lw=1)
    fig.colorbar(im, label="share of buildings that are human-mapped in OSM (cells with >=5 buildings)")
    ax.set_title("OSM building coverage vs Microsoft ML footprints (red = OSM data-poor)")
    fig.savefig(IMG / "coverage_grid.png", bbox_inches="tight"); plt.close(fig)
