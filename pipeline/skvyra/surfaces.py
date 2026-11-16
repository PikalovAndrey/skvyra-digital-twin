"""Road / water / rail surfaces, exact terrain draping, ground textures and vegetation placement."""
import hashlib
import io
import math
import random

import numpy as np
import shapely
from shapely.geometry import box

from .buildings import triangulate

# width (m) per highway class; `width` / `lanes` tags win. Values are typical Ukrainian town cross-sections
# and are *inferred* unless the way carries a width tag.
ROAD_WIDTH = {"motorway": 14, "trunk": 11, "primary": 9, "secondary": 7.5, "tertiary": 6.5, "unclassified": 5.5,
              "residential": 5.0, "living_street": 4.5, "service": 3.5, "track": 3.0, "pedestrian": 5.0,
              "footway": 1.8, "path": 1.4, "cycleway": 2.0, "steps": 2.0, "bridleway": 2.0, "construction": 5.0,
              "primary_link": 6, "secondary_link": 6, "tertiary_link": 5, "road": 5.0, "corridor": 1.5}
UNPAVED = {"unpaved", "dirt", "ground", "gravel", "fine_gravel", "compacted", "sand", "grass", "earth", "mud", "pebblestone"}
FOOT = {"footway", "path", "pedestrian", "steps", "cycleway", "bridleway", "corridor"}
COVER_PRIORITY = ["stadium", "farmland", "residential", "commercial", "industrial", "institutional", "orchard", "grass",
                  "cemetery", "park", "track", "pitch", "court", "scrub", "forest", "wetland", "paved", "water"]
GROUND = {  # sRGB, used for the ground texture (renderer applies its own lighting)
    "base": (148, 158, 112), "farmland": (196, 186, 128), "residential": (142, 152, 104), "commercial": (170, 166, 150),
    "industrial": (160, 156, 140), "institutional": (150, 158, 118), "orchard": (118, 140, 82), "grass": (128, 156, 88),
    "cemetery": (112, 132, 92), "park": (106, 146, 78), "pitch": (98, 150, 80), "scrub": (112, 128, 78),
    "track": (165, 148, 131), "court": (150, 148, 142),  # unpaved ring / hard court when no photo gives the colour
    "forest": (70, 98, 58), "wetland": (108, 128, 96), "paved": (150, 148, 142), "water": (62, 92, 110),
    "road": (92, 92, 94), "road_unpaved": (150, 130, 100), "foot": (170, 162, 150), "rail": (110, 100, 92),
    "shoulder": (138, 126, 104), "wet_bank": (74, 78, 52),
}


def road_width(p: dict) -> tuple[float, bool]:
    """-> (width m, inferred?)"""
    try:
        if p.get("width"):
            return max(1.0, float(str(p["width"]).split()[0].replace(",", "."))), False
    except ValueError:
        pass
    w = ROAD_WIDTH.get(p.get("highway"), 4.0)
    try:
        if p.get("lanes") and p["highway"] not in FOOT:
            w = max(w, float(p["lanes"]) * 3.25)
    except ValueError:
        pass
    return w, True


def road_category(p: dict) -> str:
    if p.get("highway") in FOOT:
        return "foot"
    if p.get("surface") in UNPAVED or (p.get("highway") == "track" and not p.get("surface")):
        return "unpaved"
    return "paved"


def road_polygons(roads_local: list):
    """roads_local: [(LineString, props)] -> {category: polygon}, bridges [(poly, line, props)].
    Buffering then union per category gives coherent junctions instead of overlapping strips."""
    groups = {"paved": [], "unpaved": [], "foot": []}
    bridges = []
    for line, p in roads_local:
        if p.get("tunnel") in ("yes", "building_passage", "culvert") or p.get("highway") in ("proposed", "abandoned", "razed"):
            continue
        w, _ = road_width(p)
        poly = line.buffer(w / 2, cap_style="flat" if p.get("bridge") else "round", join_style="round", quad_segs=4)
        if p.get("bridge") and p.get("bridge") != "no":
            bridges.append((poly, line, p))
        else:
            groups[road_category(p)].append(poly)
    out = {k: shapely.union_all(v) for k, v in groups.items() if v}
    if "paved" in out and "unpaved" in out:
        out["unpaved"] = out["unpaved"].difference(out["paved"])
    for k in ("paved", "unpaved"):
        if "foot" in out and k in out:
            out["foot"] = out["foot"].difference(out[k])
    return out, bridges


