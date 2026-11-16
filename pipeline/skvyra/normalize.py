"""Normalize raw sources into clean, layered GeoJSON (WGS84) with provenance and stable ids.

Outputs (data/processed/):
  buildings.geojson   OSM buildings + Microsoft ML footprints that fill OSM gaps (conflated)
  roads.geojson       highway lines, clipped to the AOI
  railways.geojson    railway lines
  waterways.geojson   river/stream centre lines
  areas.geojson       land cover / land use / water polygons with a normalized `cover` class
  trees.geojson       individually mapped trees (natural=tree)
  pois.geojson        named points of interest (search index, landmarks)
  quarantine.geojson  features that could not be safely repaired, with the reason
  normalize_report.json
"""
import gzip
import hashlib
import json
from collections import Counter

import shapely
from shapely.geometry import mapping, shape
from shapely.prepared import prep

from . import acquire, config as C
from .geo import Frame
from .log import log
from .osm import element_geometry, source_id

MIN_BUILDING_M2 = 4.0          # below this a "building" is almost certainly noise or a mapping slip
MS_OSM_OVERLAP = 0.10          # ML footprint dropped if >10% of it is covered by an OSM building
COMMERCIAL_AMENITY = {"cafe", "restaurant", "pharmacy", "bank", "fast_food", "bar", "pub", "post_office", "atm",
                      "marketplace", "car_wash", "fuel", "dentist", "doctors", "veterinary"}
SENSITIVE = {("landuse", "military"), ("military", "*"), ("power", "substation"), ("man_made", "bunker_silo")}

ROAD_TAGS = ["highway", "name", "name:en", "ref", "width", "lanes", "surface", "bridge", "tunnel", "layer",
             "oneway", "service", "sidewalk", "footway"]
BUILDING_TAGS = ["building", "height", "min_height", "building:levels", "building:min_level", "roof:shape",
                 "roof:height", "roof:levels", "roof:colour", "building:colour", "building:material",
                 "roof:material", "name", "amenity", "addr:street", "addr:housenumber", "start_date",
                 "religion", "denomination", "historic", "tourism", "shop", "office", "man_made"]

HARD_SURFACE = {"paved", "asphalt", "concrete", "acrylic", "tartan", "rubber", "paving_stones"}
TRACK_WIDTH_M = 5.0  # running track drawn as its line (no width tag in Skvyra): estimate, not a measurement
COVER = [  # first match wins
    ("water", lambda t: t.get("natural") == "water" or t.get("waterway") in ("riverbank", "dock")
     or t.get("landuse") in ("reservoir", "basin")),
    ("wetland", lambda t: t.get("natural") == "wetland"),
    ("forest", lambda t: t.get("natural") == "wood" or t.get("landuse") == "forest"),
    ("scrub", lambda t: t.get("natural") in ("scrub", "heath")),
    ("park", lambda t: t.get("leisure") in ("park", "garden")),
    ("track", lambda t: t.get("leisure") == "track"),     # area=yes / multipolygon tracks; track lines: TRACK_WIDTH_M
    ("court", lambda t: t.get("leisure") == "pitch" and t.get("surface") in HARD_SURFACE),
    ("pitch", lambda t: t.get("leisure") in ("pitch", "playground", "sports_centre")),
    ("stadium", lambda t: t.get("leisure") == "stadium"),  # stands, paths, grass: no tint, the satellite colour shows
    ("grass", lambda t: t.get("landuse") in ("grass", "meadow", "village_green", "recreation_ground")
     or t.get("natural") in ("grassland",)),
    ("cemetery", lambda t: t.get("landuse") == "cemetery" or t.get("amenity") == "grave_yard"),
    ("orchard", lambda t: t.get("landuse") in ("orchard", "vineyard", "allotments")),
    ("farmland", lambda t: t.get("landuse") in ("farmland", "farmyard", "greenhouse_horticulture")),
    ("paved", lambda t: t.get("amenity") in ("parking", "marketplace") or t.get("highway") in ("pedestrian",)
     or "area:highway" in t or t.get("place") == "square"),
    ("industrial", lambda t: t.get("landuse") in ("industrial", "railway", "garages", "construction", "depot")),
    ("commercial", lambda t: t.get("landuse") in ("commercial", "retail")),
    ("residential", lambda t: t.get("landuse") == "residential"),
    ("institutional", lambda t: t.get("amenity") in ("school", "hospital", "kindergarten", "college",
                                                      "university", "clinic")),
]


