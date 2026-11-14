"""Unit tests for the geospatial pipeline (no network, no real data)."""
import http.server
import json
import threading

import numpy as np
import pytest
import shapely
from shapely.geometry import Point, Polygon

from skvyra import acquire, buildings as B, config as C, normalize as N, osm, surfaces as S
from skvyra.geo import Frame, to_gltf
from skvyra.terrain import FlatTerrain

FRAME = Frame("EPSG:32635", (691900, 5512000))
CFG = {"floor_height_m": 3.0, "seed": 7}


# ---------------------------------------------------------------------------------------------------
# coordinates
# ---------------------------------------------------------------------------------------------------
def test_origin_maps_to_zero_and_round_trips():
    lon, lat = FRAME.to_geo(0.0, 0.0)
    assert abs(lon - 29.662925641) < 1e-8 and abs(lat - 49.729862515) < 1e-8
    xs = np.array([-3000.0, 0.0, 2875.5])
    ys = np.array([2500.0, 0.0, -2999.25])
    x2, y2 = FRAME.to_local(*FRAME.to_geo(xs, ys))
    assert np.max(np.hypot(x2 - xs, y2 - ys)) < 1e-6


def test_axis_convention_local_to_gltf_is_rotation():
    p = np.array([[1.0, 2.0, 3.0]])  # east, north, up
    assert to_gltf(p).tolist() == [[1.0, 3.0, -2.0]]  # x east, y up, z = -north
    m = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]])
    assert np.isclose(np.linalg.det(m), 1.0)  # proper rotation: preserves triangle winding


def test_east_is_positive_x():
    x, y = FRAME.to_local(29.70, 49.7299)  # east of the origin
    assert x > 2000 and abs(y) < 100


# ---------------------------------------------------------------------------------------------------
# OSM parsing and geometry repair
# ---------------------------------------------------------------------------------------------------
def g(*pts):
    return [{"lon": x, "lat": y} for x, y in pts]


def test_unclosed_and_missing_nodes_are_reported():
    assert osm.way_geometry({"type": "way", "tags": {"building": "yes"}, "geometry": g((0, 0), (1, 0), (1, 1))})[1] == "unclosed_ring"
    el = {"type": "way", "tags": {"building": "yes"}, "geometry": [{"lon": 0, "lat": 0}, None, {"lon": 1, "lat": 1}, {"lon": 0, "lat": 0}]}
    assert osm.way_geometry(el)[1] == "missing_nodes:1"


def test_linear_features_are_not_areas():
    assert not osm.is_area_way({"highway": "residential"})
    assert not osm.is_area_way({"man_made": "goods_conveyor"})
    assert osm.is_area_way({"highway": "pedestrian", "area": "yes"})
    assert osm.is_area_way({"natural": "water"})


def test_multipolygon_with_split_outer_and_hole():
    rel = {"type": "relation", "tags": {"type": "multipolygon", "building": "yes"}, "members": [
        {"type": "way", "role": "outer", "geometry": g((0, 0), (4, 0), (4, 4))},
        {"type": "way", "role": "outer", "geometry": g((4, 4), (0, 4), (0, 0))},
        {"type": "way", "role": "inner", "geometry": g((1, 1), (3, 1), (3, 3), (1, 3), (1, 1))}]}
    geom, problem = osm.relation_geometry(rel)
    assert problem is None
    assert geom.area == pytest.approx(16 - 4)
    assert len(geom.interiors) == 1


def test_bowtie_is_repaired_into_valid_parts():
    bow = Polygon([(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)])
    assert not bow.is_valid
    parts, repaired = N.polygon_parts(bow)
    assert repaired and len(parts) == 2 and all(p.is_valid for p in parts)


def test_sensitive_features_are_detected():
    assert N.is_sensitive({"landuse": "military"})
    assert N.is_sensitive({"military": "barracks"})
    assert not N.is_sensitive({"landuse": "residential"})


# ---------------------------------------------------------------------------------------------------
# identities
# ---------------------------------------------------------------------------------------------------
def test_stable_ids_are_deterministic_and_geometry_sensitive():
    p = shapely.box(10, 10, 20, 18)
    assert N.stable_ms_id(p) == N.stable_ms_id(shapely.box(10, 10, 20, 18))
    assert N.stable_ms_id(p) != N.stable_ms_id(shapely.box(13, 10, 23, 18))
    assert N.internal_id("osm:way/1") == N.internal_id("osm:way/1") != N.internal_id("osm:way/2")