def clip(g, x0, y0, x1, y1):
    """clip_by_rect that survives GEOS degenerate output on thin slivers (falls back to an exact intersection)."""
    try:
        return shapely.clip_by_rect(g, x0, y0, x1, y1)
    except shapely.errors.GEOSException:
        g = shapely.make_valid(g)
        g = shapely.union_all([q for q in shapely.get_parts(g) if q.geom_type in ("Polygon", "MultiPolygon")])
        return g.intersection(box(x0, y0, x1, y1))


def drape(poly, terrain, bounds, offset: float) -> np.ndarray:
    """Split `poly` along the terrain lattice (cells and their diagonals) so each piece lies in one planar
    terrain triangle, triangulate, and lift onto the surface + offset. -> (n,3,3)."""
    x0, y0, x1, y1 = bounds
    poly = clip(poly, x0, y0, x1, y1)
    if poly.is_empty:
        return np.zeros((0, 3, 3))
    s = terrain.step if hasattr(terrain, "step") else (x1 - x0)
    out = []
    blk = s * 8
    for bx in np.arange(x0, x1, blk):
        for by in np.arange(y0, y1, blk):
            piece = clip(poly, bx, by, min(bx + blk, x1), min(by + blk, y1))
            if piece.is_empty:
                continue
            ci0, ci1 = int(math.floor(bx / s)), int(math.ceil(min(bx + blk, x1) / s))
            cj0, cj1 = int(math.floor(by / s)), int(math.ceil(min(by + blk, y1) / s))
            for i in range(ci0, ci1):
                for j in range(cj0, cj1):
                    cell = clip(piece, i * s, j * s, (i + 1) * s, (j + 1) * s)
                    if cell.is_empty or cell.area < 1e-4:
                        continue
                    a, b, c, d = (i * s, j * s), ((i + 1) * s, j * s), ((i + 1) * s, (j + 1) * s), (i * s, (j + 1) * s)
                    for tri in (shapely.Polygon([a, b, c]), shapely.Polygon([a, c, d])):
                        try:
                            part = cell.intersection(tri)
                        except shapely.errors.GEOSException:  # robustness: repair, then intersect
                            part = shapely.make_valid(cell).intersection(tri)
                        if part.is_empty or part.area < 1e-4:
                            continue
                        t2 = triangulate(part)
                        if len(t2):
                            out.append(t2)
    if not out:
        return np.zeros((0, 3, 3))
    t2 = np.concatenate(out).reshape(-1, 2)
    z = terrain.z(t2[:, 0], t2[:, 1]) + offset
    tris = np.concatenate([t2, z[:, None]], 1).reshape(-1, 3, 3)
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    flip = n[:, 2] < 0
    tris[flip] = tris[flip][:, ::-1]
    return tris


def drape_thin(poly, terrain, bounds, offset: float, seg: float = 4.0) -> np.ndarray:
    """Cheap draping for narrow strips (shoulders): densify the outline every `seg` m, triangulate once and
    lift every vertex onto the terrain. Far fewer triangles than lattice splitting; with 1 m wide strips the
    deviation from the terrain triangles stays below the overlay offset."""
    poly = clip(poly, *bounds)
    if poly.is_empty:
        return np.zeros((0, 3, 3))
    poly = shapely.segmentize(poly, seg)
    t2 = triangulate(poly)
    if not len(t2):
        return np.zeros((0, 3, 3))
    flat = t2.reshape(-1, 2)
    z = terrain.z(flat[:, 0], flat[:, 1]) + offset
    tris = np.concatenate([flat, z[:, None]], 1).reshape(-1, 3, 3)
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    flip = n[:, 2] < 0
    tris[flip] = tris[flip][:, ::-1]
    return tris


