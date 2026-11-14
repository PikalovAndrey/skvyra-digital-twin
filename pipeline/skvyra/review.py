"""Mapping review workbench data (assets/generated/review.json) and the `skvyra review` commands.

The workbench (apps/web review.html) shows each priority item with the source outlines (OSM, Microsoft ML,
confirmed / approximate local outlines), streets, reference points (WLM coordinates, photo camera geotags) and the
missing-outline lines of the quality layer, lets a reviewer draw an outline, reject a candidate match and export a
change set. Changes are applied only by `skvyra review apply` (validation + diff preview + backup), reverted with
`skvyra review revert`. Nothing here edits OSM or the source downloads.
"""
import json
import shutil
from datetime import datetime, timezone

import shapely
from shapely.geometry import mapping, shape

from . import config as C
from . import overrides as OV
from .log import log

PRIORITIES = C.ROOT / "config" / "review_priorities.json"
HISTORY = C.OVERRIDES.parent / "history"
RADIUS = 120.0
CENTRE_RADIUS = 1200.0


def _xy(g):
    return [[round(x, 1), round(y, 1)] for x, y in g.coords]


def _poly(g):
    return [_xy(g.exterior)] + [_xy(r) for r in g.interiors]


def build(cfg, frame):
    """Write assets/generated/review.json."""
    load = lambda n: [(frame.geom_to_local(shape(f["geometry"])), f["properties"])
                      for f in json.loads((C.PROCESSED / n).read_text())["features"] if f.get("geometry")]
    blds, roads = load("buildings.geojson"), load("roads.geojson")
    review = load("review_footprints.geojson") if (C.PROCESSED / "review_footprints.geojson").exists() else []
    btree, rtree = shapely.STRtree([g for g, _ in blds]), shapely.STRtree([g for g, _ in roads])
    gaps = json.loads((C.GENERATED / "quality_gaps.json").read_text())["runs"] if (C.GENERATED / "quality_gaps.json").exists() else []
    pri = json.loads(PRIORITIES.read_text()) if PRIORITIES.exists() else {"items": []}
    items = []

    def context(cx, cy):
        area = shapely.Point(cx, cy).buffer(RADIUS)
        outl = [{"sid": p["source_id"], "src": p["source"], "area": round(g.area), "rings": _poly(g)}
                for g, p in (blds[i] for i in btree.query(area)) for g in shapely.get_parts(g)]
        outl += [{"sid": p["id"], "src": "local_approximate", "area": round(g.area), "rings": _poly(g), "confidence": p["confidence"]}
                 for g, p in review if g.intersects(area) for g in shapely.get_parts(g)]
        st = [{"name": p.get("name"), "line": _xy(g)} for g, p in (roads[i] for i in rtree.query(area.buffer(80)))
              if g.geom_type == "LineString"]
        gl = [{"cls": r["cls"], "conf": r["conf"], "line": [[x, y] for x, y, _ in r["pts"]]} for r in gaps
              if any(area.contains(shapely.Point(x, y)) for x, y, _ in r["pts"])]
        return {"centre": [round(cx, 1), round(cy, 1)], "outlines": outl, "streets": st, "gaps": gl}

    for it in sorted(pri["items"], key=lambda i: i["priority"]):
        pts = [{**p, "xy": [round(float(v), 1) for v in frame.to_local(*p["lonlat"])]} for p in it["points"]]
        cx = sum(p["xy"][0] for p in pts) / len(pts)
        cy = sum(p["xy"][1] for p in pts) / len(pts)
        items.append({**{k: it[k] for k in it if k != "points"}, "points": pts, "kind": "landmark", **context(cx, cy)})
    # other notable gaps in the historic centre: longest medium-confidence suspect runs within 1.2 km of the centre
    centre = (20.0, 140.0)  # the 'centre' viewpoint target (main square), local metres
    cands = [r for r in gaps if r["cls"] == "suspect_gap" and r["conf"] == "medium"
             and min((x - centre[0]) ** 2 + (y - centre[1]) ** 2 for x, y, _ in r["pts"]) < CENTRE_RADIUS ** 2]
    for k, r in enumerate(sorted(cands, key=lambda r: -r["len"])[:5]):
        mx, my, _ = r["pts"][len(r["pts"]) // 2]
        items.append({"id": f"centre-gap-{k + 1}", "title": f"Ймовірна прогалина: {r.get('street') or 'вулиця без назви'} ({r['len']:.0f} м)",
                      "priority": 5 + k, "kind": "gap", "points": [], "candidates": [], "photos": [],
                      "why_unconfirmed": "; ".join(r["reasons"]),
                      "needed": "Outlines from a legally usable source or a local survey; 10 m land cover only shows that something is built here.",
                      **context(mx, my)})
    doc = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "frame": frame.as_dict(), "items": items}
    (C.GENERATED / "review.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
    log.info("review workbench: %d items (%d landmarks, %d centre gaps)", len(items),
             sum(i["kind"] == "landmark" for i in items), sum(i["kind"] == "gap" for i in items))
    return doc


# ------------------------------------------------------------------------------------------------------
# change sets: {"version": 1, "changes": [{"op": "add_outline", "feature": <GeoJSON Feature>},
#                                          {"op": "approve_outline", "id": "local:...", "approval": {by, date, basis, checks}},
#                                          {"op": "remove_outline", "id": "local:..."},
#                                          {"op": "reject_match", "wikidata": "Q...", "source_id": "...", "note": "..."}]}
# ------------------------------------------------------------------------------------------------------
def _read(path, empty):
    return json.loads(path.read_text()) if path.exists() else empty


def plan(changes, frame):
    """Validate a change set against the current data. -> (new footprints doc, new overrides doc, diff lines, problems)."""
    fdoc = _read(OV.FOOTPRINTS, {"type": "FeatureCollection", "features": []})
    odoc = _read(C.OVERRIDES, {"version": 1, "buildings": {}})
    feats = {f["properties"]["id"]: f for f in fdoc["features"]}
    diff, problems = [], []
    for ch in changes.get("changes", []):
        op = ch.get("op")
        if op == "add_outline":
            f = ch["feature"]
            fid = f["properties"].get("id")
            if f["properties"].get("status") == "confirmed":
                problems.append(f"add_outline {fid}: new outlines enter as 'approximate'; confirm with a separate approve_outline change")
            diff.append(f"{'~ replace' if fid in feats else '+ add'} outline {fid} ({f['properties'].get('status')}, "
                        f"confidence {f['properties'].get('confidence')}, method: {f['properties'].get('method')})")
            feats[fid] = f
        elif op == "approve_outline":
            if ch["id"] not in feats:
                problems.append(f"approve_outline: {ch['id']} does not exist")
                continue
            feats[ch["id"]]["properties"] = {**feats[ch["id"]]["properties"], "status": "confirmed", "approval": ch.get("approval")}
            diff.append(f"! approve outline {ch['id']} (basis {(ch.get('approval') or {}).get('basis')}, by {(ch.get('approval') or {}).get('by')})")
        elif op == "remove_outline":
            if ch["id"] in feats:
                del feats[ch["id"]]
                diff.append(f"- remove outline {ch['id']}")
            else:
                problems.append(f"remove_outline: {ch['id']} does not exist")
        elif op == "reject_match":
            lm = odoc.setdefault("landmark_matches", {}).setdefault(ch["wikidata"], {"reject": []})
            if ch["source_id"] not in lm["reject"]:
                lm["reject"].append(ch["source_id"])
                lm["note"] = ch.get("note", lm.get("note", ""))
                diff.append(f"x reject match {ch['wikidata']} -> {ch['source_id']} ({ch.get('note', '')})")
        else:
            problems.append(f"unknown op {op!r}")
    new_fdoc = {"type": "FeatureCollection", "name": "Skvyra local building outlines",
                "crs_note": "RFC 7946: WGS84 longitude/latitude", "features": list(feats.values())}
    # full validation on a temporary copy: schema, geometry, duplicates, overlaps with sources and roads
    tmp = C.PROCESSED / "_review_tmp_footprints.geojson"
    tmp.write_text(json.dumps(new_fdoc, ensure_ascii=False))
    try:
        OV.load_footprints(tmp)
        load = lambda n: [(frame.geom_to_local(shape(f["geometry"])), f["properties"])
                          for f in json.loads((C.PROCESSED / n).read_text())["features"]]
        sources = [(g, p) for g, p in load("buildings.geojson") if p["source"] != "local"]
        roads = [g for g, p in load("roads.geojson") if p.get("highway") in
                 {"primary", "secondary", "tertiary", "unclassified", "residential", "living_street", "trunk"}]
        boundary = frame.geom_to_local(shape(json.loads((C.PROCESSED / "boundary.geojson").read_text())["features"][0]["geometry"]))
        saved = {n: (C.PROCESSED / n).read_bytes() for n in ("footprints_report.json", "review_footprints.geojson") if (C.PROCESSED / n).exists()}
        _, rep = OV.apply_footprints(sources, frame, boundary, roads, lambda s: s, tmp)
        for n, b in saved.items():  # a dry run must not overwrite the reports of the last real build
            (C.PROCESSED / n).write_bytes(b)
        problems += [f"{c['id']}: {c['reason']}" for c in rep["conflicts"]]
        problems += [f"{s['id']}: already mapped in OSM as {', '.join(s['by'])}" for s in rep["superseded"]]
    except ValueError as e:
        problems.append(str(e))
    finally:
        tmp.unlink(missing_ok=True)
    return new_fdoc, odoc, diff, problems


def apply(path, frame, yes=False) -> int:
    changes = json.loads(open(path).read())
    fdoc, odoc, diff, problems = plan(changes, frame)
    print("Planned changes:\n  " + ("\n  ".join(diff) or "(none)"))
    if problems:
        print("Problems (nothing written):\n  " + "\n  ".join(problems))
        return 1
    if not yes:
        print("Dry run. Re-run with --yes to write data/overrides/ (a backup goes to data/overrides/history/).")
        return 0
    HISTORY.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    for f in (OV.FOOTPRINTS, C.OVERRIDES):
        if f.exists():
            shutil.copy2(f, HISTORY / f"{stamp}__{f.name}")
        else:
            (HISTORY / f"{stamp}__{f.name}.absent").write_text("")
    OV.FOOTPRINTS.write_text(json.dumps(fdoc, ensure_ascii=False, indent=1))
    C.OVERRIDES.write_text(json.dumps(odoc, ensure_ascii=False, indent=1))
    print(f"Written. Backup {stamp}. Rebuild with `make validate build` (only affected tiles change).")
    return 0


def revert() -> int:
    """Restore the files saved by the last `apply --yes`."""
    if not HISTORY.exists():
        print("No history.")
        return 1
    stamps = sorted({p.name.split("__")[0] for p in HISTORY.iterdir()})
    if not stamps:
        print("No history.")
        return 1
    last = stamps[-1]
    for f in (OV.FOOTPRINTS, C.OVERRIDES):
        b, absent = HISTORY / f"{last}__{f.name}", HISTORY / f"{last}__{f.name}.absent"
        if b.exists():
            shutil.copy2(b, f)
        elif absent.exists():
            f.unlink(missing_ok=True)
    for p in HISTORY.glob(f"{last}__*"):
        p.unlink()
    print(f"Reverted to the state before {last}.")
    return 0
