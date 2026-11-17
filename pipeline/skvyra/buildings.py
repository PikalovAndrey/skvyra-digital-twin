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
FACADE = {"none": 0, "house": 1, "apartments": 2, "commercial": 3, "public": 4, "industrial": 5, "church": 6, "historic": 7,
          "tiled_arched": 8,  # tile-clad, rectangular ground-floor windows, tall upper windows with arched fanlights
          "tiled": 9,         # tile-clad, rectangular windows (photo-observed cladding, landmarks only)
          "unknown": 10,      # large building of unknown use: plain plaster, plain windows (no balconies, no shopfronts)
          "tiled_ribbon": 11}  # tile-clad pilasters, wide windows, brick-red spandrels (Soviet-era office block, photos)
ROOF_MAT = {"bitumen": 0, "metal_tile": 1, "slate": 2, "corrugated": 3, "seam": 4}
FLOOR_H = {"public": 3.8, "historic": 4.0, "apartments": 2.9, "commercial": 3.3, "church": 4.5, "tiled_arched": 5.5, "tiled": 4.2}
PALETTE = {  # sRGB hex with weights; tuned on licensed photos of Skvyra (docs/ARCHITECTURE_STYLE.md)
    "house_wall": [("#efece6", 6), ("#e8dcc0", 5), ("#ead79c", 2), ("#e6b99a", 2), ("#cfcac2", 3), ("#c3d2dc", 2),
                   ("#cdd8bd", 1), ("#b0553f", 1), ("#9c4f3c", 2), ("#dcd8cc", 3), ("#d8c3a5", 3)],
    "apartments_wall": [("#cfcbc2", 4), ("#ddd8cf", 3), ("#d9c9a3", 2), ("#c9b49a", 1)],
    "commercial_wall": [("#e9e4da", 3), ("#d7d2c8", 2), ("#e2cfb0", 2), ("#c9c3b6", 1)],
    "public_wall": [("#d9b48f", 3), ("#c8916a", 2), ("#e7dcc6", 3), ("#d6c7a8", 2)],
    "industrial_wall": [("#b9b6ad", 3), ("#a9a69e", 2), ("#c4bfb2", 2), ("#9d8f7f", 1)],
    "unknown_wall": [("#d6d2c9", 3), ("#cdc8bd", 3), ("#e0dace", 2), ("#c7c3bb", 2)],   # neutral plaster
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


PUBLIC_AMENITY = {"school", "hospital", "kindergarten", "clinic", "townhall", "post_office", "bank", "library",
                  "community_centre", "bus_station", "cinema", "theatre"}
COMMERCIAL_AMENITY = {"cafe", "restaurant", "pharmacy", "fuel", "fast_food", "bar"}
INDUSTRIAL_MAN_MADE = {"storage_tank", "silo", "works"}


def _by_land_use_and_size(area: float, cover: str | None) -> str:
    if area < 30:
        return "outbuilding"
    if cover in ("industrial", "farmland") and area > 150:
        return "industrial"
    if cover in ("institutional",) and area > 150:
        return "public"
    if cover in ("commercial",) and area > 150:
        return "commercial"
    return "unknown" if area > 600 else "house"


def classify(tags: dict, area: float, cover: str | None, poi: list | None = None) -> tuple[str, str, str]:
    """-> (kind, how, mass). how: 'tag' (OSM tags on the building), 'poi' (shop/amenity point inside), 'inferred'.

    kind (what the building is, i.e. its look): its tags first (building, amenity, shop, man_made, tourism, office),
    then a shop or amenity point inside it, then the OSM land use around it, then size. Size alone never makes a
    building industrial: a large untagged building outside industrial/farm land use is 'unknown' (neutral look,
    lowest confidence). Audit 2026-11-16: the museum and other central buildings were drawn as sheds.

    mass (whose default wall height and storeys apply): the type the model used before that fix. The function tags
    read since then (man_made, tourism, office, building=residential, amenity=car_wash) and the 'unknown' class
    change the look only; their default heights stay until a height or storey count is confirmed (owner's rule
    2026-11-16: no storey changes without data)."""
    b, am, tour, off = tags.get("building"), tags.get("amenity"), tags.get("tourism"), tags.get("office")
    fallback = ("commercial", "poi") if poi and area > 40 else (_by_land_use_and_size(area, cover), "inferred")
    old_mass = "industrial" if fallback[0] == "unknown" else fallback[0]
    if am == "place_of_worship" or b in KINDS["church"]:
        return "church", "tag", "church"
    if b == "residential":
        return "house", "tag", old_mass
    for kind, vals in KINDS.items():
        if b in vals:
            return kind, "tag", kind
    if am in PUBLIC_AMENITY:
        return "public", "tag", "public"
    if tags.get("shop") or am in COMMERCIAL_AMENITY:
        return "commercial", "tag", "commercial"
    if tags.get("man_made") in INDUSTRIAL_MAN_MADE:
        return "industrial", "tag", old_mass
    if tour in ("museum", "gallery") or off == "government":
        return "public", "tag", old_mass
    if tour in ("hotel", "guest_house", "hostel", "motel") or off or am == "car_wash":
        return "commercial", "tag", old_mass
    return (*fallback, old_mass)


def resolve(props: dict, area: float, aspect: float, fill: float, cover: str | None, cfg: dict,
            override: dict | None = None, ev: dict | None = None, landmark: dict | None = None) -> dict:
    """All attributes needed to build and describe one building, with provenance."""
    tags = props.get("tags", {})
    r = rng_for(props["id"], cfg["seed"])
    kind, kind_how, mass = classify(tags, area, cover, props.get("poi"))
    o, ev, lm = override or {}, ev or {}, landmark or {}
    if lm.get("match") == "probable":  # location uncertain: name only, no change of appearance
        lm = {}
    if lm.get("kind"):  # use seen on the landmark photos (e.g. a school annex, not a warehouse)
        kind, kind_how, mass = lm["kind"], "photo_estimate", lm["kind"]
    facade = lm.get("facade") or ("none" if kind == "outbuilding" else kind)
    # storey height follows the massing too, so a building whose look changed keeps its walls and storeys
    fh = lm.get("floor_h") or FLOOR_H.get(facade if mass == kind else mass, FLOOR_H.get(mass, cfg["floor_height_m"]))

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
        elif kind == "industrial":  # tanks and silos are round: flat top rather than a ridge
            roof = "flat" if area > 2000 or tags.get("man_made") in INDUSTRIAL_MAN_MADE else "gabled"
        elif kind == "unknown":  # neutral: low hipped roof (not the shed-like gable of a warehouse)
            roof = "flat" if area > 2000 else "hipped"
        elif kind == "public":
            roof = "hipped" if area < 1200 else wchoice(r, {"flat": 50, "hipped": 50})
        else:
            roof = "flat"
    pitch = {"outbuilding": r.uniform(12, 22), "house": r.uniform(24, 34), "church": 38.0, "commercial": r.uniform(18, 28),
             "public": r.uniform(18, 26), "industrial": r.uniform(8, 14), "apartments": 15.0}.get(mass, 20.0)
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
    elif kind == "unknown":
        roof_mat = wchoice(r, {"slate": 50, "seam": 50})
    else:
        roof_mat = wchoice(r, {"seam": 50, "metal_tile": 30, "slate": 20})
    roof_mat = lm.get("roof_material", roof_mat)

    # --- height ---------------------------------------------------------------------------------------
    levels = _num(tags.get("building:levels"))
    roof_levels = _num(tags.get("roof:levels"))
    tag_h, tag_rh = _num(tags.get("height")), _num(tags.get("roof:height"))
    W = math.sqrt(area / max(aspect, 1)) / 2  # approximate OBB half-width
    run = 2 * W if roof == "shed" else W
    roof_h = 0.0 if roof == "flat" else min(run * math.tan(math.radians(pitch)), ROOF_CAP.get(mass, 4.0))
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
    elif ev.get("anbh") and ev.get("share", 0) >= 0.6 and area >= 400 and mass in ("industrial", "public", "commercial", "church"):
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
        dl, de = defaults[mass]
        eave = de if de else dl * fh + plinth
        levels = levels or dl
        method = "type_default"

    if tags.get("man_made") in INDUSTRIAL_MAN_MADE:  # tanks and silos have no storeys (wall height stays)
        levels = None
    wall_key = {"house": "house_wall", "apartments": "apartments_wall", "public": "public_wall", "commercial": "commercial_wall",
                "industrial": "industrial_wall", "outbuilding": "outbuilding_wall", "church": "church_wall",
                "unknown": "unknown_wall"}[kind]
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
                         "industrial": 0.3, "unknown": 0.35}.get(kind, 0.0) if roof != "flat" else 0.12,
            "trim_color": srgb_to_linear(hex_rgb(r.choice(["#f2f0ea", "#ede8dc", "#5a3a2b", "#7a7a76"]))).tolist(),
            "door_color": srgb_to_linear(hex_rgb(r.choice(["#4a2f1e", "#5c3a22", "#3b3f45", "#6b6e70", "#7a4a2a"]))).tolist(),
            "chimney": kind == "house" and roof != "flat" and r.random() < 0.7, "rand": r.random(),
            "rand2": r.random(), "bay_m": lm.get("bay_m", 3.0), "window_base_m": lm.get("window_base_m", 0.0),
            "decompose": kind in ("house", "public", "commercial", "outbuilding", "church") and roof != "flat"
                         and o.get("roof_parts") != 1}  # override: one roof over the whole outline


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
        if small and shape != "flat" and not attrs.get("no_lean_to") and r.random() < 0.6:
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


