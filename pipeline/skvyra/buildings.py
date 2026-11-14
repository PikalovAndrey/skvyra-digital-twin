"""Building attributes (height / roof inference with provenance) and mesh generation.

Height hierarchy (`height_method`, with `height_conf`):
  override        user-verified value (data/overrides)                          high
  source_height   OSM `height`                                                   high
  source_levels   OSM `building:levels` x floor height (+ plinth)                medium
  photo_estimate  storeys read from a licensed photo of a listed landmark        medium
  ghsl_dominant   GHSL ANBH of a cell this building dominates (see heights.py)   low
  ghsl_neighbourhood  tagged apartment block, levels from GHSL 3x3 maximum       low
  type_default    documented default for the (tagged or inferred) type           lowest
Roof (`roof_method`): override > source_tag > photo_estimate > inferred (rules below, deterministic per id).

Roof geometry: every roof is the minimum of a few planes over the footprint's oriented bounding
rectangle (gabled = 2 planes, hipped = 4, half-hipped = 4 with raised end planes, pyramidal = 4, shed = 1).
The region where each plane is the lowest is found by half-plane clipping, intersected with the footprint
and triangulated, so concave footprints and courtyards need no special cases. Complex rectilinear
footprints (L/T/U shapes, extensions) are first split into rectangular parts, each with its own
orientation and roof; extensions get lean-to (shed) roofs against the main body or their own gables, and
the vertical faces between parts of different height are generated explicitly.
"""
import hashlib
import math
import random

import numpy as np
import shapely
from shapely.geometry import Polygon

from .mesh import hex_rgb, srgb_to_linear

SUPPORTED_ROOFS = {"flat", "gabled", "hipped", "half-hipped", "pyramidal", "shed"}
ROOF_ALIASES = {"skillion": "shed", "half_hipped": "half-hipped", "side_hipped": "half-hipped", "mansard": "hipped",
                "gambrel": "gabled", "saltbox": "gabled", "dome": "pyramidal", "onion": "pyramidal", "round": "gabled",
                "gabled_row": "gabled", "cone": "pyramidal"}
KINDS = {
    "apartments": {"apartments", "dormitory"},
    "church": {"church", "chapel", "cathedral", "temple", "religious", "synagogue", "mosque"},
    "industrial": {"industrial", "warehouse", "hangar", "factory", "manufacture", "farm_auxiliary", "barn",
                   "cowshed", "greenhouse", "service", "storage_tank", "silo", "transformer_tower", "construction"},
    "commercial": {"commercial", "retail", "supermarket", "kiosk", "shop", "office", "hotel"},
    "public": {"school", "hospital", "kindergarten", "university", "college", "public", "civic", "government",
               "train_station", "sports_hall", "transportation"},
    "outbuilding": {"garage", "garages", "shed", "hut", "roof", "carport", "toilets"},
    "house": {"house", "detached", "semidetached_house", "bungalow", "terrace", "farm"},
}
FACADE = {"none": 0, "house": 1, "apartments": 2, "commercial": 3, "public": 4, "industrial": 5, "church": 6, "historic": 7}
ROOF_MAT = {"bitumen": 0, "metal_tile": 1, "slate": 2, "corrugated": 3, "seam": 4}
FLOOR_H = {"public": 3.8, "historic": 4.0, "apartments": 2.9, "commercial": 3.3, "church": 4.5}
PALETTE = {  # sRGB hex with weights; tuned on licensed photos of Skvyra (docs/ARCHITECTURE_STYLE.md)
    "house_wall": [("#efece6", 6), ("#e8dcc0", 5), ("#ead79c", 2), ("#e6b99a", 2), ("#cfcac2", 3), ("#c3d2dc", 2),
                   ("#cdd8bd", 1), ("#b0553f", 1), ("#9c4f3c", 2), ("#dcd8cc", 3), ("#d8c3a5", 3)],
    "apartments_wall": [("#cfcbc2", 4), ("#ddd8cf", 3), ("#d9c9a3", 2), ("#c9b49a", 1)],
    "commercial_wall": [("#e9e4da", 3), ("#d7d2c8", 2), ("#e2cfb0", 2), ("#c9c3b6", 1)],
    "public_wall": [("#d9b48f", 3), ("#c8916a", 2), ("#e7dcc6", 3), ("#d6c7a8", 2)],
    "industrial_wall": [("#b9b6ad", 3), ("#a9a69e", 2), ("#c4bfb2", 2), ("#9d8f7f", 1)],
    "outbuilding_wall": [("#a9a39a", 3), ("#8a6f5a", 2), ("#c2bbb0", 2), ("#7e7a73", 1), ("#9c4f3c", 1)],
    "church_wall": [("#efede6", 1)],
    "roof_slate": [("#8d8f8c", 3), ("#a2a39e", 2), ("#7a7c7a", 2)],
    "roof_metal_tile": [("#7d2b24", 5), ("#5a3427", 4), ("#3f5b45", 2), ("#2f4f74", 1), ("#6e2430", 3), ("#454a4f", 2)],
    "roof_seam": [("#9da2a4", 3), ("#6e7377", 2), ("#5a3a2e", 1)],
    "roof_corrugated": [("#a3a7a8", 3), ("#8a8f90", 2), ("#b8b9b3", 1)],
    "roof_bitumen": [("#575757", 3), ("#626160", 2), ("#4e4f50", 1)],
}
# maximum roof rise (m) by type: wide plans get a flatter pitch instead of an ever taller roof
ROOF_CAP = {"house": 3.2, "outbuilding": 2.0, "commercial": 3.5, "public": 4.5, "industrial": 3.5, "apartments": 3.0,
            "church": 9.0}