# ---------------------------------------------------------------------------------------------------
# height / roof inference
# ---------------------------------------------------------------------------------------------------
def props(tags, bid="b1"):
    return {"id": bid, "tags": tags}


def test_height_hierarchy():
    a = B.resolve(props({"building": "house", "height": "12", "building:levels": "5"}), 100, 1.5, 1, None, CFG)
    assert a["height_method"] == "source_height" and a["height"] == pytest.approx(12, abs=0.01)
    a = B.resolve(props({"building": "house", "building:levels": "3"}), 100, 1.5, 1, None, CFG)
    assert a["height_method"] == "source_levels" and a["eave_height"] == pytest.approx(3 * 3.0 + 0.4)
    a = B.resolve(props({"building": "house"}), 100, 1.5, 1, None, CFG)
    assert a["height_method"] == "type_default"
    a = B.resolve(props({"building": "house", "height": "12"}), 100, 1.5, 1, None, CFG, override={"height": 9.5})
    assert a["height_method"] == "override" and a["height"] == pytest.approx(9.5, abs=0.01)


def test_roof_tags_and_aliases():
    a = B.resolve(props({"building": "yes", "roof:shape": "skillion"}), 80, 1.5, 1, None, CFG)
    assert (a["roof"], a["roof_method"]) == ("shed", "source_tag")
    a = B.resolve(props({"building": "yes", "roof:shape": "something_odd"}), 80, 1.5, 1, None, CFG)
    assert a["roof_method"] == "inferred"
    assert B.resolve(props({"building": "apartments"}), 600, 4, 1, None, CFG)["roof"] == "flat"


def test_bad_tag_values_do_not_crash():
    a = B.resolve(props({"building": "yes", "height": "approx 7 m?", "building:levels": "two"}), 90, 1.2, 1, None, CFG)
    assert a["height_method"] == "type_default"


def test_inference_is_deterministic_per_id():
    a1 = B.resolve(props({}, "bX"), 90, 1.3, 0.9, None, CFG)
    a2 = B.resolve(props({}, "bX"), 90, 1.3, 0.9, None, CFG)
    assert a1 == a2


# ---------------------------------------------------------------------------------------------------
# building meshes
# ---------------------------------------------------------------------------------------------------
FOOTPRINTS = {
    "rect": shapely.box(0, 0, 12, 8),
    "rotated": shapely.affinity.rotate(shapely.box(0, 0, 15, 7), 33, origin=(0, 0)),
    "L": Polygon([(0, 0), (20, 0), (20, 8), (8, 8), (8, 18), (0, 18)]),
    "courtyard": shapely.box(0, 0, 40, 40).difference(shapely.box(12, 12, 28, 28)),
    "narrow": shapely.box(0, 0, 30, 1.2),
    "square": shapely.box(0, 0, 10, 10),
}


def area_xy(tris):
    a, b = tris[:, 1, :2] - tris[:, 0, :2], tris[:, 2, :2] - tris[:, 0, :2]
    return float(np.sum(np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0])) / 2)


@pytest.mark.parametrize("roof", ["flat", "gabled", "hipped", "half-hipped", "pyramidal", "shed"])
@pytest.mark.parametrize("name", list(FOOTPRINTS))
def test_roof_covers_footprint_exactly_and_stays_in_bounds(roof, name):
    p = shapely.orient_polygons(FOOTPRINTS[name])
    attrs = {"roof": roof, "roof_height": 3.0, "eave_height": 5.0, "kind": "house", "chimney": False, "rand": 0.5}
    m = B.building_mesh(p, attrs, FlatTerrain().z, 3.0, lod=1)
    roof_t, roof_uv = m["roof"]
    assert roof_uv.shape == (len(roof_t), 3, 2)
    assert len(roof_t) > 0
    # roof projected area == footprint area: no gaps, no overlapping facets
    assert area_xy(roof_t) == pytest.approx(p.area, rel=5e-3)
    z = roof_t[..., 2]
    assert z.min() >= 5.0 - 1e-6 and z.max() <= 8.0 + 1e-6
    # all roof facets face up, no degenerate triangles
    n = np.cross(roof_t[:, 1] - roof_t[:, 0], roof_t[:, 2] - roof_t[:, 0])
    assert (n[:, 2] > -1e-9).all()
    walls, uv = m["walls"]
    assert len(walls) and len(uv) == len(walls)
    assert walls[..., 2].min() == pytest.approx(-0.5)  # foundation sinks below ground