def _multipart_eaves(p: Polygon, pieces, cuts, top_at, oh: float, z_ground: float):
    """Eave overhang of a multi-part roof, built wall by wall: the inner edge follows the wall top (so it meets the
    roof edge exactly), the outer edge follows the roof plane of the part that wall belongs to, never above the wall
    top and never more than 1.2 x the overhang below it. (The former single triangulation of the whole strip let one
    triangle span a 15 m wall with heights extrapolated from a neighbouring part: strips dropping down the walls.)
    -> (strip tris, strip uvs, (outer ring xy, outer ring z))."""
    ring = np.asarray(p.exterior.coords)[:-1]
    n_v = len(ring)
    edges = [(ring[i], ring[(i + 1) % n_v]) for i in range(n_v)]
    normals = []
    for a, b in edges:
        d = b - a
        ln = float(np.hypot(*d))
        normals.append(np.array([d[1], -d[0]]) / ln if ln > 1e-9 else np.zeros(2))  # outward for a CCW ring
    miter = []
    for i in range(n_v):  # offset corner shared by the edge ending here and the edge starting here
        n1, n2 = normals[i - 1], normals[i]
        m = (n1 + n2) / max(1.0 + float(n1 @ n2), 1e-6)
        ml = float(np.hypot(*m))
        miter.append(ring[i] + oh * (m if ml <= 2.0 else m / ml * 2.0))

    def owner(q):
        pt = shapely.Point(q)
        return min(pieces, key=lambda part: part.poly.distance(pt))

    walls = []
    for i, (a, b) in enumerate(edges):
        if float(np.hypot(*(b - a))) < 1e-4:
            walls.append(None)
            continue
        ts, _ = _split_ts(a, b, cuts)
        q = a + np.outer(ts, b - a)
        o = q + oh * normals[i]
        o[0], o[-1] = miter[i], miter[(i + 1) % n_v]
        zin = np.array([top_at(x) for x in q])
        zo, step = [], (b - a) * 1e-3
        for k in range(len(q)):  # a point where the owning part changes takes the lower side: no step in the strip
            sides = ([q[k] - step] if k > 0 else []) + ([q[k] + step] if k < len(q) - 1 else [])
            z = min(float(owner(x).z_ext(o[k])[0]) for x in sides)
            zo.append(float(np.clip(z, zin[k] - 1.2 * oh, zin[k])))
        walls.append([q, o, zin, np.array(zo)])
    live = [w for w in walls if w is not None]
    for w0, w1 in zip(live, live[1:] + live[:1]):  # a corner is shared by two strips: one height, no crack
        w0[3][-1] = w1[3][0] = min(w0[3][-1], w1[3][0])
    tris, uvs, out_xy, out_z = [], [], [], []
    for q, o, zin, zo in live:
        for k in range(len(q) - 1):
            A, B_ = (*q[k], zin[k] + z_ground), (*q[k + 1], zin[k + 1] + z_ground)
            C_, D = (*o[k + 1], zo[k + 1] + z_ground), (*o[k], zo[k] + z_ground)
            tris.append(np.array([[A, B_, C_], [A, C_, D]]))
            uvs.append(np.array([[q[k], q[k + 1], o[k + 1]], [q[k], o[k + 1], o[k]]]))
        out_xy += [x for x in o[:-1]]
        out_z += list(zo[:-1] + z_ground)
    if out_xy:
        out_xy.append(out_xy[0])
        out_z.append(out_z[0])
    return tris, uvs, (np.array(out_xy).reshape(-1, 2), np.array(out_z))


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

    touch = 1e-6 if len(pieces) == 1 else 0.02  # decomposed parts meet the outline only up to rounding (mm)

    def top_at(xy):
        """Roof height (relative) at a boundary point: highest piece touching it."""
        best = -1e9
        pt = shapely.Point(xy)
        for part in pieces:
            if part.poly.distance(pt) < touch:
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
                d0, d1 = dist + ts[k] * ln, dist + ts[k + 1] * ln
                bay = attrs.get("bay_m", 3.0)
                ua, ub = (d0 / bay, d1 / bay) if windows else (-1.0, -1.0)
                wb = attrs.get("window_base_m", 0.0)  # window rows start above a raised plinth / semi-basement
                v = lambda z: (z - z_ground - wb) / fh
                e = z_ground + eave_main
                if attrs.get("gable_windows", True) or max(t0, t1) <= e + 0.05:
                    A, B, Cc, D = (x0, y0, z_bot), (x1, y1, z_bot), (x1, y1, t1), (x0, y0, t0)
                    wall_tris += [[A, B, Cc], [A, Cc, D]]
                    wall_uv += [[(ua, v(z_bot)), (ub, v(z_bot)), (ub, v(t1))], [(ua, v(z_bot)), (ub, v(t1)), (ua, v(t0))]]
                else:  # plain gable: windows below the eave only
                    A, B, Cc, D = (x0, y0, z_bot), (x1, y1, z_bot), (x1, y1, e), (x0, y0, e)
                    wall_tris += [[A, B, Cc], [A, Cc, D], [D, Cc, (x1, y1, t1)], [D, (x1, y1, t1), (x0, y0, t0)]]
                    wall_uv += [[(ua, v(z_bot)), (ub, v(z_bot)), (ub, v(e))], [(ua, v(z_bot)), (ub, v(e)), (ua, v(e))]]
                    # u < -0.5 switches the facade shader's windows off; keeping it varying keeps the brick pattern
                    ga, gb = -1000.0 - d0 / bay, -1000.0 - d1 / bay
                    wall_uv += [[(ga, v(e)), (gb, v(e)), (gb, v(t1))], [(ga, v(e)), (gb, v(t1)), (ga, v(t0))]]
            dist += ln

    # ---- vertical faces between parts of different height (gable ends, wall above a lean-to) ----
    for i, pi in enumerate(pieces):
        for j, pj in enumerate(pieces):
            if i == j:
                continue
            # parts made by decompose() share edges only up to rounding: take i's boundary within 3 cm of j's
            shared = pi.poly.boundary.intersection(pj.poly.boundary.buffer(0.03, cap_style="flat"))
            for line in shapely.get_parts(shapely.line_merge(shared) if shared.geom_type == "MultiLineString" else shared):
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
        else:  # multi-part: one strip per wall, hanging from the wall top it overhangs
            st_tris, st_uv, outer_ring = _multipart_eaves(p, pieces, cuts, top_at, oh, z_ground)
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
            if single:
                ring = np.asarray(outer.exterior.coords) if outer.geom_type == "Polygon" else np.zeros((0, 2))
                zt = np.array([float(main.z_ext(q)[0]) for q in ring]) + z_ground if len(ring) else np.zeros(0)
            else:  # the outer edge of the per-wall strips
                ring, zt = outer_ring
            if len(ring):
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