def _width(v) -> float | None:
    try:
        w = float(str(v).split()[0].replace(",", "."))
        return w if w > 0 else None
    except (ValueError, IndexError):
        return None


def track_band(line, tags: dict):
    """A running track mapped as its line -> (band polygon along the line, width m, 'source' | 'estimate')."""
    w = _width(tags.get("width"))
    how = "source" if w else "estimate"
    w = w or TRACK_WIDTH_M
    return line.buffer(w / 2, cap_style="flat", join_style="round"), w, how


def is_sensitive(tags: dict) -> bool:
    return any(tags.get(k) == v or (v == "*" and k in tags) for k, v in SENSITIVE)


def cover_class(tags: dict) -> str | None:
    for name, test in COVER:
        if test(tags):
            return name
    return None


def stable_ms_id(g_metric) -> str:
    """Microsoft footprints have no ids; derive one from the quantized centroid + area (metric).
    Stable across reruns on the same release; overrides keyed to it are re-matched by proximity if a
    new release shifts the footprint (see overrides.py)."""
    c = g_metric.centroid
    key = f"{round(c.x * 2) / 2:.1f},{round(c.y * 2) / 2:.1f},{round(g_metric.area)}"
    return "ms:" + hashlib.sha1(key.encode()).hexdigest()[:12]


def internal_id(src: str) -> str:
    return "b" + hashlib.sha1(src.encode()).hexdigest()[:11]


def polygon_parts(g):
    """Repair and keep polygonal parts. -> (list[Polygon], repaired: bool)."""
    repaired = False
    if not g.is_valid:
        g = shapely.make_valid(g)
        repaired = True
    g = shapely.normalize(g)  # canonical ring orientation and start vertex
    if g.geom_type == "Polygon":
        parts = [g]
    elif g.geom_type in ("MultiPolygon", "GeometryCollection"):
        parts = [p for p in shapely.get_parts(g) if p.geom_type == "Polygon"]
        if g.geom_type == "GeometryCollection":
            parts = [q for p in parts for q in (shapely.get_parts(p) if p.geom_type != "Polygon" else [p])]
    else:
        parts = []
    return parts, repaired


def feature(geom_wgs, props) -> dict:
    return {"type": "Feature", "properties": props,
            "geometry": mapping(shapely.set_precision(geom_wgs, 1e-7))}


def write(name: str, feats: list) -> None:
    C.PROCESSED.mkdir(parents=True, exist_ok=True)
    (C.PROCESSED / name).write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                                               ensure_ascii=False, separators=(",", ":")))