@pytest.mark.parametrize("name", list(FOOTPRINTS))
def test_walls_face_outward(name):
    p = shapely.orient_polygons(FOOTPRINTS[name])
    attrs = {"roof": "flat", "roof_height": 0.0, "eave_height": 4.0, "kind": "house", "chimney": False, "rand": 0.5}
    walls, _ = B.building_mesh(p, attrs, FlatTerrain().z, 3.0, lod=1)["walls"]
    n = np.cross(walls[:, 1] - walls[:, 0], walls[:, 2] - walls[:, 0])
    n /= np.linalg.norm(n, axis=1)[:, None]
    c = walls.mean(axis=1)
    probe = c[:, :2] + n[:, :2] * 0.05
    inside = shapely.contains_xy(p, probe[:, 0], probe[:, 1])
    assert not inside.any()


def test_gable_walls_reach_the_ridge():
    p = shapely.orient_polygons(shapely.box(0, 0, 12, 8))
    attrs = {"roof": "gabled", "roof_height": 3.0, "eave_height": 5.0, "kind": "house", "chimney": False, "rand": 0.5}
    walls, _ = B.building_mesh(p, attrs, FlatTerrain().z, 3.0, lod=1)["walls"]
    assert walls[..., 2].max() == pytest.approx(8.0, abs=1e-6)  # gable end apex


def test_buildings_on_slopes_never_float():
    p = shapely.orient_polygons(shapely.box(0, 0, 20, 10))
    slope = lambda x, y: 0.2 * np.asarray(x, float)  # 20 % slope along x
    attrs = {"roof": "flat", "roof_height": 0.0, "eave_height": 4.0, "kind": "house", "chimney": False, "rand": 0.5}
    walls, _ = B.building_mesh(p, attrs, slope, 3.0, lod=1)["walls"]
    assert walls[..., 2].min() <= 0.0 - 0.5 + 1e-9  # below the lowest ground corner


def test_mesh_generation_is_deterministic():
    p = shapely.orient_polygons(FOOTPRINTS["L"])
    attrs = B.resolve(props({"building": "house"}, "det"), p.area, 1.2, 0.7, None, CFG)
    a = B.building_mesh(p, attrs, FlatTerrain().z, 3.0, lod=0)
    b = B.building_mesh(p, attrs, FlatTerrain().z, 3.0, lod=0)
    assert np.array_equal(a["roof"][0], b["roof"][0]) and np.array_equal(a["walls"][0], b["walls"][0])


# ---------------------------------------------------------------------------------------------------
# draping and roads
# ---------------------------------------------------------------------------------------------------
class Slope:
    step = 10.0

    def z(self, x, y):
        return 0.05 * np.asarray(x, float) + 0.02 * np.asarray(y, float)


def test_drape_preserves_area_and_follows_terrain():
    poly = shapely.LineString([(3, 3), (87, 41)]).buffer(3)
    tris = S.drape(poly, Slope(), (0, 0, 100, 100), offset=0.1)
    assert area_xy(tris) == pytest.approx(poly.area, rel=1e-3)
    flat = tris.reshape(-1, 3)
    assert np.allclose(flat[:, 2], Slope().z(flat[:, 0], flat[:, 1]) + 0.1)


def test_drape_clips_to_tile():
    poly = shapely.box(-50, -50, 150, 150)
    tris = S.drape(poly, Slope(), (0, 0, 100, 100), offset=0)
    assert area_xy(tris) == pytest.approx(100 * 100, rel=1e-6)


def test_road_union_has_no_overlaps():
    lines = [(shapely.LineString([(0, 0), (100, 0)]), {"highway": "primary"}),
             (shapely.LineString([(50, -50), (50, 50)]), {"highway": "residential"}),
             (shapely.LineString([(0, 30), (100, 30)]), {"highway": "track"})]
    polys, bridges = S.road_polygons(lines)
    assert set(polys) == {"paved", "unpaved"}
    assert polys["paved"].intersection(polys["unpaved"]).area < 1e-6
    assert S.road_width({"highway": "residential", "width": "7.5"}) == (7.5, False)
    assert S.road_width({"highway": "residential"})[1] is True