def pediment_tris(base, u, n, e, f: dict, tym):
    """Gable crowning a facade, standing on the eave line e at `base` (2D, on the wall), u along the wall, n outward.
    f: width_m, rise_m, shape "triangle" (classical pediment) or "ogee" (curved baroque attic with a flat top),
    cornice_colour, oculus_r (round / arched window). Tympanum, raking cornice following the outline, base cornice.
    Sizes are photo estimates. -> (tris, colours)."""
    lin = lambda h: srgb_to_linear(hex_rgb(h)).tolist()
    w2, rise = f["width_m"] / 2, f["rise_m"]
    if f.get("shape") == "ogee":  # concave foot, convex shoulder, flat top a fifth of the width
        prof = [(-1.0, 0.0), (-0.85, 0.08), (-0.7, 0.3), (-0.55, 0.62), (-0.38, 0.86), (-0.2, 1.0),
                (0.2, 1.0), (0.38, 0.86), (0.55, 0.62), (0.7, 0.3), (0.85, 0.08), (1.0, 0.0)]
    else:
        prof = [(-1.0, 0.0), (0.0, 1.0), (1.0, 0.0)]
    cc = lin(f.get("cornice_colour", "#e8e2d8"))
    u3, n3 = np.array([*u, 0.0]), np.array([*n, 0.0])
    o3 = np.array([*(base + n * 0.06), e])
    P = [o3 + u3 * (a * w2) + np.array([0, 0, b * rise]) for a, b in prof]
    tris, cols = [], []
    for k in range(len(P) - 1):  # fan from the base centre (both outlines are star-shaped from there)
        tris.append([o3.tolist(), P[k + 1].tolist(), P[k].tolist()])                                       # faces n
        tris.append([(o3 - n3 * 0.04).tolist(), (P[k] - n3 * 0.04).tolist(), (P[k + 1] - n3 * 0.04).tolist()])  # back
        cols += [tym, tym]
    kd = 0.3  # cornice depth (m), projecting from the tympanum
    for p0, p1 in zip(P[:-1], P[1:]):
        d3 = p1 - p0
        if np.linalg.norm(d3) < 1e-6:
            continue
        d3 = d3 / np.linalg.norm(d3)
        nrm = np.cross(n3, d3)
        nrm = nrm if nrm[2] >= 0 else -nrm
        a0, a1, b0, b1 = p0 + nrm * 0.12, p1 + nrm * 0.12, p0 - nrm * 0.2, p1 - nrm * 0.2
        fo = n3 * kd
        tris += [[b0 + fo, b1 + fo, a1 + fo], [b0 + fo, a1 + fo, a0 + fo], [a0, a0 + fo, a1 + fo], [a0, a1 + fo, a1]]
        cols += [cc] * 4
    tris += box_tris(base + n * (0.06 + kd / 2), u, -n, w2 + 0.3, kd / 2, e - 0.25, e + 0.08)
    cols += [cc] * 10
    if f.get("oculus_r"):
        r_ = f["oculus_r"]
        c3 = o3 + n3 * 0.01 + np.array([0, 0, rise * f.get("oculus_at", 0.45)])
        up3 = np.array([0.0, 0.0, 1.0])
        for rr, colr, dz in ((r_ + 0.12, cc, 0.0), (r_, lin("#23272c"), 0.01)):
            c_ = c3 + n3 * dz
            pts = [c_ + rr * (math.cos(t) * u3 + math.sin(t) * up3) for t in np.linspace(0, 2 * math.pi, 15)]
            for q in range(14):
                tris.append([c_.tolist(), pts[q].tolist(), pts[q + 1].tolist()])
                cols.append(colr)
    return [np.asarray(t, float).tolist() for t in tris], cols