def _facing(tris, d):
    """Wind each triangle so its normal points along `d` (one direction per triangle, or one for all)."""
    tris = np.asarray(tris, float).reshape(-1, 3, 3)
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    d = np.asarray(d, float)
    if d.ndim == 2 and len(d) != len(n):  # one direction per quad (2 triangles each)
        d = np.repeat(d, len(n) // len(d), 0)
    flip = (n * np.broadcast_to(d, n.shape)).sum(1) < 0
    tris[flip] = tris[flip][:, ::-1]
    return tris


def _quads(A, B, C, D):
    """Strips of quads A[k] B[k] C[k] D[k] (arrays (n,3)) -> (2n,3,3)."""
    return np.stack([np.stack([A, B, C], 1), np.stack([A, C, D], 1)], 1).reshape(-1, 3, 3)


def bridge_mesh(line, p: dict, terrain, offset: float, seg: float = 4.0):
    """Bridge on an OSM `bridge=*` way: deck (-> the road material of its category) and structure (fascia,
    underside, parapets or railings -> "bridge" material). -> (deck (n,3,3), structure (m,3,3)).
    Heights: the DEM under bridges does not resolve the valley, so the deck is laid on the line between the
    road levels at both ends (exactly the draped road height there: no step at the joints), never below the
    ground, plus a smooth sin^2 rise (0 at the ends, 0.2-0.6 m mid-span) that keeps it clear of the water
    surface. Width = road_width (inferred unless tagged). Parapets/railings are generic, not surveyed."""
    w, _ = road_width(p)
    foot = p.get("highway") in FOOT
    road_off = offset + (0.02 if road_category(p) == "paved" else 0)  # same lift as the draped road it joins
    thick, rail_h = (0.3, 1.0) if foot else (0.6, 0.9)
    pts = np.asarray(shapely.segmentize(line, seg).coords)[:, :2]
    if len(pts) < 2 or line.length < 0.5:
        return np.zeros((0, 3, 3)), np.zeros((0, 3, 3))
    d = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    t = d / d[-1]
    tan = np.gradient(pts, axis=0)
    tan /= np.linalg.norm(tan, axis=1)[:, None]
    nrm = np.column_stack([-tan[:, 1], tan[:, 0]])  # left of the direction of travel
    rise = np.clip(0.02 * d[-1], 0.2, 0.6) * np.sin(np.pi * t) ** 2

    def edge(o):  # points offset `o` m to the left, deck top z there
        e = pts + nrm * o
        g = terrain.z(e[:, 0], e[:, 1]) + road_off
        z = np.maximum(g[0] + (g[-1] - g[0]) * t, g) + rise
        return np.column_stack([e, z])

    L, R = edge(w / 2), edge(-w / 2)
    down = np.array([0, 0, thick])
    deck = _facing(_quads(L[:-1], R[:-1], R[1:], L[1:]), [0, 0, 1])
    struct = [_facing(_quads(L[:-1], R[:-1], R[1:], L[1:]) - down, [0, 0, -1])]  # underside
    up = np.array([0, 0, 0 if foot else rail_h])
    for E, s in ((L, 1), (R, -1)):  # fascia (+ outer parapet face): from the underside to the parapet top
        out = np.column_stack([nrm * s, np.zeros(len(E))])
        struct.append(_facing(_quads(E[:-1] - down, E[1:] - down, E[1:] + up, E[:-1] + up), out[:-1]))
    if foot:  # railing: posts every ~2 m and a hand rail, both sides
        from .buildings import box_tris
        for o in (w / 2 - 0.05, -(w / 2 - 0.05)):
            E = edge(o)
            for k in np.unique(np.round(np.linspace(0, len(E) - 1, max(2, int(d[-1] / 2) + 1))).astype(int)):
                c, z0 = E[k, :2] + tan[k] * 0.05 * (int(k == 0) - int(k == len(E) - 1)), E[k, 2]  # end posts stay on the deck
                post = np.array(box_tris(c, tan[k], nrm[k], 0.04, 0.04, z0, z0 + rail_h)[:8])  # 4 sides
                struct.append(_facing(post, np.column_stack([post.mean(1)[:, :2] - c, np.zeros(8)])))
            top, bot = E + [0, 0, rail_h], E + [0, 0, rail_h - 0.08]
            for s in (1, -1):
                struct.append(_facing(_quads(bot[:-1], bot[1:], top[1:], top[:-1]), np.column_stack([nrm[:-1] * s, np.zeros(len(E) - 1)])))
    else:  # solid parapet 0.25 m: inner face and top; ends closed
        for o, s in ((w / 2, 1), (-w / 2, -1)):
            Eo, Ei = edge(o), edge(o - s * 0.25)
            struct.append(_facing(_quads(Ei[:-1], Ei[1:], Ei[1:] + up, Ei[:-1] + up), np.column_stack([-nrm[:-1] * s, np.zeros(len(Ei) - 1)])))
            struct.append(_facing(_quads(Eo[:-1] + up, Eo[1:] + up, Ei[1:] + up, Ei[:-1] + up), [0, 0, 1]))
            for k, dirn in ((0, -tan[0]), (-1, tan[-1])):
                struct.append(_facing(_quads(Eo[[k]] - down, Ei[[k]] - down, Ei[[k]] + up, Eo[[k]] + up), [*dirn, 0]))
    return deck, np.concatenate(struct)


# How strongly mapped land cover tints the satellite base colour (0 = keep Sentinel-2, 1 = flat class colour).
# Satellite colour is real; class colours are generic, so they are used lightly except for crisp features.
TINT = {"water": 1.0, "paved": 0.7, "pitch": 0.55, "track": 0.85, "court": 0.85, "cemetery": 0.25, "park": 0.2, "grass": 0.15, "forest": 0.15,
        "orchard": 0.1, "wetland": 0.15, "scrub": 0.1, "industrial": 0.2, "commercial": 0.2, "institutional": 0.15}


def sample_base(s2, bounds, size_px, origin):
    """Bilinear sample of the Sentinel-2 true-colour window at the texture's pixel centres -> uint8 HxWx3."""
    arr, inv = s2
    x0, y0, x1, y1 = bounds
    xs = x0 + (np.arange(size_px) + 0.5) * (x1 - x0) / size_px + origin[0]
    ys = y1 - (np.arange(size_px) + 0.5) * (y1 - y0) / size_px + origin[1]
    X, Y = np.meshgrid(xs, ys)
    col = inv.a * X + inv.b * Y + inv.c - 0.5
    row = inv.d * X + inv.e * Y + inv.f - 0.5
    h, w, _ = arr.shape
    c0 = np.clip(np.floor(col).astype(int), 0, w - 2)
    r0 = np.clip(np.floor(row).astype(int), 0, h - 2)
    fc, fr = np.clip(col - c0, 0, 1)[..., None], np.clip(row - r0, 0, 1)[..., None]
    v = ((arr[r0, c0] * (1 - fc) + arr[r0, c0 + 1] * fc) * (1 - fr) + (arr[r0 + 1, c0] * (1 - fc) + arr[r0 + 1, c0 + 1] * fc) * fr)
    return np.clip(v, 0, 255).astype(np.uint8)


def grade_s2(tci: np.ndarray) -> np.ndarray:
    """TCI is dark and slightly blue-green: lift mid-tones, small saturation trim. Display-only colour grade."""
    v = (tci.astype(np.float32) / 255.0) ** 0.72 * 1.12
    k = 0.62  # soft shoulder: bare soil / sand / concrete must not burn out to white under scene lighting
    v = np.where(v > k, k + (v - k) / (1 + (v - k) / 0.22), v)
    grey = v.mean(axis=-1, keepdims=True)
    v = grey + (v - grey) * 0.9
    return np.clip(v * 255, 0, 255)


def remove_roofs(rgb: np.ndarray, transform, footprints_metric, buffer_m: float = 6.0) -> tuple[np.ndarray, int]:
    """10 m pixels around buildings contain roof colour that would show as halos on the ground. Mask them
    (footprint + buffer) and fill from the nearest unmasked pixels (yards, gardens)."""
    from rasterio.features import rasterize
    h, w, _ = rgb.shape
    mask = rasterize(((g.buffer(buffer_m), 1) for g in footprints_metric), out_shape=(h, w), transform=transform,
                     all_touched=True, fill=0, dtype="uint8").astype(bool)
    out = rgb.copy()
    known = ~mask
    for _ in range(64):
        if known.all():
            break
        p = np.pad(out * known[..., None], ((1, 1), (1, 1), (0, 0)))
        k = np.pad(known.astype(np.float32), 1)
        acc = sum(p[1 + di:1 + di + h, 1 + dj:1 + dj + w] for di, dj in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)))
        cnt = sum(k[1 + di:1 + di + h, 1 + dj:1 + dj + w] for di, dj in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)))
        grow = ~known & (cnt > 0)
        out[grow] = acc[grow] / cnt[grow][:, None]
        known = known | grow
    return out, int(mask.sum())