def test_trees_avoid_blocked_areas():
    forest = [(shapely.box(0, 0, 200, 200), "forest")]
    blocked = shapely.box(50, 50, 150, 150)
    trees = S.place_trees((0, 0, 200, 200), forest, blocked, [], [], "seed", decorative=False)
    assert len(trees) > 100
    assert not any(blocked.contains(Point(t[0], t[1])) for t in trees)
    assert trees == S.place_trees((0, 0, 200, 200), forest, blocked, [], [], "seed", decorative=False)


def test_world_noise_is_seamless_across_tiles():
    xs_a = np.array([249.0, 250.0])
    a = S.world_noise(xs_a, np.array([10.0]), 70.0, 1)
    b = S.world_noise(np.array([250.0]), np.array([10.0]), 70.0, 1)
    assert a[0, 1] == pytest.approx(b[0, 0])


# ---------------------------------------------------------------------------------------------------
# terrain
# ---------------------------------------------------------------------------------------------------
def test_terrain_tiles_share_edge_vertices(tmp_path):
    import rasterio
    from rasterio.transform import from_origin
    from skvyra.terrain import Terrain
    z = (200 + np.random.default_rng(1).random((60, 80)) * 5).astype(np.float32)
    path = tmp_path / "dem.tif"
    with rasterio.open(path, "w", driver="GTiff", height=60, width=80, count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(29.65, 49.74, 0.0003, 0.0003)) as d:
        d.write(z, 1)
    t = Terrain(path, FRAME, step=10)
    a, _ = t.grid_mesh(0, 0, 100, 100, skirt=0)
    b, _ = t.grid_mesh(100, 0, 200, 100, skirt=0)
    edge_a = {tuple(v) for v in a.reshape(-1, 3) if v[0] == 100}
    edge_b = {tuple(v) for v in b.reshape(-1, 3) if v[0] == 100}
    assert edge_a == edge_b and len(edge_a) == 11  # bit-identical shared border: no cracks
    # z() evaluates exactly the rendered triangles
    for tri in a[:50]:
        c = tri.mean(axis=0)
        assert t.z(c[0], c[1]) == pytest.approx(c[2], abs=1e-6)


# ---------------------------------------------------------------------------------------------------
# config and acquisition robustness
# ---------------------------------------------------------------------------------------------------
def test_config_validation(tmp_path):
    good = json.loads((C.ROOT / "config" / "city.json").read_text())
    p = tmp_path / "c.json"
    p.write_text(json.dumps(good))
    assert C.load(p)["name"] == "Skvyra"
    for bad in ({**good, "tile_size_m": 10}, {k: v for k, v in good.items() if k != "seed"}, {**good, "crs_metric": "utm35"}):
        p.write_text(json.dumps(bad))
        with pytest.raises(C.ConfigError):
            C.load(p)
    p.write_text("{not json")
    with pytest.raises(C.ConfigError):
        C.load(p)


class FlakyHandler(http.server.BaseHTTPRequestHandler):
    script: list = []

    def do_POST(self):
        # consume the request body: closing a socket with unread data sends a TCP RST, and the client then sees a
        # random ConnectionResetError (made this test flaky)
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        code, body = self.script.pop(0)
        self.send_response(code)
        if code == 429:
            self.send_header("Retry-After", "0")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), FlakyHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/"
    srv.shutdown()
    srv.server_close()


def test_http_retries_429_and_503_then_succeeds(server):
    FlakyHandler.script = [(429, b"slow down"), (503, b"busy"), (200, b"ok")]
    sleeps = []
    assert acquire.http_post(server, {"data": "x"}, "test", sleep=sleeps.append) == b"ok"
    assert len(sleeps) == 2


def test_http_non_retryable_fails_fast(server):
    FlakyHandler.script = [(400, b"bad query")]
    with pytest.raises(acquire.AcquisitionError, match="HTTP 400"):
        acquire.http_post(server, {"data": "x"}, "test", sleep=lambda s: None)


def test_http_gives_up_after_attempts(server):
    FlakyHandler.script = [(500, b"x")] * 3
    with pytest.raises(acquire.AcquisitionError, match="gave up"):
        acquire.http_post(server, {"data": "x"}, "test", attempts=3, sleep=lambda s: None)