def wall_frame(p: Polygon, xy):
    """Nearest exterior wall to a point: (point on the wall, unit u along it, outward normal n)."""
    ring = np.asarray(shapely.orient_polygons(p, exterior_cw=False).exterior.coords)
    best = None
    for a, b in zip(ring[:-1], ring[1:]):
        d = b - a
        ln = float(np.hypot(*d))
        if ln < 1e-6:
            continue
        t = float(np.clip((np.asarray(xy) - a) @ d / ln ** 2, 0, 1))
        q = a + t * d
        dist = float(np.hypot(*(np.asarray(xy) - q)))
        if best is None or dist < best[0]:
            best = (dist, q, d / ln)
    _, q, u = best
    return q, u, np.array([u[1], -u[0]])


def facade_features(p: Polygon, feats, to_local, ground, eave: float, wall_rgb, lod: int):
    """Facade features of a plain (non-composite) landmark, placed on the wall nearest to "at_lonlat":
    pediment (triangle / ogee attic), band (horizontal band around the whole outline). -> (tris, colours)."""
    lin = lambda h: srgb_to_linear(hex_rgb(h)).tolist()
    ext = np.asarray(p.exterior.coords)
    z0 = float(np.mean(ground(ext[:, 0], ext[:, 1])))
    tris, cols = [], []
    for f in feats:
        if f["type"] == "pediment" and lod < 2:
            base, u, n = wall_frame(p, to_local(*f["at_lonlat"]))
            t, c = pediment_tris(base, u, n, z0 + eave, f, lin(f["colour"]) if f.get("colour") else list(wall_rgb))
            tris += t
            cols += c
        elif f["type"] == "band" and lod < 2:
            ring = np.asarray(shapely.segmentize(shapely.orient_polygons(p, exterior_cw=False).exterior, 2.0).coords)
            c = lin(f["colour"])
            za, zb = z0 + eave - f["below_eave_m"][0], z0 + eave - f["below_eave_m"][1]
            for a, b in zip(ring[:-1], ring[1:]):
                d = b - a
                ln = float(np.hypot(*d))
                if ln < 1e-6:
                    continue
                o = np.array([d[1], -d[0]]) / ln * f.get("proud_m", 0.06)
                A, B_ = a + o, b + o
                tris += [[(*A, za), (*B_, za), (*B_, zb)], [(*A, za), (*B_, zb), (*A, zb)]]
                cols += [c, c]
    return np.array(tris, float).reshape(-1, 3, 3), np.array(cols, float).reshape(-1, 3)