def ground_texture(bounds, size_px: int, areas_local, water_local, roads, rails, quality: int, seed: str,
                   base: np.ndarray | None = None, footprints=None) -> bytes:
    """Rasterize ground colour for one tile -> JPEG bytes. Base = Sentinel-2 true colour when available
    (else a flat grass colour); water, rail and roads are painted crisply on top, land cover only tints."""
    from PIL import Image, ImageDraw, ImageFilter
    x0, y0, x1, y1 = bounds
    sx = size_px / (x1 - x0)
    img = Image.fromarray(base) if base is not None else Image.new("RGB", (size_px, size_px), GROUND["base"])

    def mask_of(geom):
        m = Image.new("L", (size_px, size_px), 0)
        dr = ImageDraw.Draw(m)
        for p in shapely.get_parts(geom):
            if p.geom_type != "Polygon" or p.is_empty:
                continue
            ext = [((x - x0) * sx, (y1 - y) * sx) for x, y in p.exterior.coords]
            if len(ext) >= 3:
                dr.polygon(ext, fill=255)
            for h in p.interiors:
                hole = [((x - x0) * sx, (y1 - y) * sx) for x, y in h.coords]
                if len(hole) >= 3:
                    dr.polygon(hole, fill=0)
        return m

    def paint(geom, col, alpha=1.0):
        if base is None:
            alpha = 1.0  # without a satellite base the class colours are all there is
        if alpha <= 0:
            return
        m = mask_of(geom)
        if alpha < 1:
            m = m.point(lambda v: int(v * alpha))
        img.paste(Image.new("RGB", (size_px, size_px), col), (0, 0), m)

    rank = {c: i for i, c in enumerate(COVER_PRIORITY)}
    for g, cover, *col in sorted(areas_local, key=lambda a: (rank.get(a[1], 0), -a[0].area)):
        rgb = tuple(int(col[0][i:i + 2], 16) for i in (1, 3, 5)) if col and col[0] else GROUND.get(cover, GROUND["base"])
        paint(g, rgb, TINT.get(cover, 0.0))
    if water_local is not None:
        paint(water_local.buffer(3.5).difference(water_local), GROUND["wet_bank"], 0.45)  # damp, darker banks
        paint(water_local, GROUND["water"])
    for g in rails:
        paint(g, GROUND["rail"])
    for cat, col in (("shoulder", "shoulder"), ("foot", "foot"), ("unpaved", "road_unpaved"), ("paved", "road")):
        if cat in roads:
            paint(roads[cat], GROUND[col])
    if footprints:  # baked contact shadow: soft darkening of the ground within ~2.5 m of walls
        occ = mask_of(shapely.union_all([g.buffer(1.2) for g in footprints]))
        occ = occ.filter(ImageFilter.GaussianBlur(radius=max(1.0, 1.6 * sx)))
        o = np.asarray(occ, np.float32)[..., None] / 255.0
        img = Image.fromarray(np.clip(np.asarray(img, np.float32) * (1 - 0.38 * o), 0, 255).astype(np.uint8))
    # deterministic variation from *world* coordinates, so neighbouring tiles continue seamlessly
    xs = x0 + (np.arange(size_px) + 0.5) * (x1 - x0) / size_px
    ys = y1 - (np.arange(size_px) + 0.5) * (y1 - y0) / size_px
    amp = 0.35 if base is not None else 1.0  # the satellite base already carries real variation
    var = ((world_noise(xs, ys, 70.0, 1) - 0.5) * 0.18 + (world_noise(xs, ys, 7.0, 2) - 0.5) * 0.1) * amp \
        + (world_noise(xs, ys, 1.6, 3) - 0.5) * 0.08
    a = np.clip(np.asarray(img, np.float32) * (1 + var[..., None]), 0, 255).astype(np.uint8)
    img = Image.fromarray(a).filter(ImageFilter.SMOOTH)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def world_noise(xs, ys, cell: float, seed: int):
    """Smooth value noise on a global lattice (cell metres): identical wherever it is sampled from."""
    gx, gy = np.meshgrid(xs / cell, ys / cell)
    ix, iy = np.floor(gx).astype(np.int64), np.floor(gy).astype(np.int64)
    fx, fy = gx - ix, gy - iy
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)

    def h(i, j):
        v = (i * 374761393 + j * 668265263 + seed * 1442695041) & 0xFFFFFFFF
        v = ((v ^ (v >> 13)) * 1274126177) & 0xFFFFFFFF
        return (v ^ (v >> 16)) / 0xFFFFFFFF

    a, b = h(ix, iy), h(ix + 1, iy)
    c, d = h(ix, iy + 1), h(ix + 1, iy + 1)
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


