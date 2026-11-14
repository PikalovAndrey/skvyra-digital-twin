"""Match the landmark catalogue (config/landmarks.json, Wikidata coordinates) to building footprints."""
import json

import shapely

from . import config as C
from .log import log

CATALOGUE = C.ROOT / "config" / "landmarks.json"


def _probable(buildings, tree, pt, street, radius=45.0, max_to_street=15.0):
    """Second tier for listed buildings whose Wikidata point is off: exactly one non-shed footprint within
    `radius` that fronts the street of the register address. Returns index or None."""
    if street is None:
        return None
    cands = [i for i in tree.query(pt.buffer(radius)) if buildings[i][0].distance(pt) <= radius
             and buildings[i][0].area >= 150 and buildings[i][0].distance(street) <= max_to_street]
    return cands[0] if len(cands) == 1 else None


def match(buildings, frame, max_dist: float = 30.0, streets=None) -> dict:
    """buildings: [(polygon_local, props)] -> {source_id: landmark + match info}. Writes landmarks_report.json."""
    if not CATALOGUE.exists():
        return {}
    cat = json.loads(CATALOGUE.read_text())["landmarks"]
    tree = shapely.STRtree([g for g, _ in buildings])
    out, report = {}, []
    for lm in cat:
        x, y = frame.to_local(*lm["lonlat"])
        pt = shapely.Point(float(x), float(y))
        inside = [i for i in tree.query(pt, predicate="within")]
        if inside:
            i, d = inside[0], 0.0
        else:
            near = [i for i in tree.query(pt.buffer(max_dist)) if buildings[i][0].distance(pt) <= max_dist]
            if lm.get("facade") == "church" or "Синагога" in lm["name"]:  # prefer a mapped place of worship
                pw = [i for i in near if buildings[i][1].get("tags", {}).get("amenity") == "place_of_worship"]
                near = pw or near
            # nearest, but larger footprints win ties: listed buildings are rarely the shed next door
            near.sort(key=lambda i: (round(buildings[i][0].distance(pt) / 5), -buildings[i][0].area))
            i = near[0] if near else None
            d = buildings[i][0].distance(pt) if i is not None else None
        tier = "exact" if d == 0 else "near"
        if i is None:
            st = (streets or {}).get(lm.get("address_street"))
            i = _probable(buildings, tree, pt, st)
            if i is None:
                report.append({"wikidata": lm["wikidata"], "name": lm["name"], "matched": None,
                               "reason": f"no footprint within {max_dist:.0f} m of the Wikidata coordinate and no unique "
                                         f"candidate fronting {lm.get('address_street') or 'the address street'} within 45 m"})
                continue
            d, tier = buildings[i][0].distance(pt), "probable"
        g, props = buildings[i]
        sid = props["source_id"]
        complexity = g.length / (4 * g.area ** 0.5)  # 1.0 for a square; merged ML blobs score high
        if props["source"] == "ms_ml" and (g.area > lm.get("max_area_m2", 1500) or (complexity > 1.6 and g.area > 800)):
            report.append({"wikidata": lm["wikidata"], "name": lm["name"], "matched": None,
                           "rejected": sid, "reason": f"footprint {g.area:.0f} m2, complexity {complexity:.2f}: "
                           "probably several merged buildings - attributes not applied"})
            continue
        out[sid] = {**lm, "match_distance_m": round(d, 1), "match": tier}
        report.append({"wikidata": lm["wikidata"], "name": lm["name"], "matched": sid, "match": tier,
                       "distance_m": round(d, 1), "footprint_area_m2": round(buildings[i][0].area)})
    unmatched = [r for r in report if not r.get("matched")]
    if unmatched:
        log.warning("%d landmark(s) without a footprint within %.0f m: %s", len(unmatched), max_dist, [r["name"] for r in unmatched])
    (C.PROCESSED / "landmarks_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    return out