def frustum_tris(c, u, v, h0, h1, z0, z1):
    """Truncated pyramid (bottom half-sizes h0=(hu, hv), top h1) with outward faces and a top cap."""
    q = lambda hu, hv: [c - hu * u - hv * v, c + hu * u - hv * v, c + hu * u + hv * v, c - hu * u + hv * v]
    lo, hi = q(*h0), q(*h1)
    out = []
    for k in range(4):
        A, B, C, D = (*lo[k], z0), (*lo[(k + 1) % 4], z0), (*hi[(k + 1) % 4], z1), (*hi[k], z1)
        out += [[A, B, C], [A, C, D]]
    t = [(*p_, z1) for p_ in hi]
    return out + [[t[0], t[1], t[2]], [t[0], t[2], t[3]]]


def monument_mesh(cx, cy, z_ground, spec: dict, lod: int):
    """Free-standing monument from photos (config/landmarks.json "monuments"): a stack of blocks (steps, plinth,
    pillar) rising from the lowest ground point, optionally topped by a bust. Every size is a photo estimate.
    -> (tris, colours). Local coords; spec["facing_deg"] = azimuth the front faces (0 = north, 90 = east)."""
    az = math.radians(spec.get("facing_deg", 0.0))
    v = np.array([math.sin(az), math.cos(az)])        # towards the front
    u = np.array([v[1], -v[0]])                        # (u, v) as box_tris expects: faces point outward
    c = np.array([cx, cy])
    lin = lambda h: srgb_to_linear(hex_rgb(h)).tolist()
    tris, cols = [], []
    z = z_ground
    for b in spec["stack"]:
        t = box_tris(c, u, v, b["w"] / 2, b["d"] / 2, z - (0.4 if z == z_ground else 0.0), z + b["h"])
        tris += t
        cols += [lin(b["colour"])] * len(t)
        z += b["h"]
    bust = spec.get("bust")
    if bust and lod == 0:
        col = lin(bust.get("colour", "#3b3a36"))
        w, h = bust["w"], bust["h"]
        parts = [frustum_tris(c, u, v, (w / 2, w * 0.28), (w * 0.36, w * 0.22), z, z + h * 0.42),            # chest, shoulders
                 box_tris(c, u, v, w * 0.1, w * 0.1, z + h * 0.42, z + h * 0.52),                         # neck
                 frustum_tris(c, u, v, (w * 0.16, w * 0.18), (w * 0.17, w * 0.19), z + h * 0.52, z + h * 0.82),  # head
                 frustum_tris(c, u, v, (w * 0.18, w * 0.2), (w * 0.08, w * 0.1), z + h * 0.82, z + h)]          # cap / helmet
        for t in parts:
            tris += t
            cols += [col] * len(t)
    fig = spec.get("figure")  # standing statue as a simple silhouette (long coat, shoulders, head): no likeness
    if fig and lod < 2 and fig.get("rings"):
        # silhouette lofted through rings [z above the stack, extent to +u (the statue's right), extent to -u,
        # half depth], metres: the front outline is traced from a frontal photo, the depth is assumed
        col = lin(fig.get("colour", "#8a5a44"))
        n = 16 if lod == 0 else 8
        ts = np.linspace(0, 2 * math.pi, n + 1)
        rings = [[c + (math.cos(t) * (a if math.cos(t) >= 0 else b)) * u + math.sin(t) * d * v for t in ts]
                 for _, a, b, d in fig["rings"]]
        zs = [z + r[0] for r in fig["rings"]]
        for lo, hi, z0, z1 in zip(rings[:-1], rings[1:], zs[:-1], zs[1:]):
            for k in range(n):
                A, B_, C_, D = (*lo[k], z0), (*lo[k + 1], z0), (*hi[k + 1], z1), (*hi[k], z1)
                tris += [[A, B_, C_], [A, C_, D]]
                cols += [col, col]
        top = (*np.mean(rings[-1][:-1], axis=0), zs[-1])
        for k in range(n):
            tris.append([top, (*rings[-1][k], zs[-1]), (*rings[-1][k + 1], zs[-1])])
            cols.append(col)
    elif fig and lod < 2:
        col = lin(fig.get("colour", "#8a5a44"))
        w, h = fig["w"], fig["h"]
        parts = [frustum_tris(c, u, v, (w * 0.5, w * 0.3), (w * 0.48, w * 0.28), z, z + h * 0.55),                # coat
                 frustum_tris(c, u, v, (w * 0.48, w * 0.28), (w * 0.6, w * 0.32), z + h * 0.55, z + h * 0.8),      # cape
                 frustum_tris(c, u, v, (w * 0.62, w * 0.32), (w * 0.25, w * 0.2), z + h * 0.8, z + h * 0.84),      # shoulders
                 frustum_tris(c, u, v, (w * 0.18, w * 0.2), (w * 0.16, w * 0.18), z + h * 0.84, z + h)]            # head
        for t in parts:
            tris += t
            cols += [col] * len(t)
    return np.array(tris, float).reshape(-1, 3, 3), np.array(cols, float).reshape(-1, 3)


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