# ------------------------------------------------------------------------------------------------------
# vegetation
# ------------------------------------------------------------------------------------------------------
TREE_DENSITY = {"forest": 1 / 30, "park": 1 / 90, "cemetery": 1 / 120, "orchard": 1 / 40, "scrub": 1 / 60,
                "wetland": 1 / 250}
SRC_MAPPED, SRC_LANDCOVER, SRC_DECORATIVE, SRC_CHM = 0, 1, 2, 3  # 3: canopy height model (canopy.py)


def place_trees(tile_bounds, areas_local, blocked, houses, mapped, seed: str, decorative: bool):
    """-> list of (x, y, height, crown_r, kind, src). kind 0 deciduous, 1 conifer, 2 fruit/small,
    3 columnar thuja (very common in Skvyra front gardens on licensed street photos; decorative only).
    Procedural trees only inside mapped vegetated land cover (src=1) or, if enabled, as clearly labelled
    decorative garden trees next to houses (src=2). Never inside buildings, roads, rail or water."""
    r = random.Random(seed)
    x0, y0, x1, y1 = tile_bounds
    tile = box(*tile_bounds)
    from shapely.prepared import prep
    blk = prep(blocked) if blocked is not None and not blocked.is_empty else None
    out = []
    for (x, y) in mapped:
        out.append((x, y, r.uniform(8, 14), r.uniform(2.5, 4), 0, SRC_MAPPED))

    def ok(x, y):
        return blk is None or not blk.contains(shapely.Point(x, y))

    for g, cover, *_ in areas_local:
        dens = TREE_DENSITY.get(cover)
        if not dens:
            continue
        g = g.intersection(tile)
        if g.is_empty:
            continue
        spacing = 1 / math.sqrt(dens)
        gx0, gy0, gx1, gy1 = g.bounds
        pg = prep(g)
        # jittered lattice aligned to global coordinates -> deterministic and seamless across tiles
        for gx in np.arange(math.floor(gx0 / spacing) * spacing, gx1, spacing):
            for gy in np.arange(math.floor(gy0 / spacing) * spacing, gy1, spacing):
                rr = random.Random(f"{seed[:4]}{gx:.1f},{gy:.1f}")
                x, y = gx + rr.uniform(0, spacing), gy + rr.uniform(0, spacing)
                if not (x0 <= x < x1 and y0 <= y < y1) or not pg.contains(shapely.Point(x, y)) or not ok(x, y):
                    continue
                if cover == "orchard":
                    out.append((x, y, rr.uniform(3.5, 5.5), rr.uniform(1.8, 2.6), 2, SRC_LANDCOVER))
                elif cover == "scrub":
                    out.append((x, y, rr.uniform(2, 4), rr.uniform(1.2, 2.0), 0, SRC_LANDCOVER))
                else:
                    conifer = cover == "forest" and rr.random() < 0.3
                    out.append((x, y, rr.uniform(12, 22) if cover == "forest" else rr.uniform(8, 16),
                                rr.uniform(2.2, 3.2) if conifer else rr.uniform(3, 5), 1 if conifer else 0, SRC_LANDCOVER))
    if decorative:
        for c, rad in houses:
            rr = random.Random(f"{seed[:4]}{c[0]:.1f},{c[1]:.1f}")
            for _ in range(rr.randint(0, 3)):
                ang, d = rr.uniform(0, 2 * math.pi), rad + rr.uniform(3, 12)
                x, y = c[0] + d * math.cos(ang), c[1] + d * math.sin(ang)
                if x0 <= x < x1 and y0 <= y < y1 and ok(x, y):
                    k = rr.choices([2, 0, 3, 4], [40, 20, 20, 20])[0]
                    if k == 4:  # garden shrub
                        out.append((x, y, rr.uniform(0.9, 1.8), rr.uniform(0.6, 1.1), 4, SRC_DECORATIVE))
                    elif k == 3:
                        out.append((x, y, rr.uniform(4, 7.5), rr.uniform(0.6, 0.95), 3, SRC_DECORATIVE))
                    else:
                        out.append((x, y, rr.uniform(4, 9), rr.uniform(1.8, 3.2), k, SRC_DECORATIVE))
    return out