def test_overpass_remark_and_schema_errors(server, tmp_path, monkeypatch):
    monkeypatch.setattr(C, "RAW", tmp_path)
    cfg = {"overpass_url": server, "user_agent": "t", "osm_license": "x"}
    FlakyHandler.script = [(200, json.dumps({"elements": [], "remark": "runtime error: timeout"}).encode())]
    with pytest.raises(acquire.AcquisitionError, match="remark"):
        acquire.overpass("q.json", "q", cfg)
    FlakyHandler.script = [(200, b'{"unexpected": 1}')]
    with pytest.raises(acquire.AcquisitionError, match="schema"):
        acquire.overpass("q.json", "q", cfg)
    FlakyHandler.script = [(200, b"<html>gateway</html>")]
    with pytest.raises(acquire.AcquisitionError, match="non-JSON"):
        acquire.overpass("q.json", "q", cfg)
    assert not (tmp_path / "q.json").exists()  # nothing half-written


def test_corrupted_cache_is_detected_and_offline_mode_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "RAW", tmp_path)
    f = tmp_path / "osm.json"
    f.write_text('{"elements": []}')
    acquire.record("osm.json", f, source="t")
    assert acquire.cached("osm.json") == f
    f.write_text('{"elements": [1]}')  # silently modified / truncated
    assert acquire.cached("osm.json") is None
    with pytest.raises(acquire.AcquisitionError, match="offline"):
        acquire.overpass("osm.json", "q", {"overpass_url": "x", "user_agent": "t", "osm_license": "x"}, offline=True)


@pytest.mark.parametrize("verbose", [True, False])
def test_dem_cleanup_does_not_depend_on_logging(tmp_path, verbose):
    """Regression: silencing warnings once also skipped the no-data fill and spike removal."""
    import rasterio
    from rasterio.transform import from_origin
    from skvyra.terrain import Terrain
    z = np.full((40, 40), 200.0, np.float32)
    z[20, 20] = -32767.0
    z[10, 10] = 260.0
    path = tmp_path / "dem.tif"
    with rasterio.open(path, "w", driver="GTiff", height=40, width=40, count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(29.65, 49.74, 0.0003, 0.0003), nodata=-32767.0) as d:
        d.write(z, 1)
    t = Terrain(path, FRAME, step=10, verbose=verbose)
    assert t.dem.min() == pytest.approx(200.0) and t.dem.max() == pytest.approx(200.0)


# ---------------------------------------------------------------------------------------------------
# multi-part roofs (decomposition of complex footprints)
# ---------------------------------------------------------------------------------------------------
COMPLEX = {
    "L": Polygon([(0, 0), (14, 0), (14, 6), (6, 6), (6, 15), (0, 15)]),
    "T": Polygon([(0, 8), (20, 8), (20, 15), (13, 15), (13, 25), (7, 25), (7, 15), (0, 15)]),
    "U": Polygon([(0, 0), (24, 0), (24, 16), (17, 16), (17, 6), (7, 6), (7, 16), (0, 16)]),
    "house+annex": Polygon([(0, 0), (12, 0), (12, 3), (16, 3), (16, 7), (12, 7), (12, 9), (0, 9)]),
    "rotated L": shapely.affinity.rotate(Polygon([(0, 0), (14, 0), (14, 6), (6, 6), (6, 15), (0, 15)]), 27, origin=(0, 0)),
}


@pytest.mark.parametrize("roof", ["gabled", "hipped", "half-hipped", "pyramidal"])
def test_plane_regions_partition_the_box(roof):
    L, W = 7.0, 4.0
    regs = B.plane_regions(B.planes_for(roof, L, W), L, W, m=1.0)
    assert sum(r.area for r, _ in regs) == pytest.approx((2 * L + 2) * (2 * W + 2), rel=1e-9)
    assert shapely.union_all([r for r, _ in regs]).area == pytest.approx((2 * L + 2) * (2 * W + 2), rel=1e-9)


@pytest.mark.parametrize("name", list(COMPLEX))
def test_complex_footprints_split_into_parts(name):
    dec = B.decompose(shapely.orient_polygons(COMPLEX[name]))
    assert dec is not None
    parts, filler = dec
    assert len(parts) >= 2
    covered = shapely.union_all(parts + filler)
    assert covered.symmetric_difference(COMPLEX[name]).area < 0.5