# ------------------------------------------------------------------------------------------------------
# composite landmarks: the real footprint cut into photo-measured blocks + facade details
# (config/landmarks.json "composite"; every dimension there is a photo estimate)
# ------------------------------------------------------------------------------------------------------
def composite_frame(p: Polygon, spec: dict):
    """Axis u along the long side (pointing to spec["axis_from"]'s opposite), outward front normal n, origin at the
    footprint's extreme along -u. -> (origin, u, n)."""
    c, u, v, L, W = obb(p)
    ends = {"west": np.array([-1.0, 0.0]), "east": np.array([1.0, 0.0]), "south": np.array([0.0, -1.0]), "north": np.array([0.0, 1.0])}
    if u @ ends[spec.get("axis_from", "west")] > 0:
        u = -u
    n = np.array([u[1], -u[0]])
    if n @ ends[spec.get("front", "south")] < 0:
        n = -n
    s0 = float((np.asarray(p.exterior.coords) @ u).min())
    return u * s0, u, n


def composite_parts(p: Polygon, spec: dict):
    """Cut the real footprint into blocks at the given distances along the axis. -> [(polygon, part spec)]."""
    _, u, n = composite_frame(p, spec)
    xy = np.asarray(p.exterior.coords)
    s_min, s_max = float((xy @ u).min()), float((xy @ u).max())
    t_min, t_max = float((xy @ n).min()) - 50, float((xy @ n).max()) + 50
    cuts = [s_min] + [s_min + d for d in spec["split_m"]] + [s_max]
    out = []
    for (a, b), ps in zip(zip(cuts[:-1], cuts[1:]), spec["parts"]):
        strip = Polygon([a * u + t_min * n, b * u + t_min * n, b * u + t_max * n, a * u + t_max * n])
        g = p.intersection(strip)
        g = max(shapely.get_parts(g), key=lambda q: q.area) if not g.is_empty else None
        if g is not None and g.area > 1.0:
            out.append((shapely.orient_polygons(g, exterior_cw=False), ps))
    return out