CONF = {"override": "high", "source_height": "high", "photo_estimate": "medium", "source_levels": "medium",
        "ghsl_dominant": "low", "ghsl_neighbourhood": "low", "type_default": "lowest"}


def rng_for(bid: str, seed: int) -> random.Random:
    return random.Random(int(hashlib.sha1(f"{seed}:{bid}".encode()).hexdigest()[:12], 16))


def pick(r: random.Random, key: str):
    items = PALETTE[key]
    return srgb_to_linear(hex_rgb(r.choices([c for c, _ in items], [w for _, w in items])[0])).tolist()


def wchoice(r: random.Random, options: dict):
    return r.choices(list(options), list(options.values()))[0]


def _num(v):
    try:
        x = float(str(v).split()[0].replace(",", ".").rstrip("m"))
        return x if math.isfinite(x) and x > 0 else None
    except (ValueError, IndexError):
        return None


def obb(p: Polygon):
    """-> centre, long-axis unit u, short-axis unit v, half-length L, half-width W."""
    rect = np.asarray(p.minimum_rotated_rectangle.exterior.coords)[:4]
    e0, e1 = rect[1] - rect[0], rect[2] - rect[1]
    if np.linalg.norm(e0) < np.linalg.norm(e1):
        e0, e1 = e1, e0
    L, W = np.linalg.norm(e0) / 2, np.linalg.norm(e1) / 2
    u = e0 / max(2 * L, 1e-9)
    v = np.array([-u[1], u[0]])
    return rect.mean(axis=0), u, v, max(L, 1e-3), max(W, 1e-3)


def classify(tags: dict, area: float, cover: str | None, poi: list | None = None) -> tuple[str, str]:
    """-> (kind, how). how: 'tag' (OSM building/amenity tag), 'poi' (shop/amenity point inside), 'inferred'."""
    b = tags.get("building")
    if tags.get("amenity") == "place_of_worship" or b in KINDS["church"]:
        return "church", "tag"
    for kind, vals in KINDS.items():
        if b in vals:
            return kind, "tag"
    if tags.get("amenity") in ("school", "hospital", "kindergarten", "clinic", "townhall", "post_office", "bank",
                               "library", "community_centre", "bus_station", "cinema", "theatre"):
        return "public", "tag"
    if tags.get("shop") or tags.get("amenity") in ("cafe", "restaurant", "pharmacy", "fuel", "fast_food", "bar"):
        return "commercial", "tag"
    if poi and area > 40:
        return "commercial", "poi"
    if area < 30:
        return "outbuilding", "inferred"
    if cover in ("industrial", "farmland") and area > 150:
        return "industrial", "inferred"
    if cover in ("institutional",) and area > 150:
        return "public", "inferred"
    if cover in ("commercial",) and area > 150:
        return "commercial", "inferred"
    if area > 600:
        return "industrial", "inferred"
    return "house", "inferred"


