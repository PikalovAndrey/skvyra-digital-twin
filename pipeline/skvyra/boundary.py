"""Settlement boundary: fetch, verify identity, and allow manual correction.

The boundary is the OSM administrative relation for the town itself (admin_level 9 in Ukraine,
place=town), not the hromada (admin_level 7) or the historic raion. Identity is verified against the
expected tags in config, so a renumbered or retagged relation fails loudly instead of silently giving
us the wrong area. A manual override polygon (data/overrides/boundary.geojson) wins if present.
"""
import json

import shapely
from shapely.geometry import mapping, shape

from . import acquire, config as C
from .log import log
from .osm import relation_geometry

MANUAL = C.ROOT / "data" / "overrides" / "boundary.geojson"


class BoundaryError(RuntimeError):
    pass


def load(cfg: dict, offline: bool = False):
    """-> (polygon in WGS84, info dict)."""
    if MANUAL.exists():
        g = shape(json.loads(MANUAL.read_text())["features"][0]["geometry"])
        log.warning("using MANUAL boundary override %s", MANUAL)
        return g, {"source": "manual override", "file": str(MANUAL.relative_to(C.ROOT)), "confidence": "user-asserted"}
    rel = cfg["boundary"]["osm_relation"]
    path = acquire.overpass("boundary.json", acquire.boundary_query(rel), cfg, offline=offline)
    doc = json.loads(path.read_text())
    rels = [e for e in doc["elements"] if e["type"] == "relation" and e["id"] == rel]
    if not rels:
        raise BoundaryError(f"relation {rel} not returned by Overpass (deleted?). Review config.boundary.")
    el = rels[0]
    tags = el.get("tags", {})
    wrong = {k: (tags.get(k), v) for k, v in cfg["boundary"].get("expect", {}).items() if tags.get(k) != v}
    if wrong:
        raise BoundaryError(f"relation {rel} tags differ from expected {wrong}: wrong settlement or "
                            "administrative level? Fix config.boundary or add a manual boundary override.")
    geom, problem = relation_geometry(el)
    if problem:
        raise BoundaryError(f"boundary relation {rel} cannot be assembled: {problem}")
    if not geom.is_valid:
        geom = shapely.make_valid(geom)
    meta = acquire.load_manifest()["boundary.json"]
    return geom, {"source": "OpenStreetMap", "source_id": f"osm:relation/{rel}", "tags": tags,
                  "retrieved_at": meta["retrieved_at"], "source_timestamp": meta.get("source_timestamp"),
                  "crs": "EPSG:4326", "license": cfg["osm_license"],
                  "confidence": "community-mapped administrative boundary; not an official cadastral line"}


def to_geojson(geom, info) -> dict:
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": info,
                                                        "geometry": mapping(geom)}]}