def composite_details(p: Polygon, spec: dict, ground, lod: int, wall_rgb):
    """Stone plinth and brick frieze bands around the outline, plus listed features (stepped parapet, gable window,
    stairs, doors). -> (tris (n,3,3), colours (n,3)). z from ground(x, y)."""
    s0u, u, n = composite_frame(p, spec)
    s_min = float(s0u @ u)
    parts = composite_parts(p, spec)
    lin = lambda h: srgb_to_linear(hex_rgb(h)).tolist()
    tris, cols = [], []

    def quad(a, b, z0a, z0b, z1a, z1b, col):
        A, B_ = (*a, z0a), (*b, z0b)
        tris.extend([[A, B_, (*b, z1b)], [A, (*b, z1b), (*a, z1a)]])
        cols.extend([col, col])

    def box(c2, hu, hv, z0, z1, col):
        t = box_tris(np.asarray(c2), u, -n, hu, hv, z0, z1)  # (u, -n) is counter-clockwise: faces point outward
        tris.extend(t)
        cols.extend([col] * len(t))

    ring = shapely.orient_polygons(p, exterior_cw=False)
    ext = np.asarray(ring.exterior.coords)
    zg = {id(ps): float(np.mean(ground(np.asarray(g.exterior.coords)[:, 0], np.asarray(g.exterior.coords)[:, 1]))) for g, ps in parts}
    z_bot = float(np.min(ground(ext[:, 0], ext[:, 1]))) - 0.5
    stone, frieze = lin(spec.get("plinth_colour", "#8e8b84")), (np.array(wall_rgb) * 0.72).tolist()
    seg = np.asarray(shapely.segmentize(ring.exterior, 1.0).coords)
    for a, b in zip(seg[:-1], seg[1:]):
        d = b - a
        ln = float(np.hypot(*d))
        if ln < 1e-6:
            continue
        out = np.array([d[1], -d[0]]) / ln  # outward for a CCW ring
        mid = shapely.Point((a + b) / 2 - out * 0.3)
        hit = [(g, ps) for g, ps in parts if g.buffer(0.5).contains(mid)]
        if not hit:
            continue
        g, ps = hit[0]
        z0 = zg[id(ps)]
        o = out * 0.04
        if ps.get("plinth_m"):
            quad(a + o, b + o, z_bot, z_bot, z0 + ps["plinth_m"], z0 + ps["plinth_m"], lin(ps["plinth_colour"]) if ps.get("plinth_colour") else stone)
        for z_a, z_b, c in ps.get("bands", []):  # painted / clad horizontal bands seen on the photos, metres above ground
            quad(a + out * 0.05, b + out * 0.05, z0 + z_a, z0 + z_a, z0 + z_b, z0 + z_b, lin(c))
        if lod < 2 and ps.get("frieze", True):
            e = z0 + ps["eave_m"]
            fc = lin(ps["frieze_colour"]) if ps.get("frieze_colour") else frieze
            quad(a + out * 0.08, b + out * 0.08, e - 0.55, e - 0.55, e - 0.1, e - 0.1, fc)
            if ps.get("frieze_colour"):  # a cornice that projects: top face (normal up) so it reads from above too
                tris.extend([[(*a, e - 0.1), (*(a + out * 0.3), e - 0.1), (*(b + out * 0.3), e - 0.1)],
                             [(*a, e - 0.1), (*(b + out * 0.3), e - 0.1), (*b, e - 0.1)]])
                cols.extend([fc, fc])
    for f in spec.get("features", []):
        g, ps = parts[f["part"]]
        z0 = zg[id(ps)]
        gxy = np.asarray(g.exterior.coords)
        face = float((gxy @ n).max())                       # the part's front plane
        at = s_min + f["at_m"]
        base = at * u + face * n
        if f["type"] == "stepped_parapet":
            e = z0 + ps["eave_m"]
            wall = lin(f["colour"]) if f.get("colour") else list(wall_rgb)
            for w_, top in zip(f["widths_m"], f["tops_m"]):  # widest first; tops above the eave
                box(base - n * 0.2, w_ / 2, 0.2, e - 0.6, e + top, wall)
            nz0, nz1 = e + f["tops_m"][-1] - 1.5, e + f["tops_m"][-1] - 0.25
            box(base + n * 0.02, 0.38, 0.02, nz0, nz1, lin("#3a2a22"))   # niche with the tablets
        elif f["type"] == "gable_window":
            zc = z0 + ps["eave_m"] + f["height_above_eave_m"]
            r = f.get("radius_m", 0.5)
            c3 = np.array([*(base + n * 0.03), zc])
            up = np.array([0.0, 0.0, 1.0])
            uu = np.array([*u, 0.0])
            ring_pts = [c3 + r * (math.cos(t) * uu + math.sin(t) * up) for t in np.linspace(0, 2 * math.pi, 13)]
            for k in range(12):
                tris.append([c3.tolist(), ring_pts[k].tolist(), ring_pts[k + 1].tolist()])
                cols.append(lin("#1f2328"))
            if f.get("star"):  # hexagram lattice, two triangles in front of the glass
                for rot in (math.pi / 2, -math.pi / 2):
                    a3 = [rot + k * 2 * math.pi / 3 for k in range(3)]
                    pts = [c3 + np.array([*(n * 0.01), 0.0]) + 0.8 * r * (math.cos(t) * uu + math.sin(t) * up) for t in a3]
                    tris.append([q.tolist() for q in pts])
                    cols.append(lin("#e9e6df"))
        elif f["type"] == "pediment":
            tym = lin(f.get("colour") or ps.get("wall_colour")) if (f.get("colour") or ps.get("wall_colour")) else list(wall_rgb)
            pt, pc = pediment_tris(base, u, n, z0 + ps["eave_m"], f, tym)
            tris.extend(pt)
            cols.extend(pc)
        elif f["type"] == "door":
            z_a = z0 + f.get("sill_m", 0.0)
            box(base + n * 0.02, f.get("width_m", 1.2) / 2, 0.03, z_a, z_a + f.get("height_m", 2.3), lin(f.get("colour", "#5a3b26")))
        elif f["type"] == "canopy":  # flat entrance canopy: a slab out from the front wall, optional corner posts
            hw, hd, z1 = f["width_m"] / 2, f["depth_m"] / 2, z0 + f["height_m"]
            za, col = z1 - f.get("thick_m", 0.3), lin(f.get("colour", "#e6e3dc"))
            c2 = base + n * hd
            box(c2, hw, hd, za, z1, col)
            q = [c2 - hw * u + hd * n, c2 + hw * u + hd * n, c2 + hw * u - hd * n, c2 - hw * u - hd * n]
            tris.extend([[(*q[0], za), (*q[2], za), (*q[1], za)], [(*q[0], za), (*q[3], za), (*q[2], za)]])  # underside
            cols.extend([col, col])
            if f.get("posts"):
                for s_ in (-1, 1):
                    box(c2 + n * (hd - 0.2) + s_ * u * (hw - 0.2), 0.15, 0.15, z_bot, za, col)
        elif f["type"] == "stair":
            # stone stair along the facade: rises from at_m to at_m + dir*length_m up to height_m, landing at the top
            length, h, w_, d = f["length_m"], f["height_m"], f.get("width_m", 1.5), f.get("dir", 1)
            steps = max(3, int(h / 0.18))
            for k in range(steps):
                s_ = at + d * (k + 0.5) * length / steps
                box(s_ * u + (face + w_ / 2) * n, length / steps / 2, w_ / 2, z_bot, z0 + (k + 1) * h / steps, stone)
            land = at + d * (length + f.get("landing_m", 1.6) / 2)
            box(land * u + (face + w_ / 2) * n, f.get("landing_m", 1.6) / 2, w_ / 2, z_bot, z0 + h, stone)
    return np.array(tris, float).reshape(-1, 3, 3), np.array(cols, float).reshape(-1, 3)