def resolve(props: dict, area: float, aspect: float, fill: float, cover: str | None, cfg: dict,
            override: dict | None = None, ev: dict | None = None, landmark: dict | None = None) -> dict:
    """All attributes needed to build and describe one building, with provenance."""
    tags = props.get("tags", {})
    r = rng_for(props["id"], cfg["seed"])
    kind, kind_how = classify(tags, area, cover, props.get("poi"))
    o, ev, lm = override or {}, ev or {}, landmark or {}
    if lm.get("match") == "probable":  # location uncertain: name only, no change of appearance
        lm = {}
    facade = lm.get("facade") or ("none" if kind == "outbuilding" else kind)
    fh = FLOOR_H.get(facade, FLOOR_H.get(kind, cfg["floor_height_m"]))

    # --- roof shape ---------------------------------------------------------------------------------
    roof, roof_method = None, "inferred"
    if o.get("roof_shape"):
        roof, roof_method = o["roof_shape"], "override"
    elif tags.get("roof:shape"):
        t = ROOF_ALIASES.get(tags["roof:shape"], tags["roof:shape"])
        if t in SUPPORTED_ROOFS:
            roof, roof_method = t, "source_tag"
    elif lm.get("roof_shape"):
        roof, roof_method = lm["roof_shape"], "photo_estimate"
    if roof is None:
        if kind == "outbuilding":
            roof = wchoice(r, {"shed": 45, "gabled": 45, "flat": 10})
        elif kind in ("house", "commercial") and (kind == "house" or area < 300):
            if aspect > 2.2:
                roof = wchoice(r, {"gabled": 70, "half-hipped": 30})
            elif aspect < 1.25 and area < 160:
                roof = wchoice(r, {"hipped": 65, "pyramidal": 35})
            else:
                roof = wchoice(r, {"hipped": 50, "gabled": 30, "half-hipped": 20})
        elif kind == "church":
            roof = "gabled"
        elif kind == "industrial":
            roof = "flat" if area > 2000 else "gabled"
        elif kind == "public":
            roof = "hipped" if area < 1200 else wchoice(r, {"flat": 50, "hipped": 50})
        else:
            roof = "flat"
    pitch = {"outbuilding": r.uniform(12, 22), "house": r.uniform(24, 34), "church": 38.0, "commercial": r.uniform(18, 28),
             "public": r.uniform(18, 26), "industrial": r.uniform(8, 14), "apartments": 15.0}.get(kind, 20.0)
    if roof == "shed":
        pitch = min(pitch, 18.0)

    # --- roof material --------------------------------------------------------------------------------
    if roof == "flat":
        roof_mat = "bitumen"
    elif kind == "house" or kind == "commercial":
        roof_mat = wchoice(r, {"metal_tile": 50, "slate": 35, "seam": 15})
    elif kind == "outbuilding":
        roof_mat = wchoice(r, {"slate": 45, "corrugated": 35, "metal_tile": 20})
    elif kind == "industrial":
        roof_mat = "corrugated"
    else:
        roof_mat = wchoice(r, {"seam": 50, "metal_tile": 30, "slate": 20})
    roof_mat = lm.get("roof_material", roof_mat)

    # --- height ---------------------------------------------------------------------------------------
    levels = _num(tags.get("building:levels"))
    roof_levels = _num(tags.get("roof:levels"))
    tag_h, tag_rh = _num(tags.get("height")), _num(tags.get("roof:height"))
    W = math.sqrt(area / max(aspect, 1)) / 2  # approximate OBB half-width
    run = 2 * W if roof == "shed" else W
    roof_h = 0.0 if roof == "flat" else min(run * math.tan(math.radians(pitch)), ROOF_CAP.get(kind, 4.0))
    if tag_rh:
        roof_h = tag_rh
    elif roof_levels and roof != "flat":
        roof_h = roof_levels * fh * 0.8
    plinth = 0.4
    if o.get("height"):
        eave, method = max(float(o["height"]) - roof_h, 2.0), "override"
    elif tag_h:
        eave, method = max(tag_h - roof_h, 2.0), "source_height"
    elif o.get("levels"):
        levels, method = float(o["levels"]), "override"
        eave = levels * fh + plinth
    elif levels:
        eave, method = levels * fh + plinth, "source_levels"
    elif lm.get("levels"):
        levels, method = float(lm["levels"]), "photo_estimate"
        eave = levels * fh + plinth
    elif kind == "apartments" and ev.get("nbr_max"):
        levels = float(min(9, max(3, round(ev["nbr_max"] / 3.0))))
        eave, method = levels * fh + plinth, "ghsl_neighbourhood"
    elif ev.get("anbh") and ev.get("share", 0) >= 0.6 and area >= 400 and kind in ("industrial", "public", "commercial", "church"):
        # large non-residential building that dominates its cell: the cell value is mostly this building
        # (houses excluded: their 1-2 storey difference is within the product error)
        # ANBH ~ mean height of the roof surface: eave = ANBH - half the roof rise
        eave = float(np.clip(ev["anbh"] - roof_h / 2, 2.5, 40.0))
        levels = float(max(1, round((eave - plinth) / fh)))
        method = "ghsl_dominant"
    # NOTE: GHSL is *not* used to choose 1 vs 2 storeys for small houses. Audit 2026-11-12: with the
    # product's stated error (RMSE 3.55 m) a 7 m threshold flags 20-29 % of truly one-storey cells, the
    # result swings from 8 % to 42 % two-storey for thresholds 8..6 m, and flagged houses cluster within
    # 100 m of large buildings (spill-over of the 100 m model). See docs/AUDIT_REPORT.md.
    else:
        defaults = {"outbuilding": (None, 2.4), "house": (2 if area >= 220 else 1, None), "apartments": (5, None),
                    "public": (2, None), "commercial": (1 if area < 250 else 2, None),
                    "industrial": (None, 6.0 if area > 500 else 4.5), "church": (None, 8.0)}
        dl, de = defaults[kind]
        eave = de if de else dl * fh + plinth
        levels = levels or dl
        method = "type_default"

    wall_key = {"house": "house_wall", "apartments": "apartments_wall", "public": "public_wall", "commercial": "commercial_wall",
                "industrial": "industrial_wall", "outbuilding": "outbuilding_wall", "church": "church_wall"}[kind]
    wall_c = pick(r, wall_key)
    roof_c = pick(r, f"roof_{roof_mat}")
    for src in (lm, o):  # landmark photo estimate, then user override, win over the palette
        if src.get("wall_colour"):
            wall_c = srgb_to_linear(hex_rgb(src["wall_colour"])).tolist()
        if src.get("roof_colour"):
            roof_c = srgb_to_linear(hex_rgb(src["roof_colour"])).tolist()
    return {"kind": kind, "kind_method": kind_how, "roof": roof, "roof_method": roof_method,
            "roof_height": round(roof_h, 2), "eave_height": round(eave, 2),
            "height": round(eave + roof_h, 2), "height_method": method, "height_conf": CONF[method],
            "levels": levels, "floor_h": fh, "wall_color": wall_c, "roof_color": roof_c, "pitch": round(pitch, 1),
            "facade": facade, "roof_mat": roof_mat,
            "overhang": {"house": 0.45, "commercial": 0.4, "public": 0.5, "church": 0.5, "outbuilding": 0.25,
                         "industrial": 0.3}.get(kind, 0.0) if roof != "flat" else 0.12,
            "trim_color": srgb_to_linear(hex_rgb(r.choice(["#f2f0ea", "#ede8dc", "#5a3a2b", "#7a7a76"]))).tolist(),
            "door_color": srgb_to_linear(hex_rgb(r.choice(["#4a2f1e", "#5c3a22", "#3b3f45", "#6b6e70", "#7a4a2a"]))).tolist(),
            "chimney": kind == "house" and roof != "flat" and r.random() < 0.7, "rand": r.random(),
            "rand2": r.random(), "decompose": kind in ("house", "public", "commercial", "outbuilding", "church") and roof != "flat"}


# ------------------------------------------------------------------------------------------------------
# roof planes and regions
# ------------------------------------------------------------------------------------------------------