@pytest.mark.parametrize("roof", ["gabled", "hipped", "half-hipped"])
@pytest.mark.parametrize("name", list(COMPLEX))
def test_multipart_roof_covers_footprint_and_walls_face_out(roof, name):
    p = shapely.orient_polygons(COMPLEX[name])
    attrs = {"roof": roof, "eave_height": 3.4, "kind": "house", "pitch": 30.0, "chimney": False, "rand": 0.3,
             "decompose": True, "facade": "house"}
    m = B.building_mesh(p, attrs, FlatTerrain().z, 3.0, lod=1)
    roof_t, _ = m["roof"]
    assert area_xy(roof_t) == pytest.approx(p.area, rel=5e-3)  # parts tile the footprint: no holes, no overlaps
    assert roof_t[..., 2].min() >= 2.2 - 1e-6 and roof_t[..., 2].max() <= 3.4 + 5.0 + 1e-6
    walls, uv = m["walls"]
    n = np.cross(walls[:, 1] - walls[:, 0], walls[:, 2] - walls[:, 0])
    ok = np.linalg.norm(n, axis=1) > 1e-9
    assert ok.all()
    # exterior walls (those with a foundation below ground) all face out of the footprint
    ext = walls[:, :, 2].min(axis=1) < 0
    nn = n[ext] / np.linalg.norm(n[ext], axis=1)[:, None]
    probe = walls[ext].mean(axis=1)[:, :2] + nn[:, :2] * 0.05
    assert not shapely.contains_xy(p, probe[:, 0], probe[:, 1]).any()


def test_multipart_has_gable_faces_between_parts():
    p = shapely.orient_polygons(COMPLEX["T"])
    attrs = {"roof": "gabled", "eave_height": 3.4, "kind": "house", "pitch": 35.0, "chimney": False, "rand": 0.9,
             "decompose": True, "facade": "house"}
    walls, uv = B.building_mesh(p, attrs, FlatTerrain().z, 3.0, lod=1)["walls"]
    internal = walls[(uv[:, :, 0] < 0).all(axis=1) & (walls[:, :, 2].min(axis=1) >= 2.0)]
    assert len(internal) > 0  # vertical faces where differently oriented roofs meet


# ---------------------------------------------------------------------------------------------------
# GHSL height evidence, Sentinel-2 roof removal, DSM -> DTM, landmark matching
# ---------------------------------------------------------------------------------------------------
def _ghsl(tmp_path, values):
    import rasterio
    from rasterio.transform import from_origin
    path = tmp_path / "anbh.tif"
    a = np.asarray(values, np.float32)
    with rasterio.open(path, "w", driver="GTiff", height=a.shape[0], width=a.shape[1], count=1, dtype="float32",
                       crs="EPSG:4326", transform=from_origin(29.0, 50.0, 0.001, 0.001)) as d:
        d.write(a, 1)
    return path


def test_ghsl_evidence_dominant_and_small_cells(tmp_path):
    from skvyra.heights import evidence
    path = _ghsl(tmp_path, [[9.0, 4.0, 2.5], [3.0, 3.0, 3.0]])
    pts = [(29.0005, 49.9995, 900), (29.0005, 49.9995, 60),     # cell (0,0): one big + one small building
           (29.0015, 49.9995, 120), (29.0015, 49.9995, 80),     # cell (0,1): only small ones
           (29.0025, 49.9995, 100)]                             # cell (0,2): ANBH at the product floor
    ev = evidence(pts, path)
    assert ev[0]["anbh"] == 9.0 and ev[0]["share"] > 0.9 and not ev[0]["small_cell"]
    assert ev[2]["small_cell"] and ev[2]["anbh"] == 4.0
    assert ev[4]["anbh"] is None  # 2.5 m = no information
    assert ev[0]["nbr_max"] == 9.0


def test_ghsl_rules_respect_hierarchy():
    house = {"id": "h1", "tags": {"building": "house"}}
    a = B.resolve(house, 120, 1.3, 1, None, CFG, ev={"anbh": 7.5, "share": 0.4, "small_cell": True})
    # GHSL noise (RMSE 3.55 m) must not decide storeys of small houses (audit 2026-11-12)
    assert (a["height_method"], a["levels"]) == ("type_default", 1)
    a = B.resolve({**house, "tags": {"building": "house", "building:levels": "1"}}, 120, 1.3, 1, None, CFG,
                  ev={"anbh": 7.5, "share": 0.4, "small_cell": True})
    assert a["height_method"] == "source_levels" and a["levels"] == 1  # OSM wins over satellite estimate
    big = {"id": "s1", "tags": {"building": "school"}}
    a = B.resolve(big, 1500, 2.5, 0.9, None, CFG, ev={"anbh": 9.0, "share": 0.8, "small_cell": False})
    assert a["height_method"] == "ghsl_dominant" and 6.0 <= a["eave_height"] <= 9.0


