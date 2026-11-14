"""Build a small, fully SYNTHETIC offline dataset under <root> (not Skvyra, not real features).

    SKVYRA_ROOT=/tmp/x python tests/fixtures/make_fixture.py /tmp/x

It exercises the edge cases the pipeline must handle: a boundary split across member ways, a courtyard
multipolygon, a self-intersecting footprint, a duplicate footprint, a building mapped as a node, an
unclosed building way, a tiny footprint, a narrow footprint, a building on a tile edge, a curved road,
a bridge over a river, a sensitive (military) area that must be suppressed, mapped trees, a POI, an ML
footprint that duplicates OSM and one that fills a gap, and a DEM with a no-data cell and a spike.
"""
import gzip
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(sys.argv[1])
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipeline"))
from pyproj import Transformer  # noqa: E402

LON0, LAT0 = 29.70, 49.80
fwd = Transformer.from_crs("EPSG:4326", "EPSG:32635", always_xy=True)
inv = Transformer.from_crs("EPSG:32635", "EPSG:4326", always_xy=True)
E0, N0 = (round(v / 100) * 100 for v in fwd.transform(LON0, LAT0))


def ll(x, y):
    lon, lat = inv.transform(E0 + x, N0 + y)
    return {"lat": round(lat, 8), "lon": round(lon, 8)}


def rect(cx, cy, w, h, ang=0.0):
    c, s = math.cos(ang), math.sin(ang)
    pts = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    pts = [(cx + x * c - y * s, cy + x * s + y * c) for x, y in pts]
    return pts + [pts[0]]


def way(i, pts, tags, closed=True):
    g = [ll(x, y) for x, y in pts]
    return {"type": "way", "id": i, "geometry": g, "tags": tags, "nodes": list(range(len(g)))}