def planes_for(shape: str, L: float, W: float, slope_dir=(0.0, 1.0)) -> list[tuple[float, float, float]]:
    """Roof as min over planes f(s, t) = a*s + b*t + c (0 at eave, 1 at ridge) in OBB coordinates."""
    if shape == "flat":
        return []
    if shape == "shed":  # rises towards slope_dir: (0, 1) = towards +t, (1, 0) = towards +s
        ds, dt = slope_dir
        half = L if ds else W
        return [(ds / (2 * half), dt / (2 * half), 0.5)]
    sides = [(0.0, -1 / W, 1.0), (0.0, 1 / W, 1.0)]
    if shape == "gabled":
        return sides
    if shape == "hipped":
        return sides + [(-1 / W, 0.0, L / W), (1 / W, 0.0, L / W)]
    if shape == "half-hipped":  # gable walls up to 55 % of the rise, hipped above
        return sides + [(-1 / W, 0.0, 0.55 + L / W), (1 / W, 0.0, 0.55 + L / W)]
    if shape == "pyramidal":
        return sides + [(-1 / L, 0.0, 1.0), (1 / L, 0.0, 1.0)]
    raise ValueError(f"unsupported roof {shape}")


def clip_halfplane(pts, A, B, C):
    """Sutherland-Hodgman: keep A*s + B*t + C <= 0 of a convex polygon (list of (s, t))."""
    out = []
    n = len(pts)
    for i in range(n):
        p, q = pts[i], pts[(i + 1) % n]
        fp, fq = A * p[0] + B * p[1] + C, A * q[0] + B * q[1] + C
        if fp <= 0:
            out.append(p)
        if (fp < 0 < fq) or (fq < 0 < fp):
            k = fp / (fp - fq)
            out.append((p[0] + k * (q[0] - p[0]), p[1] + k * (q[1] - p[1])))
    return out


def plane_regions(planes, L, W, m=1.0):
    """[(polygon in (s,t), plane)] where each plane is the lowest (roof surface) - convex regions."""
    box = [(-L - m, -W - m), (L + m, -W - m), (L + m, W + m), (-L - m, W + m)]
    if not planes:
        return [(Polygon(box), (0.0, 0.0, 0.0))]
    out = []
    for i, (a, b, c) in enumerate(planes):
        poly = box
        for j, (a2, b2, c2) in enumerate(planes):
            if i != j and poly:
                poly = clip_halfplane(poly, a - a2, b - b2, c - c2)
        if len(poly) >= 3:
            pg = Polygon(poly)
            if pg.area > 1e-8:
                out.append((pg, (a, b, c)))
    return out


class Part:
    """One rectangular-ish piece of a building with its own roof."""

    def __init__(self, poly, shape, eave, pitch, cap, slope_dir=(0.0, 1.0), rh=None):
        self.poly = poly
        self.c, self.u, self.v, self.L, self.W = obb(poly)
        if self.W < 1.2 and shape != "flat":
            shape = "flat"
        self.shape, self.eave = shape, eave
        run = (2 * (self.L if slope_dir[0] else self.W)) if shape == "shed" else self.W
        self.rh = 0.0 if shape == "flat" else (rh if rh is not None else min(run * math.tan(math.radians(pitch)), cap))
        self.planes = planes_for(shape, self.L, self.W, slope_dir)

    def st(self, xy):
        d = np.atleast_2d(xy) - self.c
        return d @ self.u, d @ self.v

    def frac(self, xy):
        if not self.planes:
            return np.zeros(len(np.atleast_2d(xy)))
        s, t = self.st(xy)
        return np.clip(np.min([a * s + b * t + c for a, b, c in self.planes], axis=0), 0, 1)

    def z(self, xy):
        return self.eave + self.rh * self.frac(xy)

    def z_ext(self, xy):
        """Roof surface continued *outside* the walls (eaves overhang): planes are not clipped below 0."""
        if not self.planes:
            return np.full(len(np.atleast_2d(xy)), self.eave)
        s, t = self.st(xy)
        return self.eave + self.rh * np.minimum(np.min([a * s + b * t + c for a, b, c in self.planes], axis=0), 1.0)

    def world_regions(self):
        out = []
        for reg, plane in plane_regions(self.planes, self.L, self.W):
            ring = np.asarray(reg.exterior.coords)
            out.append((Polygon(self.c + ring[:, :1] * self.u + ring[:, 1:2] * self.v), plane))
        return out


# ------------------------------------------------------------------------------------------------------
# footprint decomposition
# ------------------------------------------------------------------------------------------------------

def _cluster(vals, tol):
    out = []
    for v in sorted(vals):
        if not out or v - out[-1][-1] > tol:
            out.append([v])
        else:
            out[-1].append(v)
    return [sum(g) / len(g) for g in out]