def run(cfg: dict) -> dict:
    frame = Frame(cfg["crs_metric"], cfg["origin_utm"])
    boundary_wgs = shape(json.loads((C.PROCESSED / "boundary.geojson").read_text())["features"][0]["geometry"])
    boundary = frame.geom_to_local(boundary_wgs)
    aoi = boundary.buffer(cfg["context_buffer_m"])
    aoi_p, bnd_p = prep(aoi), prep(boundary)
    manifest = acquire.load_manifest()
    osm_ts = manifest["osm.json"].get("source_timestamp")
    doc = json.loads((C.RAW / "osm.json").read_text())

    stats = Counter()
    commercial_points = []
    quarantine, buildings, roads, rails, waterways, areas, trees, pois = [], [], [], [], [], [], [], []
    seen_geom = {}

    def quarantine_add(sid, reason, geom_wgs=None, tags=None):
        stats[f"quarantine:{reason.split(':')[0]}"] += 1
        quarantine.append({"type": "Feature", "geometry": mapping(geom_wgs) if geom_wgs is not None else None,
                           "properties": {"source_id": sid, "reason": reason, "tags": tags or {}}})

    for el in doc["elements"]:
        tags = el.get("tags", {})
        sid = source_id(el)
        if is_sensitive(tags):
            stats["suppressed_sensitive"] += 1  # never reconstructed; counted so the omission is visible
            continue
        geom, problem = element_geometry(el)
        if problem and problem.startswith("skip:"):
            stats[problem] += 1
            continue
        if problem:
            quarantine_add(sid, problem, tags=tags)
            continue
        g = frame.geom_to_local(geom)
        if not aoi_p.intersects(g):
            stats["outside_aoi"] += 1
            continue

        if "building" in tags and tags["building"] != "no":
            if g.geom_type == "Point":
                quarantine_add(sid, "building_mapped_as_node", geom, tags)
                continue
            if g.geom_type == "LineString":
                quarantine_add(sid, "building_not_closed", geom, tags)
                continue
            parts, repaired = polygon_parts(g)
            stats["osm_building_repaired"] += repaired
            if not parts:
                quarantine_add(sid, "unrepairable_polygon", geom, tags)
                continue
            for i, p in enumerate(parts):
                if p.area < MIN_BUILDING_M2:
                    quarantine_add(f"{sid}#{i}", f"too_small:{p.area:.1f}m2", frame.geom_to_geo(p), tags)
                    continue
                key = shapely.to_wkb(shapely.set_precision(p, 0.01))
                if key in seen_geom:
                    stats["osm_building_duplicate"] += 1
                    continue
                psid = sid if len(parts) == 1 else f"{sid}#{i}"
                seen_geom[key] = psid
                props = {"id": internal_id(psid), "source_id": psid, "source": "osm", "source_timestamp": osm_ts,
                         "repaired": repaired, "in_boundary": bnd_p.contains(p.representative_point()),
                         "area_m2": round(p.area, 1), "tags": {k: tags[k] for k in BUILDING_TAGS if k in tags}}
                buildings.append((p, props))
            continue

        if g.geom_type == "Point":
            ck = next((f"{k}={tags[k]}" for k in ("shop", "craft", "office") if k in tags), None) or (
                f"amenity={tags['amenity']}" if tags.get("amenity") in COMMERCIAL_AMENITY else None)
            if ck:
                commercial_points.append((g, ck))
            if tags.get("natural") == "tree":
                trees.append(feature(geom, {"source_id": sid, "source": "osm",
                                            "tags": {k: tags[k] for k in ("species", "leaf_type", "height") if k in tags}}))
            elif tags.get("name") and any(k in tags for k in ("amenity", "shop", "tourism", "historic", "place", "office")):
                pois.append(feature(geom, {"source_id": sid, "name": tags["name"], "name_en": tags.get("name:en"),
                                           "kind": next(f"{k}={tags[k]}" for k in ("amenity", "shop", "tourism", "historic", "place", "office") if k in tags)}))
            continue

        if g.geom_type == "LineString":
            clipped = g.intersection(aoi)
            if clipped.is_empty:
                continue
            for part in shapely.get_parts(clipped):
                if part.geom_type != "LineString" or part.length < 0.5:
                    continue
                pg = frame.geom_to_geo(part)
                if "highway" in tags:
                    roads.append(feature(pg, {"source_id": sid, **{k: tags[k] for k in ROAD_TAGS if k in tags}}))
                elif "railway" in tags and tags["railway"] in ("rail", "narrow_gauge", "light_rail", "tram", "siding"):
                    rails.append(feature(pg, {"source_id": sid, "railway": tags["railway"], "name": tags.get("name"),
                                              "bridge": tags.get("bridge")}))
                elif tags.get("leisure") == "track":
                    band, w, how = track_band(part, tags)
                    areas.append(feature(frame.geom_to_geo(band), {"source_id": sid, "cover": "track", "name": tags.get("name"),
                                                                   "repaired": False, "width_m": w, "width_method": how}))
                elif "waterway" in tags and tags["waterway"] in ("river", "stream", "canal", "ditch", "drain"):
                    waterways.append(feature(pg, {"source_id": sid, "waterway": tags["waterway"], "name": tags.get("name"),
                                                  "width": tags.get("width"), "tunnel": tags.get("tunnel")}))
                else:
                    stats["line_unused"] += 1
            continue

        cover = cover_class(tags)
        if cover:
            parts, repaired = polygon_parts(g.intersection(aoi) if not aoi.contains(g) else g)
            for i, p in enumerate(parts):
                if p.area < 1.0:
                    continue
                areas.append(feature(frame.geom_to_geo(p), {"source_id": sid if len(parts) == 1 else f"{sid}#{i}",
                                                            "cover": cover, "name": tags.get("name"),
                                                            "repaired": repaired}))
            if tags.get("name") and any(k in tags for k in ("amenity", "leisure", "tourism", "historic")):
                pois.append(feature(frame.geom_to_geo(g.representative_point()),
                                    {"source_id": sid, "name": tags["name"], "name_en": tags.get("name:en"),
                                     "kind": next(f"{k}={tags[k]}" for k in ("amenity", "leisure", "tourism", "historic") if k in tags)}))
        else:
            stats["area_unused"] += 1

    # --- Microsoft ML footprints: only where OSM has no building --------------------------------------
    osm_tree = shapely.STRtree([p for p, _ in buildings])
    ms_paths = sorted(C.RAW.glob("ms_buildings_*.csv.gz"))  # one file per zoom-9 quadkey; read them all
    if not ms_paths:
        log.warning("no Microsoft footprint file in data/raw - building coverage limited to OSM")

    def ms_lines():
        for path in ms_paths:
            meta = manifest.get(path.name, {})
            for line in gzip.open(path, "rt"):
                yield line, meta

    seen_ms = set()
    if ms_paths:
        for line, ms_meta in ms_lines():
            f = json.loads(line)
            g = frame.geom_to_local(shape(f["geometry"]))
            if not aoi_p.intersects(g):
                continue
            stats["ms_in_aoi"] += 1
            parts, repaired = polygon_parts(g)
            if not parts:
                stats["ms_unrepairable"] += 1
                continue
            p = max(parts, key=lambda q: q.area)
            if p.area < MIN_BUILDING_M2:
                stats["ms_too_small"] += 1
                continue
            hits = osm_tree.query(p, predicate="intersects")
            covered = sum(p.intersection(buildings[i][0]).area for i in hits) / p.area
            if covered > MS_OSM_OVERLAP:
                stats["ms_matched_osm"] += 1
                continue
            sid = stable_ms_id(p)
            if sid in seen_ms:  # the same footprint can sit in two neighbouring quadkey files
                stats["ms_duplicate"] += 1
                continue
            seen_ms.add(sid)
            h = f["properties"].get("height", -1)
            buildings.append((p, {"id": internal_id(sid), "source_id": sid, "source": "ms_ml",
                                  "source_timestamp": ms_meta.get("source_timestamp"), "repaired": repaired,
                                  "in_boundary": bnd_p.contains(p.representative_point()), "area_m2": round(p.area, 1),
                                  "ml_height": h if h and h > 0 else None,
                                  "ml_confidence": f["properties"].get("confidence"), "tags": {}}))
            stats["ms_added"] += 1

    # ML footprints crossed by a vehicle road centre line: OSM (human-mapped) roads win, footprint quarantined
    vehicle = {"primary", "secondary", "tertiary", "unclassified", "residential", "living_street", "trunk"}
    road_lines = [frame.geom_to_local(shape(f["geometry"])) for f in roads
                  if f["properties"].get("highway") in vehicle and not f["properties"].get("tunnel") and not f["properties"].get("layer")]
    if road_lines:
        rtree = shapely.STRtree(road_lines)
        kept = []
        for p, props in buildings:
            if props["source"] == "ms_ml":
                inset = p.buffer(-1.0)
                hit = next((i for i in rtree.query(p, predicate="intersects") if road_lines[i].intersection(inset).length > 2), None)
                if hit is not None:
                    quarantine_add(props["source_id"], "ml_footprint_on_road", frame.geom_to_geo(p), {})
                    continue
            kept.append((p, props))
        buildings = kept

    # local outlines (data/overrides/footprints.geojson): confirmed ones join the model, approximate ones only the
    # review layer; sources stay untouched (see overrides.py)
    from . import overrides as OV
    buildings, fp_report = OV.apply_footprints(buildings, frame, bnd_p, road_lines, internal_id)
    stats["local_outlines_applied"] = len(fp_report["applied"])
    stats["local_outlines_review_only"] = len(fp_report["review"])

    # shops / services inside a building (points) -> facade hint
    if commercial_points:
        btree = shapely.STRtree([p for p, _ in buildings])
        for pt, kind in commercial_points:
            for i in btree.query(pt, predicate="within"):
                buildings[i][1].setdefault("poi", []).append(kind)
        stats["buildings_with_commercial_poi"] = sum(1 for _, p in buildings if p.get("poi"))

    # overlap report (overlapping footprints z-fight and usually mean a mapping error)
    tree = shapely.STRtree([p for p, _ in buildings])
    overl = 0
    for i, (p, props) in enumerate(buildings):
        for j in tree.query(p, predicate="intersects"):
            if j > i:
                q = buildings[j][0]
                if p.intersection(q).area > 0.5 * min(p.area, q.area):
                    overl += 1
                    props.setdefault("flags", []).append("overlaps:" + buildings[j][1]["source_id"])
    stats["building_overlaps"] = overl

    ids = Counter(props["id"] for _, props in buildings)
    if any(c > 1 for c in ids.values()):
        raise RuntimeError(f"stable id collision: {[k for k, c in ids.items() if c > 1][:5]}")

    # paint colours read from licensed photos (config/surfaces.json); geometry and cover class stay OSM's
    sf = C.ROOT / "config" / "surfaces.json"
    photo = {c["source_id"]: c for c in json.loads(sf.read_text())["colours"]} if sf.exists() else {}
    for f in areas:
        c = photo.get(f["properties"]["source_id"])
        if c:
            f["properties"].update(colour=c["colour"], colour_method="photo_estimate", colour_date=c["date"])

    buildings.sort(key=lambda b: b[1]["id"])  # deterministic output order
    write("buildings.geojson", [feature(frame.geom_to_geo(p), props) for p, props in buildings])
    for name, items in [("roads", roads), ("railways", rails), ("waterways", waterways), ("areas", areas),
                        ("trees", trees), ("pois", pois), ("quarantine", quarantine)]:
        items.sort(key=lambda f: f["properties"]["source_id"])
        write(f"{name}.geojson", items)

    report = {"osm_source_timestamp": osm_ts, "counts": {
        "buildings": len(buildings), "buildings_osm": sum(1 for _, p in buildings if p["source"] == "osm"),
        "buildings_ms": sum(1 for _, p in buildings if p["source"] == "ms_ml"),
        "buildings_local": sum(1 for _, p in buildings if p["source"] == "local"),
        "buildings_in_boundary": sum(1 for _, p in buildings if p["in_boundary"]),
        "roads": len(roads), "railways": len(rails), "waterways": len(waterways), "areas": len(areas),
        "trees": len(trees), "pois": len(pois), "quarantined": len(quarantine)}, "stats": dict(sorted(stats.items()))}
    (C.PROCESSED / "normalize_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    log.info("normalize: %s", report["counts"])
    if stats.get("quarantine:unrepairable_polygon", 0) > 0.05 * max(1, len(buildings)):
        raise RuntimeError("more than 5% of buildings unrepairable - source data problem, refusing to continue")
    return report
