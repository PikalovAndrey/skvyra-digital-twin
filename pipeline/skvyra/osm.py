"""Overpass `out geom` JSON -> shapely geometries (WGS84), with explicit failure reasons."""
import shapely
from shapely.geometry import LineString, Point, Polygon

AREA_KEYS = {"building", "building:part", "landuse", "leisure", "amenity", "area:highway", "man_made", "place"}
NATURAL_LINEAR = {"coastline", "tree_row", "cliff", "ridge", "arete", "valley"}
WATERWAY_AREAS = {"riverbank", "dock", "boatyard", "dam"}
MAN_MADE_LINEAR = {"goods_conveyor", "pipeline", "embankment", "dyke", "breakwater", "groyne", "cutline", "pier"}


def is_area_way(tags: dict) -> bool:
    if tags.get("area") == "no":
        return False
    if tags.get("area") == "yes":
        return True
    if "highway" in tags or "barrier" in tags or "railway" in tags:
        return False
    if "natural" in tags:
        return tags["natural"] not in NATURAL_LINEAR
    if "waterway" in tags:
        return tags["waterway"] in WATERWAY_AREAS
    if tags.get("man_made") in MAN_MADE_LINEAR:
        return False
    return any(k in tags for k in AREA_KEYS)


def coords(geom: list) -> tuple[list, int]:
    """Return (lon, lat) list and the number of missing node references (null entries)."""
    out, missing = [], 0
    for p in geom or []:
        if p is None:
            missing += 1
        else:
            out.append((p["lon"], p["lat"]))
    return out, missing


def way_geometry(el: dict):
    """-> (geometry or None, problem string or None)."""
    pts, missing = coords(el.get("geometry"))
    if missing:
        return None, f"missing_nodes:{missing}"
    if len(pts) < 2:
        return None, "too_few_nodes"
    closed = pts[0] == pts[-1]
    if is_area_way(el.get("tags", {})):
        if not closed:
            return None, "unclosed_ring"
        if len(pts) < 4:
            return None, "degenerate_ring"
        return Polygon(pts), None
    return LineString(pts), None


def relation_geometry(el: dict):
    """Assemble a multipolygon relation from its member ways (rings may be split over many ways)."""
    outer, inner, missing = [], [], 0
    for m in el.get("members", []):
        if m.get("type") != "way":
            continue
        pts, miss = coords(m.get("geometry"))
        missing += miss
        if len(pts) >= 2:
            (inner if m.get("role") == "inner" else outer).append(LineString(pts))
    if missing:
        return None, f"missing_nodes:{missing}"
    if not outer:
        return None, "no_outer_members"
    shells = shapely.polygonize(shapely.node(shapely.union_all(outer)).geoms)
    if shells.is_empty:
        return None, "outer_rings_do_not_close"
    geom = shapely.union_all(list(shells.geoms))
    if inner:
        holes = shapely.polygonize(shapely.node(shapely.union_all(inner)).geoms)
        if not holes.is_empty:
            geom = geom.difference(shapely.union_all(list(holes.geoms)))
    return geom, None


def element_geometry(el: dict):
    t = el["type"]
    if t == "node":
        return Point(el["lon"], el["lat"]), None
    if t == "way":
        return way_geometry(el)
    if el.get("tags", {}).get("type") in ("multipolygon", "boundary", None) or "building" in el.get("tags", {}):
        return relation_geometry(el)
    if el.get("tags", {}).get("type") in ("waterway", "route", "site", "associatedStreet", "street"):
        return None, "skip:grouping_relation"  # members are fetched as ways already
    return None, "unsupported_relation_type"


def source_id(el: dict) -> str:
    return f"osm:{el['type']}/{el['id']}"