def decompose(p: Polygon, max_parts: int = 4):
    """Split a rectilinear-ish footprint into rectangles (OBB-aligned). -> (parts, filler) or None."""
    if p.interiors:
        return None
    c, u, v, L, W = obb(p)
    if p.area / (4 * L * W) > 0.92:
        return None
    to_st = lambda xy: np.column_stack([(xy - c) @ u, (xy - c) @ v])
    q = shapely.transform(p, to_st).simplify(0.4)
    if q.geom_type != "Polygon" or q.is_empty:
        return None
    xs = _cluster([x for x, _ in q.exterior.coords], 0.6)
    ys = _cluster([y for _, y in q.exterior.coords], 0.6)
    if len(xs) > 12 or len(ys) > 12 or len(xs) < 2 or len(ys) < 2:
        return None
    nx, ny = len(xs) - 1, len(ys) - 1
    inside = np.zeros((nx, ny), bool)
    areas = np.zeros((nx, ny))
    for i in range(nx):
        for j in range(ny):
            cell = shapely.box(xs[i], ys[j], xs[i + 1], ys[j + 1])
            areas[i, j] = cell.area
            inside[i, j] = cell.area > 0 and q.intersection(cell).area >= 0.75 * cell.area
    if areas[inside].sum() < 0.85 * q.area:
        return None  # not rectilinear enough (diagonal walls): keep a single roof
    rects = []
    free = inside.copy()
    while free.any() and len(rects) < max_parts:
        best, best_a = None, 0.0
        for i0 in range(nx):
            for i1 in range(i0, nx):
                for j0 in range(ny):
                    for j1 in range(j0, ny):
                        if free[i0:i1 + 1, j0:j1 + 1].all():
                            a = areas[i0:i1 + 1, j0:j1 + 1].sum()
                            if a > best_a:
                                best, best_a = (i0, i1, j0, j1), a
        if best is None:
            break
        i0, i1, j0, j1 = best
        free[i0:i1 + 1, j0:j1 + 1] = False
        r = shapely.box(xs[i0], ys[j0], xs[i1 + 1], ys[j1 + 1])
        if r.area >= 10 and min(xs[i1 + 1] - xs[i0], ys[j1 + 1] - ys[j0]) >= 2.4:
            rects.append(r)
    if len(rects) < 2:
        return None
    back = lambda st: c + st[:, :1] * u + st[:, 1:2] * v
    parts = []
    for r in rects:
        g = shapely.transform(r, back).intersection(p)
        g = max(shapely.get_parts(g), key=lambda x: x.area) if not g.is_empty else g
        if g.geom_type == "Polygon" and g.area >= 8:
            parts.append(g)
    if len(parts) < 2:
        return None
    filler = p.difference(shapely.union_all(parts))
    filler = [f for f in shapely.get_parts(filler) if f.geom_type == "Polygon" and f.area > 0.3]
    return parts, filler


def plan(p: Polygon, attrs: dict):
    """Roof parts for a footprint: [Part], filler polygons (flat at main eave)."""
    shape, eave, pitch = attrs["roof"], attrs["eave_height"], attrs.get("pitch") or 25.0
    cap = ROOF_CAP.get(attrs["kind"], 4.0)
    dec = decompose(p) if attrs.get("decompose") else None
    if dec is None:
        rh = attrs["roof_height"] if "roof_height" in attrs else None
        return [Part(p, shape, eave, pitch, cap, rh=rh)], []
    polys, filler = dec
    polys.sort(key=lambda g: -g.area)
    main = Part(polys[0], shape, eave, pitch, cap)
    parts = [main]
    r = random.Random(f"{attrs.get('rand', 0)}")
    for g in polys[1:]:
        small = g.area < 0.4 * main.poly.area
        pc, pu, pv, pL, pW = obb(g)
        d = main.c - pc
        ds, dt = d @ pu / pL, d @ pv / pW
        if small and shape != "flat" and r.random() < 0.6:
            # lean-to extension rising towards the main body, its high side just under the main eave
            slope = (math.copysign(1.0, ds), 0.0) if abs(ds) > abs(dt) else (0.0, math.copysign(1.0, dt))
            probe = Part(g, "shed", eave, min(pitch, 16.0), 2.2, slope)
            parts.append(Part(g, "shed", max(2.2, eave - probe.rh * 0.95), min(pitch, 16.0), 2.2, slope))
        else:
            sub = "gabled" if shape in ("hipped", "pyramidal", "half-hipped") and small else shape
            parts.append(Part(g, sub, eave, pitch, cap))
    return parts, filler


# ------------------------------------------------------------------------------------------------------
# meshing
# ------------------------------------------------------------------------------------------------------

def triangulate(poly) -> np.ndarray:
    """Constrained Delaunay triangulation of a (multi)polygon with holes -> (n,3,2)."""
    out = []
    for p in shapely.get_parts(poly):
        if p.geom_type != "Polygon" or p.area < 1e-6:
            continue
        tri = shapely.constrained_delaunay_triangles(p)
        for t in shapely.get_parts(tri):
            out.append(np.asarray(t.exterior.coords)[:3])
    return np.array(out).reshape(-1, 3, 2)


def up_facing(tris3: np.ndarray) -> np.ndarray:
    n = np.cross(tris3[:, 1] - tris3[:, 0], tris3[:, 2] - tris3[:, 0])
    flip = n[:, 2] < 0
    tris3[flip] = tris3[flip][:, ::-1]
    return tris3


def _facet_uv(flat_xy, part: Part, plane):
    """UV in metres: u along the eave, v up the slope (true length) - for tile rows / seams in shaders."""
    a, b, _ = plane
    g = (a * part.u + b * part.v) * part.rh  # gradient of z in world xy
    n = float(np.hypot(*g))
    if n < 1e-9:
        return flat_xy.copy()
    d = g / n
    perp = np.array([-d[1], d[0]])
    return np.column_stack([flat_xy @ perp, (flat_xy @ d) * math.sqrt(1 + n * n)])


def _split_ts(a, b, cuts):
    seg = b - a
    ln = float(np.hypot(*seg))
    ts = [0.0, 1.0]
    if cuts is not None and ln > 1e-9:
        inter = shapely.intersection(shapely.LineString([a, b]), cuts)
        for q in shapely.get_coordinates(inter):
            ts.append(float(np.clip((q - a) @ seg / ln ** 2, 0, 1)))
    return sorted(set(round(t, 9) for t in ts)), ln