# ------------------------------------------------------------------------------------------------------
# decorative yard layer (never presented as data: separate mesh "decor_fence", switchable in the viewer)
# ------------------------------------------------------------------------------------------------------
FENCE_COLORS = ["#4a5e4c", "#5a4639", "#8a8c88", "#6e5a46", "#56606a", "#9a9384"]  # muted: decor must not dominate


def yard_fences(houses, road_lines, roads_union, obstacles, seed: str):
    """Street-side fence for each house within 25 m of a road: parallel to the road, between road and house,
    with a 1.2 m gate opposite the house. -> [(LineString, colour hex)]. Purely decorative."""
    out = []
    if not road_lines:
        return out
    tree = shapely.STRtree(road_lines)
    from shapely.ops import nearest_points
    for g, hid in houses:
        c = g.centroid
        j = tree.nearest(c)
        road = road_lines[j]
        rp = nearest_points(road, c)[0]
        d = rp.distance(g)
        if d < 3.5 or d > 25:
            continue
        s0 = road.project(rp)
        p1, p2 = road.interpolate(max(0, s0 - 1)), road.interpolate(min(road.length, s0 + 1))
        t = np.array([p2.x - p1.x, p2.y - p1.y])
        if np.hypot(*t) < 1e-6:
            continue
        t /= np.hypot(*t)
        nrm = np.array([c.x - rp.x, c.y - rp.y])
        nrm -= t * (nrm @ t)
        if np.hypot(*nrm) < 1e-6:
            continue
        nrm /= np.hypot(*nrm)
        off = min(d * 0.45, 6.0)
        if off < 2.5:
            continue
        proj = np.asarray(g.exterior.coords) @ t
        half = (proj.max() - proj.min()) / 2 + 3.0
        mid = np.array([rp.x, rp.y]) + nrm * (off + 0.0) + t * ((proj.max() + proj.min()) / 2 - np.array([rp.x, rp.y]) @ t)
        line = shapely.LineString([mid - t * half, mid + t * half])
        gate_c = shapely.Point(mid + t * ((np.array([c.x, c.y]) - mid) @ t))
        line = line.difference(gate_c.buffer(0.6)).difference(obstacles).difference(roads_union.buffer(0.4))
        r = random.Random(f"{seed}{hid}")
        col = r.choice(FENCE_COLORS)
        for part in shapely.get_parts(line):
            if part.geom_type == "LineString" and part.length > 1.0:
                out.append((part, col))
    return out


def fence_tris(line, terrain, height=1.5, post=3.0):
    """Thin panel following the terrain (one face, rendered double-sided) plus posts (4 sides, no caps)."""
    pts = np.asarray(shapely.segmentize(line, 4.0).coords)
    z = terrain.z(pts[:, 0], pts[:, 1])
    tris = []
    for k in range(len(pts) - 1):
        (x0, y0), (x1, y1) = pts[k], pts[k + 1]
        A, B = (x0, y0, z[k] + 0.05), (x1, y1, z[k + 1] + 0.05)
        Cc, D = (x1, y1, z[k + 1] + height), (x0, y0, z[k] + height)
        tris += [[A, B, Cc], [A, Cc, D]]
    dist = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    for s in np.arange(0, dist[-1] + 1e-6, post):
        q = line.interpolate(s)
        zz = float(terrain.z(q.x, q.y))
        c = np.array([q.x, q.y])
        u, v = np.array([1.0, 0]), np.array([0, 1.0])
        from .buildings import box_tris
        tris += box_tris(c, u, v, 0.05, 0.05, zz, zz + height + 0.1)[:8]  # sides only
    return np.array(tris).reshape(-1, 3, 3)
