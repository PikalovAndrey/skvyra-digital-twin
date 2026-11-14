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
    for sid, o in doc.get("buildings", {}).items():
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