def test_sentinel_roof_halos_are_removed():
    from rasterio.transform import from_origin
    rgb = np.full((40, 40, 3), 80.0)
    rgb[18:22, 18:22] = 240.0  # roof seen from space
    t = from_origin(0, 400, 10, 10)
    out, n = S.remove_roofs(rgb, t, [shapely.box(185, 185, 215, 215)], buffer_m=6)
    assert n > 0 and out.max() == pytest.approx(80.0)


def test_dtm_lowers_objects_only_inside_the_mask(tmp_path):
    import rasterio
    from rasterio.transform import from_origin
    from skvyra.terrain import prepare_dtm
    a = np.full((30, 30), 200.0, np.float32)
    a[10, 10] = 215.0   # a tall building inside the mask
    a[20, 20] = 206.0   # a narrow natural knoll outside the mask
    src = tmp_path / "dsm.tif"
    with rasterio.open(src, "w", driver="GTiff", height=30, width=30, count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(29.0, 50.0, 0.001, 0.001)) as d:
        d.write(a, 1)
    bld = shapely.box(29.0099, 49.9891, 29.0111, 49.9899)  # covers pixel (10, 10)
    rep = prepare_dtm(src, tmp_path / "dtm.tif", [bld])
    with rasterio.open(tmp_path / "dtm.tif") as d:
        dtm = d.read(1)
    assert dtm[10, 10] == pytest.approx(200.0) and dtm[20, 20] == pytest.approx(206.0)
    assert rep["max_lowering_m"] == pytest.approx(15.0, abs=0.01)


def test_landmark_rejects_merged_ml_blob(tmp_path, monkeypatch):
    from skvyra import landmarks as LM
    cat = tmp_path / "landmarks.json"
    lon, lat = FRAME.to_geo(50.0, 50.0)
    cat.write_text(json.dumps({"landmarks": [{"wikidata": "Q1", "name": "Test church", "lonlat": [float(lon), float(lat)], "facade": "church"}]}))
    monkeypatch.setattr(LM, "CATALOGUE", cat)
    monkeypatch.setattr(C, "PROCESSED", tmp_path)
    blob = shapely.union_all([shapely.box(0, 0, 60, 20), shapely.box(0, 20, 20, 90), shapely.box(40, 20, 60, 90)])
    blob = blob.union(shapely.box(20, 40, 40, 60))
    assert LM.match([(blob, {"source_id": "ms:x", "source": "ms_ml"})], FRAME) == {}
    church = shapely.box(40, 40, 60, 70)
    got = LM.match([(church, {"source_id": "osm:way/1", "source": "osm", "tags": {"amenity": "place_of_worship"}})], FRAME)
    assert "osm:way/1" in got


@pytest.mark.parametrize("roof", ["gabled", "hipped", "half-hipped"])
def test_eaves_overhang_extends_roof_not_walls(roof):
    p = shapely.orient_polygons(shapely.box(0, 0, 12, 8))
    attrs = {"roof": roof, "roof_height": 2.5, "eave_height": 3.4, "kind": "house", "chimney": False, "rand": 0.5,
             "overhang": 0.45}
    m = B.building_mesh(p, attrs, FlatTerrain().z, 3.0, lod=0)
    roof_t, _ = m["roof"]
    expected = p.buffer(0.45, join_style="mitre").area
    assert area_xy(roof_t) == pytest.approx(expected, rel=5e-3)        # roof covers footprint + eaves
    walls, _ = m["walls"]
    xy = walls.reshape(-1, 3)[:, :2]
    assert xy[:, 0].min() >= -1e-6 and xy[:, 0].max() <= 12 + 1e-6   # walls stay on the real footprint
    assert roof_t[..., 2].min() < 3.4                                   # eaves drop below the wall top
    assert len(m["extra"]) > 0                                          # fascia board at LOD0


