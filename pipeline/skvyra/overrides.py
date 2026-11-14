"""Manual corrections, stored separately from source data and keyed by stable source ids.

data/overrides/overrides.json:
{
  "version": 1,
  "buildings": {
    "osm:way/123456": {
      "height": 15.5, "levels": 5, "roof_shape": "hipped", "wall_colour": "#e8dcc0", "roof_colour": "#8e3b2f",
      "suppress": false, "verified": true, "note": "measured from photo", "author": "...", "date": "2026-11-12",
      "anchor": [29.6629, 49.7298], "model": "landmarks/church.glb", "model_rotation_deg": 0
    }
  }
}
Matching: by source id first. If the id no longer exists (OSM way replaced/split, ML footprint shifted in a
new release) and the override has an `anchor` (lon, lat), it is re-matched to the building containing or
nearest (< 4 m) to that point and reported as `rematched`. Otherwise it is reported as a conflict: the
correction is never silently dropped, it stays in the file and shows up in overrides_report.json.
"""
import json

import shapely

from . import config as C
from .log import log

ALLOWED = {"height", "levels", "roof_shape", "wall_colour", "roof_colour", "suppress", "verified", "note",
           "author", "date", "anchor", "model", "model_rotation_deg", "evidence"}


def load() -> dict:
    if not C.OVERRIDES.exists():
        return {}
    doc = json.loads(C.OVERRIDES.read_text())
    out = {}
    for sid, o in doc.get("buildings", {}).items():  # ("landmark_matches" is read by landmark_rejections)
        unknown = set(o) - ALLOWED
        if unknown:
            raise ValueError(f"override {sid}: unknown fields {sorted(unknown)} (allowed: {sorted(ALLOWED)})")
        if "roof_shape" in o and o["roof_shape"] not in ("flat", "gabled", "hipped", "pyramidal", "shed"):
            raise ValueError(f"override {sid}: unsupported roof_shape {o['roof_shape']}")
        if "height" in o and not (1 <= float(o["height"]) <= 200):
            raise ValueError(f"override {sid}: implausible height {o['height']}")
        out[sid] = o
    return out