def building_mesh(p: Polygon, attrs: dict, ground, floor_h: float, lod: int, parts=None):
    """-> dict(walls=(tris, uvs), roof=(tris, uvs), extra=tris, z_ground) in local coords. ground(x, y) -> z."""
    p = shapely.orient_polygons(p, exterior_cw=False)  # exterior CCW, holes CW => walls face outward
    if lod == 2:
        p = p.simplify(0.5, preserve_topology=True)
        if p.is_empty or p.geom_type != "Polygon":
            return None
    ext = np.asarray(p.exterior.coords)
    gz = ground(ext[:, 0], ext[:, 1])
    z_ground = float(np.mean(gz))
    z_bot = float(np.min(gz)) - 0.5  # foundation sinks into the lowest point: never floats on slopes
    fh = attrs.get("floor_h", floor_h)
    windows = attrs.get("facade", attrs["kind"]) not in ("none", "industrial") and attrs["kind"] not in ("outbuilding", "industrial")

    if lod == 2:  # overview prism: flat top at mid-roof
        parts, filler = [Part(p, "flat", attrs["eave_height"] + attrs["roof_height"] * 0.5, 0, 0)], []
    elif parts is None:
        parts, filler = plan(p, attrs)
    else:
        parts, filler = parts
    eave_main = parts[0].eave
    pieces = list(parts) + [Part(f, "flat", eave_main, 0, 0) for f in filler]

    # ---- roofs ----
    roof_tris, roof_uv, region_lines = [], [], []
    for part in pieces:
        for reg, plane in part.world_regions():
            region_lines.append(reg.exterior)
            piece = part.poly.intersection(reg)
            if piece.is_empty:
                continue
            t2 = triangulate(piece)
            if not len(t2):
                continue
            flat = t2.reshape(-1, 2)
            a, b, c = plane
            s, t = part.st(flat)
            z = z_ground + part.eave + part.rh * np.clip(a * s + b * t + c, 0, 1) if part.planes else np.full(len(flat), z_ground + part.eave)
            tri3 = np.concatenate([flat, z[:, None]], 1).reshape(-1, 3, 3)
            uv = _facet_uv(flat, part, plane).reshape(-1, 3, 2)
            n = np.cross(tri3[:, 1] - tri3[:, 0], tri3[:, 2] - tri3[:, 0])
            flip = n[:, 2] < 0
            tri3[flip], uv[flip] = tri3[flip][:, ::-1], uv[flip][:, ::-1]
            roof_tris.append(tri3)
            roof_uv.append(uv)
        region_lines.append(part.poly.exterior)
    cuts = shapely.union_all(region_lines) if region_lines else None

    def top_at(xy):
        """Roof height (relative) at a boundary point: highest piece touching it."""
        best = -1e9
        pt = shapely.Point(xy)
        for part in pieces:
            if part.poly.distance(pt) < 1e-6:
                best = max(best, float(part.z(xy)[0]))
        return best if best > -1e9 else eave_main

    # ---- exterior walls ----
    wall_tris, wall_uv = [], []
    for ring in [p.exterior, *p.interiors]:
        pts = np.asarray(ring.coords)
        dist = 0.0
        for a, b in zip(pts[:-1], pts[1:]):
            ts, ln = _split_ts(a, b, cuts)
            if ln < 1e-4:
                continue
            q = a + np.outer(ts, b - a)
            zt = [top_at(x) + z_ground for x in q]
            for k in range(len(q) - 1):
                (x0, y0), (x1, y1) = q[k], q[k + 1]
                t0, t1 = zt[k], zt[k + 1]
                A, B, Cc, D = (x0, y0, z_bot), (x1, y1, z_bot), (x1, y1, t1), (x0, y0, t0)
                d0, d1 = dist + ts[k] * ln, dist + ts[k + 1] * ln
                ua, ub = (d0 / 3.0, d1 / 3.0) if windows else (-1.0, -1.0)
                va, vb, vd = (z_bot - z_ground) / fh, (t1 - z_ground) / fh, (t0 - z_ground) / fh
                wall_tris += [[A, B, Cc], [A, Cc, D]]
                wall_uv += [[(ua, va), (ub, va), (ub, vb)], [(ua, va), (ub, vb), (ua, vd)]]
            dist += ln

    # ---- vertical faces between parts of different height (gable ends, wall above a lean-to) ----
    for i, pi in enumerate(pieces):
        for j, pj in enumerate(pieces):
            if i == j:
                continue
            shared = pi.poly.boundary.intersection(pj.poly.boundary)
            for line in shapely.get_parts(shared):
                if line.geom_type != "LineString" or line.length < 0.05:
                    continue
                pts = np.asarray(line.coords)
                for a, b in zip(pts[:-1], pts[1:]):
                    ts, ln = _split_ts(a, b, cuts)
                    q = a + np.outer(ts, b - a)
                    zi = pi.z(q)
                    zj = pj.z(q)
                    for k in range(len(q) - 1):
                        h0, h1 = zi[k] - zj[k], zi[k + 1] - zj[k + 1]
                        if h0 <= 0.02 and h1 <= 0.02:
                            continue
                        segs = [(0.0, 1.0)]
                        if (h0 > 0) != (h1 > 0):  # crossing: keep only the part where i is higher
                            x = h0 / (h0 - h1)
                            segs = [(0.0, x)] if h0 > 0 else [(x, 1.0)]
                        for s0, s1 in segs:
                            P0, P1 = q[k] + s0 * (q[k + 1] - q[k]), q[k] + s1 * (q[k + 1] - q[k])
                            lo0, hi0 = float(pj.z(P0)[0]), float(pi.z(P0)[0])
                            lo1, hi1 = float(pj.z(P1)[0]), float(pi.z(P1)[0])
                            A, B = (*P0, lo0 + z_ground), (*P1, lo1 + z_ground)
                            Cc, D = (*P1, hi1 + z_ground), (*P0, hi0 + z_ground)
                            tri = np.array([[A, B, Cc], [A, Cc, D]])
                            n = np.cross(tri[0, 1] - tri[0, 0], tri[0, 2] - tri[0, 0])
                            mid = (np.asarray(P0) + np.asarray(P1)) / 2
                            if np.hypot(*n[:2]) > 1e-9 and pi.poly.contains(shapely.Point(mid + n[:2] / np.hypot(*n[:2]) * 0.05)):
                                tri = tri[:, ::-1]  # face away from part i
                            wall_tris += tri.tolist()
                            wall_uv += [[(-1, -1)] * 3] * 2

    extra, extra_col = [], []
    main = parts[0]
    oh = attrs.get("overhang", 0.0) if lod < 2 else 0.0
    if oh > 0 and p.area > 12:
        outer = p.buffer(oh, join_style="mitre", mitre_limit=2.0)
        strip = outer.difference(p)
        single = len(pieces) == 1
        st_tris, st_uv = [], []
        if single:  # exact: split the strip by roof-plane regions so every piece lies in one plane
            for reg, plane in main.world_regions():
                piece = strip.intersection(reg)
                if piece.is_empty:
                    continue
                t2 = triangulate(piece)
                if not len(t2):
                    continue
                flat = t2.reshape(-1, 2)
                a, b, c = plane
                s_, t_ = main.st(flat)
                f = np.minimum(a * s_ + b * t_ + c, 1.0) if main.planes else np.zeros(len(flat))
                z = z_ground + main.eave + main.rh * f
                st_tris.append(np.concatenate([flat, z[:, None]], 1).reshape(-1, 3, 3))
                st_uv.append(_facet_uv(flat, main, plane).reshape(-1, 3, 2))
        else:  # multi-part: each strip triangle follows the roof of the nearest part
            t2 = triangulate(strip)
            for tri in t2:
                cpt = shapely.Point(tri.mean(axis=0))
                part = min(pieces, key=lambda pt: pt.poly.distance(cpt))
                z = z_ground + part.z_ext(tri)
                st_tris.append(np.concatenate([tri, z[:, None]], 1).reshape(1, 3, 3))
                st_uv.append(tri.reshape(1, 3, 2).copy())
        if st_tris:
            top = np.concatenate(st_tris)
            uv_top = np.concatenate(st_uv)
            n = np.cross(top[:, 1] - top[:, 0], top[:, 2] - top[:, 0])
            flip = n[:, 2] < 0
            top[flip], uv_top[flip] = top[flip][:, ::-1], uv_top[flip][:, ::-1]
            roof_tris.append(top)
            roof_uv.append(uv_top)
        if st_tris and lod == 0:  # soffit + fascia only close up: invisible from LOD1 distances
            fascia = 0.14  # no separate soffit: the viewer renders roofs double-sided (underside visible)
            # fascia board along the outer edge (heights from the overhang surface)
            ring = np.asarray(outer.exterior.coords) if outer.geom_type == "Polygon" else np.zeros((0, 2))
            if len(ring):
                zt = np.array([float(min(pieces, key=lambda pt: pt.poly.distance(shapely.Point(q))).z_ext(q)[0]) for q in ring]) + z_ground
                for k in range(len(ring) - 1):
                    (x0, y0), (x1, y1) = ring[k], ring[k + 1]
                    A, B = (x0, y0, zt[k] - fascia), (x1, y1, zt[k + 1] - fascia)
                    Cc, D = (x1, y1, zt[k + 1]), (x0, y0, zt[k])
                    extra += [[A, B, Cc], [A, Cc, D]]
                    extra_col += [attrs.get("trim_color", [0.8, 0.8, 0.78])] * 2
    door = attrs.get("door")
    if lod == 0 and door is not None:
        (ax, ay), (bx, by), tpos = door
        d = np.array([bx - ax, by - ay])
        ln = float(np.hypot(*d))
        # distance of this edge's start along the exterior ring (same walk as the wall UVs)
        ring_pts = np.asarray(p.exterior.coords)
        cum, start = 0.0, None
        for qa, qb in zip(ring_pts[:-1], ring_pts[1:]):
            if np.allclose(qa, (ax, ay), atol=1e-6) and np.allclose(qb, (bx, by), atol=1e-6):
                start = cum
                break
            cum += float(np.hypot(*(qb - qa)))
        if ln > 2.2 and start is not None:
            u_ = d / ln
            nrm = np.array([u_[1], -u_[0]])  # outward for a CCW exterior ring
            # put the door on a boundary between 3 m window bays, never on top of a window
            along = np.clip(tpos * ln, 1.1, ln - 1.1)
            k = np.round((start + along) / 3.0) * 3.0 - start
            along = k if 0.6 <= k <= ln - 0.6 else along
            cpos = np.array([ax, ay]) + u_ * along + nrm * 0.03
            gz0 = float(ground(cpos[0], cpos[1])) + 0.25
            w2, hgt = 0.5, 2.1
            P0, P1 = cpos - u_ * w2, cpos + u_ * w2
            A, B, Cc, D = (*P0, gz0), (*P1, gz0), (*P1, gz0 + hgt), (*P0, gz0 + hgt)
            extra += [[A, B, Cc], [A, Cc, D]]
            extra_col += [attrs.get("door_color", [0.18, 0.12, 0.08])] * 2
    if lod == 0 and attrs.get("chimney") and main.shape in ("gabled", "hipped", "half-hipped") and main.L > 2.5:
        s0 = (attrs["rand"] - 0.5) * main.L
        base = main.c + s0 * main.u + 0.25 * main.W * main.v
        if main.poly.contains(shapely.Point(base)):
            top = float(main.z(base)[0]) + z_ground + 0.9
            ch = box_tris(base, main.u, main.v, 0.25, 0.25, top - 2.0, top)
            extra += ch
            extra_col += [[0.32, 0.17, 0.12]] * len(ch)
    rt = np.concatenate(roof_tris) if roof_tris else np.zeros((0, 3, 3))
    ru = np.concatenate(roof_uv) if roof_uv else np.zeros((0, 3, 2))
    wt, wu = np.array(wall_tris).reshape(-1, 3, 3), np.array(wall_uv, float).reshape(-1, 3, 2)
    keep = np.linalg.norm(np.cross(wt[:, 1] - wt[:, 0], wt[:, 2] - wt[:, 0]), axis=1) > 1e-8  # zero-height slivers
    wt, wu = wt[keep], wu[keep]
    return {"walls": (wt, wu),
            "roof": (rt, ru), "extra": np.array(extra).reshape(-1, 3, 3), "extra_col": np.array(extra_col).reshape(-1, 3),
            "z_ground": z_ground,
            "ridge": max(float(pt.eave + pt.rh) for pt in parts)}