def main():
    raw = ROOT / "data" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (ROOT / "config").mkdir(exist_ok=True)
    cfg = json.loads((Path(__file__).resolve().parents[2] / "config" / "city.json").read_text())
    cfg.update(name="Synthetic test town", boundary={"osm_relation": 1, "expect": {"admin_level": "9", "place": "town"}},
               origin_utm=[E0, N0], tile_size_m=250, context_buffer_m=50, overpass_url="http://127.0.0.1:9/never")
    cfg["ms_buildings"]["region"] = "Testland"
    (ROOT / "config" / "city.json").write_text(json.dumps(cfg, indent=2))

    # boundary: square +-400 m, outer ring split into two member ways (tests ring assembly)
    sq = [(-400, -400), (400, -400), (400, 400), (-400, 400), (-400, -400)]
    rel = {"type": "relation", "id": 1, "tags": {"type": "boundary", "boundary": "administrative", "admin_level": "9",
                                                 "place": "town", "name": "Тестове"},
           "members": [{"type": "way", "ref": 11, "role": "outer", "geometry": [ll(*p) for p in sq[:3]]},
                       {"type": "way", "ref": 12, "role": "outer", "geometry": [ll(*p) for p in sq[2:]]}]}
    stamp = {"version": 0.6, "osm3s": {"timestamp_osm_base": "2026-01-01T00:00:00Z"}}
    (raw / "boundary.json").write_text(json.dumps({**stamp, "elements": [rel]}))

    els = [
        way(101, rect(0, 0, 20, 10), {"building": "house", "building:levels": "2", "addr:street": "Тестова вулиця", "addr:housenumber": "1"}),
        way(102, [(60, 0), (90, 0), (90, 10), (70, 10), (70, 25), (60, 25), (60, 0)], {"building": "yes", "roof:shape": "gabled"}),
        way(103, [(100, 100), (120, 120), (120, 100), (100, 120), (100, 100)], {"building": "yes"}),  # bowtie
        way(104, rect(0, 0, 20, 10), {"building": "house"}),  # exact duplicate of 101
        way(105, [(0, 60), (10, 60), (10, 70)], {"building": "yes"}),  # unclosed
        way(106, rect(-50, -60, 1, 1), {"building": "shed"}),  # tiny
        way(107, rect(-150, 120, 30, 1.6, 0.3), {"building": "yes"}),  # narrow
        way(108, rect(250, 40, 12, 8), {"building": "yes", "roof:shape": "skillion"}),  # on the x=250 tile edge
        {"type": "node", "id": 301, "lat": ll(30, -80)["lat"], "lon": ll(30, -80)["lon"], "tags": {"building": "yes", "name": "Точка"}},
        {"type": "relation", "id": 201, "tags": {"type": "multipolygon", "building": "apartments", "height": "15"},
         "members": [{"type": "way", "ref": 21, "role": "outer", "geometry": [ll(*p) for p in rect(-100, 50, 40, 40)]},
                     {"type": "way", "ref": 22, "role": "inner", "geometry": [ll(*p) for p in rect(-100, 50, 20, 20)]}]},
        way(401, [(-390, -30), (0, -30), (390, -30)], {"highway": "primary", "name": "Тестова вулиця"}, False),
        way(402, [(-300, 200 + 40 * math.sin(i / 3)) for i in range(0, 1)] + [(-300 + 30 * i, 200 + 40 * math.sin(i / 3)) for i in range(1, 21)],
            {"highway": "residential", "name": "Кривий провулок"}, False),
        way(403, [(150, -300), (150, -100)], {"highway": "track"}, False),
        way(404, [(-230, -150), (-170, -150)], {"highway": "residential", "bridge": "yes", "name": "Мостова вулиця"}, False),
        way(405, [(-390, -150), (-230, -150)], {"highway": "residential", "name": "Мостова вулиця"}, False),
        way(501, [(-200, -400), (-200, 400)], {"waterway": "river", "name": "Тестова річка"}, False),
        way(601, rect(250, 250, 200, 150), {"landuse": "forest"}),
        way(602, rect(300, -300, 80, 80), {"landuse": "military"}),
        way(603, rect(-300, -300, 120, 100), {"natural": "water"}),
        {"type": "node", "id": 701, **ll(20, 30), "tags": {"natural": "tree"}},
        {"type": "node", "id": 801, **ll(-60, -10), "tags": {"amenity": "school", "name": "Тестова школа"}},
    ]
    (raw / "osm.json").write_text(json.dumps({**stamp, "elements": els}))

    def feat(pts):
        return json.dumps({"type": "Feature", "properties": {"height": -1.0, "confidence": -1.0},
                           "geometry": {"type": "Polygon", "coordinates": [[[ll(*p)["lon"], ll(*p)["lat"]] for p in pts]]}})
    from skvyra.acquire import quadkey
    keys = sorted({quadkey(la, lo, 9) for la in (LAT0 - 0.01, LAT0, LAT0 + 0.01) for lo in (LON0 - 0.01, LON0, LON0 + 0.01)})
    ms_names = [f"ms_buildings_{k}.csv.gz" for k in keys]
    for k, nm in enumerate(ms_names):
        with gzip.open(raw / nm, "wt") as f:
            if k == 0:
                f.write(feat(rect(0.3, 0.2, 20, 10)) + "\n")      # duplicates OSM way 101 -> dropped
                f.write(feat(rect(150, -150, 12, 9)) + "\n")      # gap fill -> added

    import rasterio
    from rasterio.transform import from_origin
    res = 0.0003
    w, n = LON0 - 0.02, LAT0 + 0.015
    cols, rows = int(0.04 / res), int(0.03 / res)
    lon = w + (np.arange(cols) + 0.5) * res
    lat = n - (np.arange(rows) + 0.5) * res
    LON, LAT = np.meshgrid(lon, lat)
    z = 200 + (LON - LON0) * 300 + 6 * np.exp(-(((LON - LON0) / 0.004) ** 2 + ((LAT - LAT0) / 0.003) ** 2))
    z = z.astype(np.float32)
    z[rows // 2 + 3, cols // 2 + 5] = -32767.0   # no-data cell
    z[rows // 2 - 4, cols // 2 - 6] += 40.0      # spike outlier
    with rasterio.open(raw / "dem_copernicus_glo30.tif", "w", driver="GTiff", height=rows, width=cols, count=1,
                       dtype="float32", crs="EPSG:4326", transform=from_origin(w, n, res, res), nodata=-32767.0) as dst:
        dst.write(z, 1)

    # synthetic Sentinel-2 true colour (UTM 35N like the real scenes) with a bright "roof" blob at way 101
    from rasterio.transform import from_origin as fo
    size = 120  # 10 m pixels, +-600 m
    rgb = np.zeros((3, size, size), np.uint8)
    rgb[0], rgb[1], rgb[2] = 60, 110, 50  # grass
    yy, xx = np.mgrid[0:size, 0:size]
    cx, cy = size // 2, size // 2  # local (0, 0) = way 101
    rgb[:, (abs(xx - cx) <= 1) & (abs(yy - cy) <= 1)] = 230
    with rasterio.open(raw / "sentinel2_tci.tif", "w", driver="GTiff", height=size, width=size, count=3, dtype="uint8",
                       crs="EPSG:32635", transform=fo(E0 - 600, N0 + 600, 10, 10)) as dst:
        dst.write(rgb)
    # synthetic GHSL ANBH (3 arcsec): 12 m over the apartment courtyard block, 3.5 m elsewhere
    gres = 0.0008333333
    gw, gn = LON0 - 0.02, LAT0 + 0.015
    gcols, grows = int(0.04 / gres), int(0.03 / gres)
    anbh = np.full((grows, gcols), 3.5, np.float32)
    a_ll = ll(-100, 50)
    anbh[int((gn - a_ll["lat"]) / gres), int((a_ll["lon"] - gw) / gres)] = 12.0
    with rasterio.open(raw / "ghsl_anbh_2018.tif", "w", driver="GTiff", height=grows, width=gcols, count=1, dtype="float32",
                       crs="EPSG:4326", transform=fo(gw, gn, gres, gres)) as dst:
        dst.write(anbh, 1)

    # synthetic Meta/WRI canopy height (Web Mercator ~1.19 m like the real COG): two crowns, one model-fill plateau
    from pyproj import Transformer
    tm = Transformer.from_crs(4326, 3857, always_xy=True)
    c_ll = ll(0, 0)
    mx, my = tm.transform(c_ll["lon"], c_ll["lat"])
    px = 1.1943285669558747
    n = 1100  # +-~655 m (+-~420 m on the ground)
    chm = np.zeros((n, n), np.uint8)
    yy, xx = np.mgrid[0:n, 0:n]
    k = 1 / np.cos(np.radians(LAT0))  # ground m -> mercator px
    for gx, gy, h, r in ((300, 300, 14, 5), (-300, 250, 9, 4)):
        q = ll(gx, gy)  # exact local -> WGS84 -> Web Mercator (UTM grid north is ~2 deg off Mercator north here)
        qx, qy = tm.transform(q["lon"], q["lat"])
        cx_, cy_ = (qx - (mx - n / 2 * px)) / px - 0.5, ((my + n / 2 * px) - qy) / px - 0.5
        chm = np.maximum(chm, (h * np.exp(-((xx - cx_) ** 2 + (yy - cy_) ** 2) / (2 * (r * k / px / 2) ** 2))).astype(np.uint8))
    chm[n // 2 + int(150 * k / px):n // 2 + int(300 * k / px), n // 2 - int(300 * k / px):n // 2 - int(150 * k / px)] = 10
    with rasterio.open(raw / "chm_meta_v2.tif", "w", driver="GTiff", height=n, width=n, count=1, dtype="uint8",
                       crs="EPSG:3857", transform=fo(mx - n / 2 * px, my + n / 2 * px, px, px)) as dst:
        dst.write(chm, 1)
    (raw / "chm_meta_v2_dates.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature",
        "properties": {"acq_date": "2015-06-24"}, "geometry": {"type": "Polygon", "coordinates": [[
            [LON0 - 0.02, LAT0 - 0.02], [LON0 + 0.02, LAT0 - 0.02], [LON0 + 0.02, LAT0 + 0.02], [LON0 - 0.02, LAT0 + 0.02], [LON0 - 0.02, LAT0 - 0.02]]]}}]}))
    wres = 1 / 12000
    with rasterio.open(raw / "worldcover_2021.tif", "w", driver="GTiff", height=480, width=720, count=1, dtype="uint8",
                       crs="EPSG:4326", transform=fo(LON0 - 0.03, LAT0 + 0.02, wres, wres)) as dst:
        dst.write(np.full((480, 720), 30, np.uint8), 1)  # grassland everywhere

    from skvyra import acquire
    for nm in ("chm_meta_v2.tif", "chm_meta_v2_dates.geojson", "worldcover_2021.tif"):
        acquire.record(nm, raw / nm, source="SYNTHETIC fixture", license="test only")
    acquire.record("sentinel2_tci.tif", raw / "sentinel2_tci.tif", source="SYNTHETIC fixture", license="test only")
    acquire.record("ghsl_anbh_2018.tif", raw / "ghsl_anbh_2018.tif", source="SYNTHETIC fixture", license="test only")
    acquire.record("boundary.json", raw / "boundary.json", source="SYNTHETIC fixture", license="test only",
                   source_timestamp="2026-01-01T00:00:00Z")
    acquire.record("osm.json", raw / "osm.json", source="SYNTHETIC fixture", license="test only",
                   source_timestamp="2026-01-01T00:00:00Z", query="synthetic")
    for nm in ms_names:
        acquire.record(nm, raw / nm, source="SYNTHETIC fixture", license="test only", source_timestamp="2026-01-01")
    acquire.record("dem_copernicus_glo30.tif", raw / "dem_copernicus_glo30.tif", source="SYNTHETIC fixture",
                   license="test only")
    print(f"fixture written to {ROOT} (origin {E0}, {N0})")


if __name__ == "__main__":
    main()