def match(overrides: dict, buildings: list, frame) -> tuple[dict, dict]:
    """buildings: [(polygon_local, props)]. -> ({source_id: override}, report)."""
    by_sid = {p["source_id"]: i for i, (_, p) in enumerate(buildings)}
    tree = shapely.STRtree([g for g, _ in buildings])
    applied, report = {}, {"applied": [], "rematched": [], "conflicts": []}
    for sid, o in overrides.items():
        if sid in by_sid:
            applied[sid] = o
            report["applied"].append(sid)
            continue
        if o.get("anchor"):
            x, y = frame.to_local(*o["anchor"])
            pt = shapely.Point(float(x), float(y))
            idx = tree.nearest(pt)
            if idx is not None and buildings[idx][0].distance(pt) < 4.0:
                new = buildings[idx][1]["source_id"]
                applied[new] = {**o, "note": (o.get("note") or "") + f" [re-matched from {sid}]"}
                report["rematched"].append({"from": sid, "to": new})
                continue
        report["conflicts"].append({"source_id": sid, "reason": "source feature not found and no anchor match",
                                    "override": o})
    if report["conflicts"]:
        log.warning("%d override(s) could not be matched - see data/processed/overrides_report.json", len(report["conflicts"]))
    (C.PROCESSED / "overrides_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return applied, report


# ------------------------------------------------------------------------------------------------------
# Local building outlines (geometry overrides) - data/overrides/footprints.geojson
# ------------------------------------------------------------------------------------------------------
# A GeoJSON FeatureCollection (RFC 7946: WGS84 lon/lat, which is the CRS of record). Each feature:
#   geometry   Polygon or MultiPolygon, holes allowed
#   properties id (stable, "local:<slug>"), status "confirmed" | "approximate" | "rejected",
#              confidence "high" | "medium" | "low", method (how the outline was obtained), date, author,
#              evidence [{url, license, author, date, note}], note,
#              replaces [{source_id, anchor:[lon,lat]}]  - source outlines this one supersedes (explicit only),
#              wikidata (bind a landmark), tags {building, building:levels, roof:shape, name, ...},
#              approval {by, date, basis, checks}  - required for "confirmed" (see below)
# Approval: an evidence link alone never makes an outline "confirmed". The workbench only exports "approximate"
# outlines; a person promotes one with an `approve_outline` change (skvyra review apply) that states who checked
# it, when, what the geometry itself comes from (APPROVAL_BASES - a photo or a WLM point is not a basis) and what
# was compared. Every evidence entry of a confirmed outline needs a url and a licence.
# Only status "confirmed" enters the city model. "approximate" outlines go to processed/review_footprints.geojson
# for the review workbench and never reach tiles. Source files are never modified; after an OSM / Microsoft
# update, `replaces` ids are re-matched by anchor, and a local outline that is now covered by an OSM building is
# reported (superseded) instead of duplicating it.
FOOTPRINTS = C.OVERRIDES.parent / "footprints.geojson"
STATUSES = {"confirmed", "approximate", "rejected"}
CONFIDENCES = {"high", "medium", "low"}
FP_FIELDS = {"id", "status", "confidence", "method", "date", "author", "evidence", "note", "replaces", "wikidata", "tags",
             "approval"}
APPROVAL_BASES = {"site_survey",        # measured on site (GNSS walk-around, tape)
                  "licensed_imagery",   # traced from imagery whose licence allows derived outlines
                  "official_document",  # a plan / passport with a stated licence or public-domain status
                  "open_dataset"}       # an open building dataset not already among the sources
import re as _re  # noqa: E402


def load_footprints(path=None) -> list:
    """-> [feature]; raises ValueError on schema errors (unknown fields, bad status, duplicate ids)."""
    path = path or FOOTPRINTS
    if not path.exists():
        return []
    doc = json.loads(path.read_text())
    if doc.get("type") != "FeatureCollection":
        raise ValueError(f"{path.name}: must be a GeoJSON FeatureCollection (WGS84 lon/lat)")
    seen, out = set(), []
    for f in doc.get("features", []):
        p = f.get("properties") or {}
        fid = p.get("id", "")
        if not _re.fullmatch(r"local:[a-z0-9][a-z0-9-]{1,60}", fid):
            raise ValueError(f"footprint id {fid!r}: must look like 'local:zemstvo-building'")
        if fid in seen:
            raise ValueError(f"duplicate footprint id {fid}")
        seen.add(fid)
        unknown = set(p) - FP_FIELDS
        if unknown:
            raise ValueError(f"{fid}: unknown fields {sorted(unknown)}")
        if p.get("status") not in STATUSES:
            raise ValueError(f"{fid}: status must be one of {sorted(STATUSES)}")
        if p.get("confidence") not in CONFIDENCES:
            raise ValueError(f"{fid}: confidence must be one of {sorted(CONFIDENCES)}")
        for k in ("method", "date"):
            if not p.get(k):
                raise ValueError(f"{fid}: '{k}' is required")
        if p["status"] == "confirmed":
            if not p.get("evidence") or not all(e.get("url") and e.get("license") for e in p["evidence"]):
                raise ValueError(f"{fid}: a confirmed outline needs evidence entries with url and license")
            a = p.get("approval") or {}
            if not all(a.get(k) for k in ("by", "date", "basis", "checks")):
                raise ValueError(f"{fid}: a confirmed outline needs approval {{by, date, basis, checks}} - an evidence link is not enough")
            if a["basis"] not in APPROVAL_BASES:
                raise ValueError(f"{fid}: approval basis must be one of {sorted(APPROVAL_BASES)} (photos and points only support a check)")
        if (f.get("geometry") or {}).get("type") not in ("Polygon", "MultiPolygon"):
            raise ValueError(f"{fid}: geometry must be Polygon or MultiPolygon")
        out.append(f)
    return out


def check_geometry(g, fid) -> list:
    """Geometry errors of a local outline (local metric coordinates)."""
    from shapely.validation import explain_validity
    errs = []
    if not g.is_valid:
        errs.append(f"invalid geometry: {explain_validity(g)}")
    elif not (6.0 <= g.area <= 20000.0):
        errs.append(f"implausible area {g.area:.1f} m2")
    return errs


def apply_footprints(buildings, frame, boundary_p, road_lines, internal_id, path=None):
    """buildings: [(polygon_local, props)] from the sources. Returns (buildings with confirmed local outlines added
    and explicitly replaced source outlines removed, report). Never edits the sources."""
    from shapely.geometry import shape
    report = {"applied": [], "review": [], "rejected_status": [], "conflicts": [], "superseded": [], "replaced": []}
    feats = load_footprints(path)
    if not feats:
        return buildings, report
    tree = shapely.STRtree([g for g, _ in buildings])
    by_sid = {p["source_id"]: i for i, (_, p) in enumerate(buildings)}
    rtree = shapely.STRtree(road_lines) if road_lines else None
    drop, add, review, local_geoms = set(), [], [], []
    for f in feats:
        p = f["properties"]
        fid = p["id"]
        g = frame.geom_to_local(shape(f["geometry"]))
        errs = check_geometry(g, fid)
        for j, (og, oid) in enumerate(local_geoms):  # duplicates between local outlines
            if g.is_valid and og.intersection(g).area > 0.5 * min(g.area, og.area):
                errs.append(f"duplicates local outline {oid}")
        if p["status"] == "rejected":
            report["rejected_status"].append(fid)
            continue
        if errs:
            report["conflicts"].append({"id": fid, "reason": "; ".join(errs)})
            continue
        local_geoms.append((g, fid))
        if p["status"] == "approximate":  # review only, never in the model
            review.append((g, p))
            report["review"].append(fid)
            continue
        # explicit replacements (re-matched by anchor after a source update)
        repl = set()
        for r in p.get("replaces", []):
            sid = r["source_id"]
            if sid not in by_sid and r.get("anchor"):
                x, y = frame.to_local(*r["anchor"])
                pt = shapely.Point(float(x), float(y))
                hit = [i for i in tree.query(pt, predicate="within")]
                sid = buildings[hit[0]][1]["source_id"] if hit else sid
            if sid in by_sid:
                repl.add(by_sid[sid])
            else:
                report["conflicts"].append({"id": fid, "reason": f"replaced source outline {r['source_id']} not found (no anchor match)"})
        hits = [i for i in tree.query(g, predicate="intersects") if i not in repl and buildings[i][0].intersection(g).area > 1.0]
        osm_cover = [i for i in hits if buildings[i][1]["source"] == "osm" and
                     buildings[i][0].intersection(g).area > 0.5 * min(g.area, buildings[i][0].area)]
        if osm_cover:  # someone mapped it in OSM since: the human-mapped source wins, the local file is kept
            report["superseded"].append({"id": fid, "by": [buildings[i][1]["source_id"] for i in osm_cover]})
            continue
        if hits:
            report["conflicts"].append({"id": fid, "reason": "overlaps source outline(s) " +
                                        ", ".join(buildings[i][1]["source_id"] for i in hits) + " - list them in 'replaces' or fix the outline"})
            continue
        if rtree is not None:
            inset = g.buffer(-1.0)
            crossed = [i for i in rtree.query(g, predicate="intersects") if road_lines[i].intersection(inset).length > 2]
            if crossed:
                report["conflicts"].append({"id": fid, "reason": "crosses a road centre line"})
                continue
        drop |= repl
        report["replaced"] += [buildings[i][1]["source_id"] for i in repl]
        props = {"id": internal_id(fid), "source_id": fid, "source": "local", "source_timestamp": p["date"],
                 "repaired": False, "in_boundary": boundary_p.contains(g.representative_point()), "area_m2": round(g.area, 1),
                 "tags": p.get("tags", {}),
                 "local": {k: p[k] for k in ("confidence", "method", "date", "author", "evidence", "note", "wikidata", "approval") if p.get(k)}}
        parts = list(shapely.get_parts(g))  # MultiPolygon -> one building per part, same provenance
        for k, part in enumerate(parts):
            psid = fid if len(parts) == 1 else f"{fid}#{k}"
            add.append((part, {**props, "id": internal_id(psid), "source_id": psid, "area_m2": round(part.area, 1)}))
        report["applied"].append(fid)
    out = [b for i, b in enumerate(buildings) if i not in drop] + add
    (C.PROCESSED / "review_footprints.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": shapely.geometry.mapping(frame.geom_to_geo(g)), "properties": p} for g, p in review]},
        ensure_ascii=False))
    (C.PROCESSED / "footprints_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    if report["conflicts"]:
        log.warning("%d local outline(s) not applied - see data/processed/footprints_report.json", len(report["conflicts"]))
    log.info("local outlines: %d applied, %d for review only, %d superseded by OSM, %d replaced source outlines",
             len(report["applied"]), len(report["review"]), len(report["superseded"]), len(report["replaced"]))
    return out, report


def landmark_rejections() -> dict:
    """overrides.json "landmark_matches": {"Q...": {"reject": [source_id, ...], "note": ...}} -> {Q: set(ids)}."""
    if not C.OVERRIDES.exists():
        return {}
    doc = json.loads(C.OVERRIDES.read_text())
    return {q: set(v.get("reject", [])) for q, v in doc.get("landmark_matches", {}).items()}