def test_pond_cut_leaves_no_terrain_above_water_inside_outline():
    """Regression (2026-11-12): grid triangles straddling a pond shore rose above the flat water ("saw teeth")."""
    from skvyra import tiles as T
    from skvyra.terrain import Terrain

    class Bowl(Terrain):
        def __init__(self):
            self.step = 10.0

        def lattice(self, i, j):
            x, y = np.asarray(i) * 10.0 - 50, np.asarray(j) * 10.0 - 50
            return 100 + 0.004 * (x ** 2 + y ** 2)

    ter, pond, lvl = Bowl(), Point(50, 50).buffer(33), 104.0
    b = (0, 0, 100, 100)
    tris, _ = ter.grid_mesh(*b, skirt=0, clamp=[(pond, lvl - 0.6)])
    out = T.cut_ponds(tris, [(pond, lvl)], ter, b)
    u, v = out[:, 1, :2] - out[:, 0, :2], out[:, 2, :2] - out[:, 0, :2]
    a2 = u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
    ground = out[np.abs(a2) > 1e-9]
    inside = shapely.contains_xy(pond.buffer(-0.05), ground[..., 0].mean(1), ground[..., 1].mean(1))
    assert not inside.any()                       # no ground left inside the real outline
    assert (a2[np.abs(a2) > 1e-9] > 0).all()      # every ground triangle faces up (not culled)
    assert (np.abs(a2) <= 1e-9).sum() > 0         # bank wall down to the water exists
    assert T.face_up(np.array([[[0, 0, 0], [0, 1, 0], [1, 0, 0]]], float))[0, 0, 0] == 1  # clockwise -> flipped


# ---------------------------------------------------------------------------------------------------
# canopy height model -> tree candidates (Realism & Authenticity 3.0)
# ---------------------------------------------------------------------------------------------------
def _crown(shape, cx, cy, h, r):
    yy, xx = np.indices(shape)
    return h * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * (r / 2) ** 2))


def test_canopy_one_candidate_per_crown_not_per_pixel():
    from skvyra import canopy as CN
    chm = np.maximum(_crown((120, 120), 30, 30, 14.0, 9), _crown((120, 120), 80, 70, 7.0, 6)).astype(np.float32)
    c = CN.detect(chm, 0.0, 120 * CN.PX, CN.PX)
    assert len(c) == 2
    tall = c[np.argmax(c[:, 2])]
    assert abs(tall[0] - 30.5 * CN.PX) < 1.0 and abs(tall[1] - (120 - 30.5) * CN.PX) < 1.0  # north-up grid
    assert 12.5 < tall[2] <= 14.0 and 2.0 < tall[3] <= 0.35 * 14 + 1.0
    # a blocked mask (building) removes the candidate on it
    m = np.zeros(chm.shape, bool)
    m[20:40, 20:40] = True
    assert len(CN.detect(chm, 0.0, 120 * CN.PX, CN.PX, blocked_mask=m)) == 1


def test_canopy_flags_constant_height_fill_but_not_real_canopy():
    from skvyra import canopy as CN
    flat = np.zeros((128, 128), np.float32)
    flat[:64, :64] = 1.0     # model fill: constant plateau
    flat[64:, 64:] = 0.1     # real canopy: rarely flat
    bad = CN.unreliable_blocks(flat, block=32)
    assert bad[0, 0] and bad[1, 1] and not bad[3, 3]


def test_canopy_confidence_and_kind_are_deterministic_appearance_only():
    from skvyra import canopy as CN
    assert CN.confidence(12, "2015-06-24") == "medium"
    assert CN.confidence(12, "2011-04-28") == "low"  # leaf-off imagery
    assert CN.confidence(4, "2015-06-24") == "low"
    houses = [((0.0, 0.0), 5.0)]
    k = [CN.kind_for(3.3, 4.4, 5.0, None, houses) for _ in range(3)]
    assert len(set(k)) == 1 and k[0] in (0, 2)
    assert CN.kind_for(500, 500, 15, None, houses) == 0


def test_rotunda_walls_face_out_and_dome_tops_the_drum():
    spec = {"diameter_m": 7.0, "wall_above_eave_m": 0.6, "drum_diameter_m": 6.0, "drum_h_m": 1.2, "dome_rise_m": 2.1}
    wt, wu, dt, dc = B.rotunda_mesh(10.0, 20.0, 99.5, 100.0, spec, eave=8.4, floor_h=4.0, n=24)
    n = np.cross(wt[:, 1] - wt[:, 0], wt[:, 2] - wt[:, 0])
    mid = wt.mean(axis=1)
    radial = mid[:, :2] - np.array([10.0, 20.0])
    assert ((n[:, :2] * radial).sum(axis=1) > 0).all()  # outward
    assert wu.shape == (len(wt), 3, 2) and len(dc) == len(dt)
    top = dt[:, :, 2].max()
    assert abs(top - (100.0 + 8.4 + 0.6 + 1.2 + 0.18 + 2.1)) < 0.05