def box_tris(c, u, v, hu, hv, z0, z1):
    corners = [c - hu * u - hv * v, c + hu * u - hv * v, c + hu * u + hv * v, c - hu * u + hv * v]
    out = []
    for k in range(4):
        a, b = corners[k], corners[(k + 1) % 4]
        A, B, C, D = (*a, z0), (*b, z0), (*b, z1), (*a, z1)
        out += [[A, B, C], [A, C, D]]
    t = [(*q, z1) for q in corners]
    out += [[t[0], t[1], t[2]], [t[0], t[2], t[3]]]
    return out


def rotunda_mesh(cx, cy, z_bot, z_ground, spec: dict, eave: float, floor_h: float, n: int = 32):
    """Domed corner tower of a landmark (photo-estimated, see config/landmarks.json "features"): cylinder wall
    centred on the footprint corner (three quarters stand outside the building), set-back drum with round
    windows (oculi) and a ribbed dome. -> (wall tris, wall uvs, detail tris, detail colours). Local coords.
    Wall UVs follow the facade convention (u = bays of 3 m along the wall, v = storeys) so the facade shader
    draws the same windows as on the wings."""
    R = spec["diameter_m"] / 2
    top = z_ground + eave + spec.get("wall_above_eave_m", 0.6)
    rd = spec.get("drum_diameter_m", 0.85 * 2 * R) / 2
    d_top = top + spec.get("drum_h_m", 1.2)
    rise = spec.get("dome_rise_m", 0.35 * 2 * rd)
    a = np.linspace(0, 2 * math.pi, n + 1)
    ring = lambda r, z: np.column_stack([cx + r * np.cos(a), cy + r * np.sin(a), np.full(n + 1, z)])
    walls, uvs, det, col = [], [], [], []

    def band(r0, z0, r1, z1, c=None, uv=False):
        p, q = ring(r0, z0), ring(r1, z1)
        for k in range(n):
            A, B, Cc, D = p[k], p[k + 1], q[k + 1], q[k]
            if uv:
                u0, u1 = k * (2 * math.pi * R / n) / 3.0, (k + 1) * (2 * math.pi * R / n) / 3.0
                v0, v1 = (z0 - z_ground) / floor_h, (z1 - z_ground) / floor_h
                walls.extend([[A, B, Cc], [A, Cc, D]])
                uvs.extend([[(u0, v0), (u1, v0), (u1, v1)], [(u0, v0), (u1, v1), (u0, v1)]])
            else:
                det.extend([[A, B, Cc], [A, Cc, D]])
                cc = c(k) if callable(c) else c
                col.extend([cc, cc])

    cornice = srgb_to_linear(hex_rgb(spec.get("cornice_colour", "#e9e4da"))).tolist()
    drum_c = srgb_to_linear(hex_rgb(spec.get("drum_colour", "#e6e2d8"))).tolist()
    dome_a = np.array(srgb_to_linear(hex_rgb(spec.get("dome_colour", "#8e979c"))))
    band(R, z_bot, R, top, uv=True)                                   # tower wall (facade shader: windows)
    band(R + 0.25, top - 0.35, R + 0.25, top, cornice)                # cornice band
    band(R + 0.25, top, rd, top, cornice)                             # flat roof ring between wall and drum
    band(R, top - 0.35, R + 0.25, top - 0.35, cornice)                # cornice soffit (faces down)
    band(rd, top, rd, d_top, drum_c)                                  # drum
    band(rd + 0.12, d_top, rd + 0.12, d_top + 0.18, cornice)          # drum cornice
    band(rd, d_top, rd + 0.12, d_top, cornice)
    rib = lambda k: (dome_a * (1.12 if k % 2 else 0.9)).tolist()      # metal sheet panels read as ribs
    steps = 6
    rb, zb = rd + 0.12, d_top + 0.18
    for s in range(steps):  # dome: quarter-ellipse profile
        t0, t1 = s / steps * math.pi / 2, (s + 1) / steps * math.pi / 2
        band(rb * math.cos(t0), zb + rise * math.sin(t0), max(rb * math.cos(t1), 0.02), zb + rise * math.sin(t1), rib)
    # oculi: dark discs just outside the drum
    k_oc = spec.get("oculi", 8)
    zc, ro = (top + d_top) / 2, min(0.32, (d_top - top) * 0.32)
    dark = srgb_to_linear(hex_rgb("#2b2e33")).tolist()
    for j in range(k_oc):
        ang = 2 * math.pi * (j + 0.5) / k_oc
        c0 = np.array([cx + (rd + 0.03) * math.cos(ang), cy + (rd + 0.03) * math.sin(ang), zc])
        t = np.array([-math.sin(ang), math.cos(ang), 0.0])
        up = np.array([0.0, 0.0, 1.0])
        pts = [c0 + ro * (math.cos(b) * t + math.sin(b) * up) for b in np.linspace(0, 2 * math.pi, 9)]
        for k in range(8):
            det.append([c0.tolist(), pts[k].tolist(), pts[k + 1].tolist()])
            col.append(dark)
    return np.array(walls), np.array(uvs, float), np.array(det), np.array(col)
