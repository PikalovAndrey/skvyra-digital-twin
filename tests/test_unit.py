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


@pytest.mark.parametrize("area", [80, 601, 2500, 12000])
def test_size_alone_never_makes_a_tagged_public_building_industrial(area):
    """Audit 2026-11-16: the museum (tourism=museum, 964 m2) was drawn as a windowless shed because every
    building over 600 m2 without a building=* type became industrial."""
    for tags in ({"building": "yes", "tourism": "museum"}, {"building": "yes", "amenity": "school"},
                 {"building": "school"}, {"building": "yes", "office": "government"}):
        assert B.classify(tags, area, None)[:2] == ("public", "tag"), tags
        assert B.classify(tags, area, "industrial")[0] == "public", tags  # its own tags beat the land use
        a = B.resolve(props(tags), area, 2.0, 0.9, None, CFG)
        assert a["kind"] == "public" and a["facade"] == "public" and a["roof_mat"] != "corrugated", tags


def test_large_untagged_building_is_unknown_not_industrial_and_keeps_its_height():
    assert B.classify({"building": "yes"}, 1000, None)[:2] == ("unknown", "inferred")
    assert B.classify({"building": "yes"}, 1000, "residential")[0] == "unknown"
    assert B.classify({"building": "yes"}, 400, None)[0] == "house"                    # small: unchanged
    # real industry stays industrial: explicit type, industrial/farm land use, tanks and silos
    assert B.classify({"building": "warehouse"}, 100, "residential")[:2] == ("industrial", "tag")
    assert B.classify({"building": "yes"}, 1000, "industrial")[:2] == ("industrial", "inferred")
    assert B.classify({"building": "yes"}, 200, "farmland")[0] == "industrial"
    assert B.classify({"building": "yes", "man_made": "storage_tank"}, 40, None)[:2] == ("industrial", "tag")
    u = B.resolve(props({"building": "yes"}, "u1"), 1000, 2.5, 0.9, None, CFG)
    i = B.resolve(props({"building": "yes"}, "u1"), 1000, 2.5, 0.9, "industrial", CFG)
    assert (u["kind"], u["facade"]) == ("unknown", "unknown") and u["roof_mat"] in ("slate", "seam")
    # neutral look only: wall height, storeys and roof rise are those of the former size rule
    assert (u["eave_height"], u["levels"], u["roof_height"], u["height_method"]) == \
           (i["eave_height"], i["levels"], i["roof_height"], i["height_method"]) == (6.0, None, i["roof_height"], "type_default")


def test_function_tags_change_the_look_not_the_storeys():
    """Tags read since 2026-11-16 (tourism, office, man_made, building=residential, car_wash) must not change a
    building's wall height or storey count: those stay until a height or storey count is confirmed."""
    cases = [({"building": "yes", "tourism": "museum"}, 964, None), ({"building": "yes", "office": "telecommunication"}, 640, None),
             ({"building": "yes", "man_made": "storage_tank"}, 38, None), ({"building": "residential"}, 232, "commercial"),
             ({"building": "yes", "amenity": "car_wash"}, 452, None)]
    for tags, area, cover in cases:
        new = B.resolve(props(tags, "f1"), area, 1.5, 0.9, cover, CFG)
        before = B.resolve(props({"building": "yes"}, "f1"), area, 1.5, 0.9, cover, CFG)   # what the size rule gave
        assert new["kind"] != before["kind"], tags
        assert (new["eave_height"], new["height_method"]) == (before["eave_height"], before["height_method"]), tags
        # storeys kept, except where they do not apply (a tank is not "1 storey")
        assert new["levels"] == (None if "man_made" in tags else before["levels"]), tags
    ev = {"anbh": 9.0, "share": 0.8}  # GHSL-derived height keeps its value too
    new = B.resolve(props({"building": "yes", "tourism": "museum"}, "f2"), 964, 2.0, 0.9, None, CFG, ev=ev)
    old = B.resolve(props({"building": "yes"}, "f2"), 964, 2.0, 0.9, None, CFG, ev=ev)
    assert new["kind"] == "public" and new["height_method"] == old["height_method"] == "ghsl_dominant"
    assert (new["eave_height"], new["levels"]) == (old["eave_height"], old["levels"])


def test_running_track_line_is_a_band_not_a_filled_oval():
    assert osm.is_area_way({"leisure": "track", "sport": "athletics"}) is False      # line unless area=yes
    assert osm.is_area_way({"leisure": "track", "area": "yes"}) is True
    assert osm.is_area_way({"leisure": "pitch"}) is True
    oval = shapely.Point(0, 0).buffer(40).exterior
    band, w, how = N.track_band(oval, {"leisure": "track"})
    assert (w, how) == (N.TRACK_WIDTH_M, "estimate")
    assert band.area == pytest.approx(oval.length * w, rel=0.02) and not band.contains(shapely.Point(0, 0))
    assert N.track_band(oval, {"leisure": "track", "width": "7 m"})[1:] == (7.0, "source")
    assert N.cover_class({"leisure": "track", "area": "yes"}) == "track"
    assert N.cover_class({"leisure": "pitch", "surface": "paved"}) == "court"
    assert N.cover_class({"leisure": "pitch", "surface": "grass"}) == "pitch"
    assert N.cover_class({"leisure": "stadium"}) == "stadium"   # stands and paths: no grass paint over all of it


def test_sport_surfaces_paint_in_order_with_photo_colours():
    pitch = shapely.box(-40, -25, 40, 25)
    track, _, _ = N.track_band(shapely.box(-43, -28, 43, 28).exterior, {})
    court = shapely.box(44, -8, 58, 8)
    stadium = shapely.box(-70, -60, 70, 60)
    areas = [(stadium, "stadium", None), (court, "court", "#96443f"), (pitch, "pitch", None), (track, "track", "#a59483")]
    import io
    from PIL import Image
    jpg = S.ground_texture((-70, -60, 70, 60), 140, areas, None, {}, [], 95, "t", base=None)
    px = np.asarray(Image.open(io.BytesIO(jpg)).convert("RGB")).astype(int)
    at = lambda x, y: px[int(60 - y), int(x + 70)]
    r, g, b = at(51, 0)
    assert r > g + 40 and r > b + 40                       # court: the photo's red, painted over the track
    r, g, b = at(0, 0)
    assert g > r and g > b                                 # pitch stays grass inside the track
    r, g, b = at(0, 28)
    assert r > g > b and abs(r - 165) < 45                 # the ring: grey-brown unpaved


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


def test_composite_landmark_cuts_real_footprint_and_details_face_outward():
    # synagogue-like outline: 51 x 12 m block with a 9 m wide projection to the south at the west end
    fp = Polygon([(0, 0), (9, 0), (9, 5.6), (51, 5.6), (51, 17.8), (0, 17.8)])
    spec = {"axis_from": "west", "front": "south", "split_m": [9.0, 27.0],
            "parts": [{"roof": "gabled", "eave_m": 5.9, "rise_m": 2.8, "frieze": False}, {"roof": "hipped", "eave_m": 5.6, "rise_m": 2.2, "frieze": False},
                      {"roof": "hipped", "eave_m": 4.5, "rise_m": 2.0, "frieze": False}],  # friezes off: isolate the door
            "features": [{"type": "door", "part": 1, "at_m": 16.0, "sill_m": 2.1}]}
    parts = B.composite_parts(fp, spec)
    assert len(parts) == 3 and abs(sum(g.area for g, _ in parts) - fp.area) < 1e-6  # real outline kept exactly
    assert parts[0][0].bounds[1] == 0.0 and parts[0][0].bounds[2] <= 9.0 + 1e-9    # the projection is the west block
    tris, cols = B.composite_details(fp, spec, lambda x, y: np.zeros(np.shape(x)), lod=1, wall_rgb=[0.6, 0.2, 0.1])
    door = tris[np.isclose(tris[:, :, 2].max(axis=1), 2.1 + 2.3) | np.isclose(tris[:, :, 2].min(axis=1), 2.1)]
    c = door.reshape(-1, 3).mean(axis=0)
    nrm = np.cross(door[:, 1] - door[:, 0], door[:, 2] - door[:, 0])
    assert len(door) and ((nrm * (door.mean(axis=1) - c)).sum(axis=1) > 0).all()     # every face points out of the box
    assert abs(c[1] - 5.6) < 0.1 and abs(c[0] - 16.0) < 0.1                           # on the front of the central block


def test_quality_layer_flags_suspicions_never_facts():
    from skvyra import quality as Q
    assert Q.classify(0.9, True, 0.9)[0] == "covered"                    # an outline nearby wins
    cls, conf, why = Q.classify(0.5, False, 0.3)
    assert cls == "suspect_gap" and conf == "medium" and any("trees" in r for r in why)
    assert Q.classify(0.25, False, 0.0)[:2] == ("suspect_gap", "low")
    assert Q.classify(0.1, False, 0.0)[0] == "insufficient"
    cls, conf, why = Q.classify(0.0, False, 0.0)
    assert cls == "no_signs" and conf == "low" and any("can be missed" in r for r in why)  # never 'confirmed empty'


# ---------------------------------------------------------------------------------------------------
# local building outlines (geometry overrides, Authenticity 3.3)
# ---------------------------------------------------------------------------------------------------
def _fp_doc(features):
    return {"type": "FeatureCollection", "features": features}


def _fp(fid, ring_local, status="confirmed", **props):
    ll = [list(FRAME.to_geo(x, y)) for x, y in ring_local]
    ll = [[float(a), float(b)] for a, b in ll]
    p = {"id": fid, "status": status, "confidence": props.pop("confidence", "high"), "method": "synthetic test",
         "date": "2026-11-14", "evidence": [{"url": "https://example.org/test", "license": "test"}], **props}
    if status == "confirmed":
        p.setdefault("approval", {"by": "test", "date": "2026-11-14", "basis": "site_survey", "checks": "synthetic"})
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ll]}, "properties": p}


def _sq(cx, cy, s):
    return [(cx - s, cy - s), (cx + s, cy - s), (cx + s, cy + s), (cx - s, cy + s), (cx - s, cy - s)]


def test_local_outlines_validate_apply_and_never_leak_approximate(tmp_path, monkeypatch):
    from skvyra import overrides as OVR
    monkeypatch.setattr(C, "PROCESSED", tmp_path)
    src = [(shapely.Polygon(_sq(0, 0, 5)), {"source_id": "osm:way/1", "source": "osm", "id": "a"}),
           (shapely.Polygon(_sq(100, 0, 5)), {"source_id": "ms:abc", "source": "ms_ml", "id": "b"})]
    bowtie = [(200, 0), (210, 10), (210, 0), (200, 10), (200, 0)]
    path = tmp_path / "footprints.geojson"
    path.write_text(json.dumps(_fp_doc([
        _fp("local:gap", _sq(50, 50, 6)),                                    # empty spot -> applied
        _fp("local:draft", _sq(-50, 50, 6), status="approximate", confidence="low", evidence=[]),  # review only
        _fp("local:bow", bowtie),                                            # self-intersection -> conflict
        _fp("local:over", _sq(7, 0, 5)),                                     # partly overlaps osm:way/1 -> conflict
        _fp("local:dup", _sq(50.5, 50, 6)),                                  # duplicates local:gap -> conflict
        _fp("local:repl", _sq(100, 0, 6), replaces=[{"source_id": "ms:abc", "anchor": list(map(float, FRAME.to_geo(100, 0)))}]),
    ])))
    out, rep = OVR.apply_footprints(src, FRAME, shapely.Polygon(_sq(0, 0, 1000)), [], lambda s: s, path)
    sids = {p["source_id"] for _, p in out}
    assert rep["applied"] == ["local:gap", "local:repl"] and rep["review"] == ["local:draft"]
    assert "local:draft" not in sids and "ms:abc" not in sids and "osm:way/1" in sids  # explicit replacement only
    reasons = {c["id"]: c["reason"] for c in rep["conflicts"]}
    assert "Self-intersection" in reasons["local:bow"] and "osm:way/1" in reasons["local:over"] and "duplicates" in reasons["local:dup"]
    assert json.loads((tmp_path / "review_footprints.geojson").read_text())["features"][0]["properties"]["id"] == "local:draft"
    # after a source update the replaced ML outline has a new id: re-matched by anchor, local edit kept
    src2 = [src[0], (shapely.Polygon(_sq(100.3, 0, 5)), {"source_id": "ms:new", "source": "ms_ml", "id": "c"})]
    out2, rep2 = OVR.apply_footprints(src2, FRAME, shapely.Polygon(_sq(0, 0, 1000)), [], lambda s: s, path)
    assert "local:repl" in rep2["applied"] and "ms:new" not in {p["source_id"] for _, p in out2}
    # if someone maps the building in OSM, the human-mapped source wins and the local outline is reported
    src3 = src + [(shapely.Polygon(_sq(50, 50, 6)), {"source_id": "osm:way/9", "source": "osm", "id": "d"})]
    _, rep3 = OVR.apply_footprints(src3, FRAME, shapely.Polygon(_sq(0, 0, 1000)), [], lambda s: s, path)
    assert rep3["superseded"][0]["id"] == "local:gap"


def test_local_outline_schema_errors(tmp_path):
    from skvyra import overrides as OVR
    path = tmp_path / "f.geojson"
    for bad, msg in ((_fp("bad id", _sq(0, 0, 5)), "local:"), (_fp("local:xx", _sq(0, 0, 5), status="maybe"), "status"),
                     (_fp("local:yy", _sq(0, 0, 5), evidence=[]), "evidence"),
                     (_fp("local:zz", _sq(0, 0, 5), evidence=[{"url": "https://example.org/x"}]), "license"),
                     (_fp("local:link-only", _sq(0, 0, 5), approval={}), "approval"),
                     (_fp("local:photo", _sq(0, 0, 5), approval={"by": "x", "date": "2026-11-14", "basis": "photo", "checks": "x"}), "basis")):
        path.write_text(json.dumps(_fp_doc([bad])))
        with pytest.raises(ValueError, match=msg):
            OVR.load_footprints(path)


# ---------------------------------------------------------------------------------------------------
# Authenticity 3.4: photo-estimated landmark parts (pediments / attics, bands, free-standing monuments)
# ---------------------------------------------------------------------------------------------------
def _outward_share(tris, centre_xy):
    """Share of triangles whose normal points away from the vertical axis through centre_xy (or up)."""
    t = np.asarray(tris, float)
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    c = t.mean(1)
    radial = n[:, 0] * (c[:, 0] - centre_xy[0]) + n[:, 1] * (c[:, 1] - centre_xy[1])
    return float(np.mean((radial > 1e-9) | (n[:, 2] > 1e-9)))


def test_monument_and_pediment_faces_point_outward():
    from skvyra import buildings as B
    for facing in (0, 90, 215):
        t, c = B.monument_mesh(5.0, 7.0, 100.0, {"facing_deg": facing, "stack": [
            {"w": 2.4, "d": 2.4, "h": 0.35, "colour": "#333333"}, {"w": 0.6, "d": 0.55, "h": 2.6, "colour": "#222222"}],
            "bust": {"w": 0.8, "h": 0.95}}, lod=0)
        assert len(t) == len(c) and _outward_share(t, (5.0, 7.0)) == 1.0
        assert t[..., 2].max() == pytest.approx(100.0 + 0.35 + 2.6 + 0.95)
        t1, _ = B.monument_mesh(5.0, 7.0, 100.0, {"stack": [{"w": 1, "d": 1, "h": 1, "colour": "#333333"}], "bust": {"w": 0.8, "h": 0.9}}, lod=1)
        assert t1[..., 2].max() == pytest.approx(101.0)  # no bust beyond LOD0
    # a pediment on a south wall: the tympanum faces south, nothing faces down, the apex is at eave + rise
    for shape_ in ("triangle", "ogee"):
        t, c = B.pediment_tris(np.array([0.0, 0.0]), np.array([1.0, 0.0]), np.array([0.0, -1.0]), 10.0,
                               {"width_m": 8, "rise_m": 2, "shape": shape_, "oculus_r": 0.4}, [1, 1, 1])
        t = np.asarray(t)
        n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        assert (n[:, 2] < -1e-9).sum() == 0 and (n[:, 1] < -1e-9).sum() > (n[:, 1] > 1e-9).sum()
        assert t[..., 2].max() == pytest.approx(12.0 + 0.12, abs=0.05)


def test_wall_frame_and_facade_features_follow_the_nearest_wall():
    from skvyra import buildings as B
    sq = shapely.Polygon([(0, 0), (20, 0), (20, 10), (0, 10)])
    base, u, n = B.wall_frame(sq, (10.0, -3.0))           # point in front of the south wall
    assert base == pytest.approx([10.0, 0.0]) and n == pytest.approx([0.0, -1.0])
    base, u, n = B.wall_frame(sq, (24.0, 5.0))            # east wall
    assert base == pytest.approx([20.0, 5.0]) and n == pytest.approx([1.0, 0.0])
    feats = [{"type": "pediment", "at_lonlat": [10.0, -3.0], "width_m": 6, "rise_m": 1.5},
             {"type": "band", "below_eave_m": [0.9, 0.3], "colour": "#eeeeee"}]
    t, c = B.facade_features(sq, feats, lambda x, y: (x, y), lambda x, y: np.zeros_like(np.asarray(x, float)), 4.0, [1, 1, 1], lod=1)
    assert len(t) == len(c) > 0 and t[..., 2].max() == pytest.approx(4.0 + 1.5 + 0.12, abs=0.05)
    t2, _ = B.facade_features(sq, feats, lambda x, y: (x, y), lambda x, y: np.zeros_like(np.asarray(x, float)), 4.0, [1, 1, 1], lod=2)
    assert len(t2) == 0  # overview LOD: no details


def test_landmark_photo_overrides_use_floor_bay_and_kind():
    from skvyra import buildings as B
    cfg = {"seed": 1, "floor_height_m": 3.0}
    props = {"id": "x", "tags": {"building": "yes"}}
    a = B.resolve(props, 900.0, 2.0, 0.95, None, cfg, landmark={"kind": "public", "levels": 1, "floor_h": 4.0, "bay_m": 2.9,
                                                                 "facade": "tiled", "match": "exact"})
    assert a["kind"] == "public" and a["kind_method"] == "photo_estimate" and a["floor_h"] == 4.0 and a["bay_m"] == 2.9
    assert a["eave_height"] == pytest.approx(4.4) and B.FACADE[a["facade"]] == 9
    p = B.resolve(props, 900.0, 2.0, 0.95, None, cfg, landmark={"kind": "public", "levels": 1, "match": "probable"})
    assert p["kind_method"] != "photo_estimate"  # an unconfirmed match never changes the appearance


def test_composite_frieze_and_door_render_together_at_lod1():
    # regression (3.4): friezes now render at LOD1 too; the door at the same height as a frieze edge must still be
    # emitted, distinguishable by colour, and every frieze / cornice / door face must point out of the building
    fp = Polygon([(0, 0), (30, 0), (30, 12), (0, 12)])
    spec = {"axis_from": "west", "front": "south", "split_m": [15.0],
            "parts": [{"roof": "gabled", "eave_m": 4.5, "rise_m": 2.0, "frieze_colour": "#ff0000"},
                      {"roof": "hipped", "eave_m": 4.5, "rise_m": 2.0}],
            "features": [{"type": "door", "part": 0, "at_m": 7.0, "sill_m": 2.1, "width_m": 1.2, "height_m": 2.3, "colour": "#00ff00"}]}
    for lod in (0, 1):
        tris, cols = B.composite_details(fp, spec, lambda x, y: np.zeros(np.shape(x)), lod=lod, wall_rgb=[0.5, 0.5, 0.5])
        lin = lambda h: np.asarray(B.srgb_to_linear(B.hex_rgb(h)))
        is_red = np.all(np.isclose(cols, lin("#ff0000"), atol=1e-6), axis=1)
        is_green = np.all(np.isclose(cols, lin("#00ff00"), atol=1e-6), axis=1)
        assert is_red.sum() > 0 and is_green.sum() == 10           # frieze + projecting cornice; door box (4 sides + top)
        door = tris[is_green]
        assert door[..., 2].max() == pytest.approx(2.1 + 2.3) and door[..., 2].min() == pytest.approx(2.1)
        assert abs(door[..., 0].mean() - 7.0) < 0.1 and door[..., 1].mean() < 0.1      # on the south front
        fr = tris[is_red]
        n = np.cross(fr[:, 1] - fr[:, 0], fr[:, 2] - fr[:, 0])
        c = fr.mean(axis=1)
        out2 = np.column_stack([np.where(c[:, 0] < 0.2, -1, np.where(c[:, 0] > 29.8, 1, 0)),
                                np.where(c[:, 1] < 0.2, -1, np.where(c[:, 1] > 11.8, 1, 0))])
        side = (n[:, :2] * out2).sum(axis=1) > 0
        assert (side | (n[:, 2] > 1e-9)).all()                    # outward walls of the band, cornice tops face up
        assert fr[..., 2].min() == pytest.approx(4.5 - 0.55) and fr[..., 2].max() == pytest.approx(4.5 - 0.1)


def test_pack_demo_removes_internal_files_gzips_tiles_and_flags_leaks(tmp_path, monkeypatch):
    import gzip as _gz
    import importlib.util
    spec = importlib.util.spec_from_file_location("pack_demo", C.ROOT / "scripts" / "pack_demo.py")
    pd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pd)
    out = tmp_path / "dist-demo"
    (out / "tiles").mkdir(parents=True)
    (out / "blender").mkdir()
    (out / "index.html").write_text("<html></html>")
    (out / "review.json").write_text("{}")
    (out / "blender" / "centre.blend").write_bytes(b"x")
    glb = b"glTF" + bytes(4000)
    (out / "tiles" / "t+0+0.lod0.glb").write_bytes(glb)
    monkeypatch.setattr(pd, "OUT", out)
    monkeypatch.setattr(pd, "ROOT", tmp_path)
    assert pd.main() == 1  # no licence notices in the repository: refuse
    (tmp_path / "LICENSES").mkdir()
    for n in pd.LICENCE_FILES:
        (tmp_path / "LICENSES" / n).write_text(n)
    assert pd.main() == 0
    assert all((out / "LICENSES" / n).exists() for n in pd.LICENCE_FILES)  # ODbL / Copernicus / CDLA notices ship
    rules = {r["source"]: r["headers"] for r in json.loads((out / "vercel.json").read_text())["headers"]}
    assert "immutable" in str(rules["/assets/(.*)"]) and rules["/tiles/index.json"][0]["value"] == "no-cache"
    assert list(rules)[-1] == "/tiles/index.json"  # Vercel: the last matching rule wins over /tiles/(.*)
    assert (out / ".vercelignore").read_text().split() == ["_headers"]
    packed = (out / "tiles" / "t+0+0.lod0.glb").read_bytes()
    assert packed[:2] == b"\x1f\x8b" and _gz.decompress(packed) == glb and len(packed) < len(glb)
    assert not (out / "review.json").exists() and not (out / "blender").exists()
    assert "immutable" in (out / "_headers").read_text()
    assert "404" in (out / "404.html").read_text()  # a missing file is a real 404, not the index page (Pages SPA mode)
    assert set(json.loads((out / "version.json").read_text())) == {"commit", "dirty", "tiles_generated_at"}
    assert pd.main() == 0 and (out / "tiles" / "t+0+0.lod0.glb").read_bytes() == packed  # idempotent
    (out / "search.json").write_text('{"path": "/Users/someone/x"}')
    assert pd.main() == 1  # a local path leaks into the public build


def test_preview_preflight_blocks_every_unsafe_cloudflare_setup(monkeypatch):
    """scripts/preflight_preview.py remote checks against a fake Cloudflare API: the good setup passes; a wrong
    production branch, a production deployment, a Git source, missing Access, a non-e-mail or non-allow policy, an
    unapproved address each fail; after the upload a public deployment URL, alias or production URL fails."""
    import copy
    import importlib.util
    spec = importlib.util.spec_from_file_location("preflight_preview", C.ROOT / "scripts" / "preflight_preview.py")
    pf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pf)
    ok = {
        "account": {"success": True, "result": {"name": "Demo account"}},
        "project": {"result": {"name": "skv", "subdomain": "skv.pages.dev", "production_branch": "production", "source": None}},
        "deployments": {"result": []},
        "apps": {"result": [{"name": "skv previews", "domain": "*.skv.pages.dev", "policies": [
            {"name": "testers", "decision": "allow", "include": [{"email": {"email": "a@example.org"}}, {"email": {"email": "b@example.org"}}]}]}]},
        "idps": {"result": [{"type": "onetimepin"}]},
        "preview": (302, {"Location": "https://team.cloudflareaccess.com/cdn-cgi/access/login/preview.skv.pages.dev"}, b""),
        "production": (404, {}, b"not found"),
    }

    def run(world):
        def http(url, token=None):
            if url.startswith(pf.API):
                key = ("deployments" if "/deployments" in url else "project" if "/pages/projects/" in url else
                       "apps" if url.endswith("/access/apps") else "idps" if "identity_providers" in url else "account")
                return 200, {}, json.dumps(world[key]).encode()
            return world["preview"] if url.startswith("https://preview.") else world["production"]
        monkeypatch.setattr(pf, "http", http)
        fails = []
        pf.remote_checks(lambda good, what: fails.append(what) if not good else None, "acc", "skv", "preview",
                         ["a@example.org", "b@example.org", "c@example.org"], "token")
        return fails

    assert run(ok) == []
    def broken(change):
        w = copy.deepcopy(ok)
        change(w)
        return run(w)
    pol = lambda w: w["apps"]["result"][0]["policies"][0]
    assert broken(lambda w: w["project"]["result"].update(production_branch="preview"))
    assert broken(lambda w: w["project"]["result"].update(source={"type": "github"}))
    assert broken(lambda w: w.update(deployments={"result": [{"environment": "production"}]}))
    assert broken(lambda w: w.update(apps={"result": []}))
    assert broken(lambda w: pol(w)["include"].append({"everyone": {}}))
    assert broken(lambda w: pol(w)["include"].append({"email_domain": {"domain": "example.org"}}))
    assert broken(lambda w: pol(w)["include"].append({"email": {"email": "stranger@example.com"}}))
    assert broken(lambda w: pol(w).update(decision="bypass"))
    assert broken(lambda w: w["apps"]["result"][0].update(domain="preview.skv.pages.dev"))  # alias only, not hash URLs
    # before the first upload the preview hosts do not exist: no HTTP probe may block it
    assert broken(lambda w: w.update(preview=(404, {}, b"no deployment"))) == []

    # after the upload: the unique deployment URL and every alias must redirect to Access, production stays empty
    head = "f" * 40
    dep = {"id": "new-1", "url": "https://ab12cd34.skv.pages.dev", "aliases": ["https://preview.skv.pages.dev"], "environment": "preview",
           "deployment_trigger": {"metadata": {"branch": "preview", "commit_hash": head}}}
    access = (302, {"Location": "https://team.cloudflareaccess.com/cdn-cgi/access/login/x"}, b"")
    after = {"project": ok["project"], "preview": {"result": [dep]}, "production": {"result": []},
             "https://ab12cd34.skv.pages.dev/": access, "https://preview.skv.pages.dev/": access,
             "https://skv.pages.dev/": (404, {}, b"")}

    def run_after(world):
        def http(url, token=None):
            if url.startswith(pf.API):
                key = "production" if "env=production" in url else "preview" if "env=preview" in url else "project"
                return 200, {}, json.dumps(world[key]).encode()
            return world[url]
        monkeypatch.setattr(pf, "http", http)
        fails = []
        code = pf.after_upload_checks(lambda good, what: fails.append(what) if not good else None, "acc", "skv", "preview", head, set(), "token")
        assert (code == 0) == (fails == [])
        return fails

    assert run_after(after) == []
    public = (200, {}, b'<html><div id="root"></div>')
    for change in ({"https://ab12cd34.skv.pages.dev/": public}, {"https://preview.skv.pages.dev/": public},
                   {"https://skv.pages.dev/": public}, {"production": {"result": [{"environment": "production"}]}},
                   {"preview": {"result": []}}):
        assert run_after({**after, **change}), change


def test_licence_files_scope():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    lic = (root / "LICENSE").read_text()
    assert lic.startswith("MIT License") and "Permission is hereby granted" in lic
    notice = (root / "LICENSES" / "NOTICE.txt").read_text()
    for must in ("ODbL", "do not incur any liability", "NOT relicensed under the ODbL", "Collective Database",
                 "MIT licence", "CDLA-Permissive-2.0.txt", "changes made"):
        assert must in notice, must
    assert "Community Data License Agreement" in (root / "LICENSES" / "CDLA-Permissive-2.0.txt").read_text()


@pytest.mark.parametrize("highway", ["primary", "footway"])
def test_bridge_deck_faces_up_meets_road_and_clears_ground(highway):
    """The deck was wound clockwise (culled = invisible: every bridge was a gap in the road, 2026-11-15)."""
    ter = Slope()
    line = shapely.LineString([(10, 10), (30, 12), (45, 20)])  # curved
    p = {"highway": highway, "bridge": "yes"}
    deck, struct = S.bridge_mesh(line, p, ter, 0.12)
    for t in (deck, struct):
        assert len(t) and np.isfinite(t).all()
    n = np.cross(deck[:, 1] - deck[:, 0], deck[:, 2] - deck[:, 0])
    assert (n[:, 2] > 0).all()  # every deck triangle faces up
    w, _ = S.road_width(p)
    v = deck.reshape(-1, 3)
    lift = 0.12 + (0.02 if S.road_category(p) == "paved" else 0)
    g = ter.z(v[:, 0], v[:, 1]) + lift
    assert (v[:, 2] >= g - 1e-6).all()  # never below the draped road level
    assert v[:, 2].max() - g.max() < 0.7  # no floating deck
    for end in (line.coords[0], line.coords[-1]):  # joints: exactly the draped road height, no step
        near = v[np.hypot(v[:, 0] - end[0], v[:, 1] - end[1]) < w / 2 + 1e-6]
        assert len(near) >= 2
        assert np.abs(near[:, 2] - (ter.z(near[:, 0], near[:, 1]) + lift)).max() < 0.05
    # structure: underside faces down, something rises above the deck (parapet / railing), all inside the width
    sn = np.cross(struct[:, 1] - struct[:, 0], struct[:, 2] - struct[:, 0])
    assert (sn[:, 2] < -1e-9).any() and struct[..., 2].max() > v[:, 2].max() + 0.8
    assert line.buffer(w / 2 + 0.05, cap_style="flat").buffer(1e-3).covers(shapely.MultiPoint(struct.reshape(-1, 3)[:, :2]))
    if highway == "primary":  # outer faces (fascia + parapet) point away from the road, inner parapet faces towards it
        side = np.abs(sn[:, 2]) < 1e-6 * np.linalg.norm(sn, axis=1)
        c, ns = struct[side].mean(1), sn[side]
        foot_pt = np.array([line.interpolate(line.project(shapely.Point(x, y))).coords[0] for x, y in c[:, :2]])
        away = ((c[:, :2] - foot_pt) * ns[:, :2]).sum(1)
        dist = np.hypot(*(c[:, :2] - foot_pt).T)
        outer, inner = np.abs(dist - w / 2) < 0.05, np.abs(dist - (w / 2 - 0.25)) < 0.05
        assert outer.sum() > 4 and inner.sum() > 4
        assert (away[outer] > 0).all() and (away[inner] < 0).all()


# ---------------------------------------------------------------------------------------------------
# Central Buildings Realism (2026-11-17): landmarks without a Wikidata item, entrance canopy, standing statue
# ---------------------------------------------------------------------------------------------------
def test_landmark_bound_by_source_id_and_rematched_by_anchor(tmp_path, monkeypatch):
    from skvyra import landmarks as LM
    lon, lat = FRAME.to_geo(15.0, 10.0)
    entry = {"source_id": "osm:way/7", "anchor": [float(lon), float(lat)], "name": "Office block", "kind": "public"}
    cat = tmp_path / "landmarks.json"
    cat.write_text(json.dumps({"landmarks": [entry]}))
    monkeypatch.setattr(LM, "CATALOGUE", cat)
    monkeypatch.setattr(C, "PROCESSED", tmp_path)
    near = (shapely.box(40, 0, 60, 20), {"source_id": "osm:way/8", "source": "osm"})  # bigger and closer to nothing
    got = LM.match([(shapely.box(0, 0, 30, 20), {"source_id": "osm:way/7", "source": "osm"}), near], FRAME)
    assert set(got) == {"osm:way/7"} and got["osm:way/7"]["match"] == "source_id"
    # the OSM way was replaced: the outline that contains the anchor takes the landmark, never a neighbour
    got = LM.match([(shapely.box(0, 0, 30, 20), {"source_id": "osm:way/99", "source": "osm"}), near], FRAME)
    assert set(got) == {"osm:way/99"}
    # nothing contains the anchor: not applied (and reported), no proximity guess
    assert LM.match([near], FRAME) == {}
    assert json.loads((tmp_path / "landmarks_report.json").read_text())[0]["matched"] is None


def test_composite_canopy_is_a_slab_seen_from_below_with_posts_on_the_ground():
    fp = Polygon([(0, 0), (40, 0), (40, 12), (0, 12)])
    spec = {"axis_from": "west", "front": "south", "split_m": [], "parts": [{"roof": "flat", "eave_m": 13.0, "rise_m": 0.0, "frieze": False}],
            "features": [{"type": "canopy", "part": 0, "at_m": 20.0, "width_m": 12.0, "depth_m": 3.0, "height_m": 4.0,
                          "thick_m": 0.3, "colour": "#0000ff", "posts": True}]}
    tris, cols = B.composite_details(fp, spec, lambda x, y: np.zeros(np.shape(x)), lod=1, wall_rgb=[0.5, 0.5, 0.5])
    blue = np.all(np.isclose(cols, np.asarray(B.srgb_to_linear(B.hex_rgb("#0000ff"))), atol=1e-6), axis=1)
    t = tris[blue]
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    assert t[..., 2].max() == pytest.approx(4.0)
    assert (n[:, 2] < -1e-9).sum() == 2                       # the underside, and only it, faces down
    assert t[..., 1].min() == pytest.approx(-3.0) and t[..., 1].max() == pytest.approx(0.0)   # out from the south wall
    assert t[..., 0].min() == pytest.approx(14.0) and t[..., 0].max() == pytest.approx(26.0)  # centred on at_m
    assert t[..., 2].min() < 0.0                                # posts reach below ground


def test_monument_standing_figure_height_and_lod():
    from skvyra import buildings as B
    spec = {"facing_deg": 37, "stack": [{"w": 6.1, "d": 3.4, "h": 0.3, "colour": "#888888"}, {"w": 1.3, "d": 1.3, "h": 2.2, "colour": "#666666"}],
            "figure": {"w": 1.8, "h": 4.5, "colour": "#95543f"}}
    for lod in (0, 1):
        t, c = B.monument_mesh(0.0, 0.0, 200.0, spec, lod)
        assert len(t) == len(c) and t[..., 2].max() == pytest.approx(200.0 + 0.3 + 2.2 + 4.5)
        assert _outward_share(t, (0.0, 0.0)) == 1.0
        xy = t.reshape(-1, 3)[:, :2]
        assert np.hypot(xy[:, 0], xy[:, 1]).max() <= np.hypot(3.05, 1.7) + 1e-6   # all within the platform
    t2, _ = B.monument_mesh(0.0, 0.0, 200.0, spec, 2)
    assert t2[..., 2].max() == pytest.approx(202.5)            # overview LOD: pedestal only


def test_office_block_facade_style_is_registered_everywhere():
    import re
    from pathlib import Path
    assert B.FACADE["tiled_ribbon"] == 11
    root = Path(__file__).resolve().parents[1]
    shader = (root / "apps/web/src/scene/materials.ts").read_text()
    assert "st == 11" in shader and re.search(r"st == 8 \|\| st == 9 \|\| st == 11", shader)
    assert "11: 0.33" in (root / "blender/scripts/build_scene.py").read_text()


def test_monument_figure_lofted_from_traced_rings():
    from skvyra import buildings as B
    rings = [[0.0, 0.97, 0.78, 0.54], [2.4, 0.82, 0.78, 0.47], [3.4, 1.32, 1.05, 0.45], [3.66, 0.23, 0.35, 0.25], [4.5, 0.02, 0.22, 0.11]]
    spec = {"facing_deg": 37, "stack": [{"w": 2.1, "d": 1.8, "h": 2.0, "colour": "#847a70"}], "figure": {"colour": "#93503a", "rings": rings}}
    t0, c0 = B.monument_mesh(0.0, 0.0, 200.0, spec, 0)
    t1, _ = B.monument_mesh(0.0, 0.0, 200.0, spec, 1)
    assert len(t0) == len(c0) and len(t1) < len(t0)                   # fewer sides at LOD1
    assert t0[..., 2].max() == pytest.approx(206.5) and _outward_share(t0, (0.0, 0.0)) == 1.0
    # the cape spreads to the statue's right (+u, east when it faces north): asymmetric extents survive
    tn, _ = B.monument_mesh(0.0, 0.0, 0.0, {"facing_deg": 0, "stack": [], "figure": {"rings": rings}}, 0)
    assert tn[..., 0].max() == pytest.approx(1.32) and tn[..., 0].min() == pytest.approx(-1.05)


def test_roof_parts_override_gives_one_roof_over_an_irregular_outline(tmp_path, monkeypatch):
    """Owner feedback 2026-11-17 (ms:edc91edf970c): a Z-shaped ML outline split into 4 roof parts showed eave strips
    dropping across the walls and cut gables; the override `roof_parts: 1` keeps one roof over the unchanged outline."""
    from skvyra import buildings as B, overrides as OVR
    z = Polygon([(0, 0), (9, 0), (9, 6), (15, 6), (15, 15), (6, 15), (6, 9), (0, 9)])  # Z-like rectilinear plan
    props, cfg = {"id": "zz", "tags": {"building": "yes"}}, {"seed": 1, "floor_height_m": 3.0}
    base = B.resolve(props, z.area, 1.0, 0.8, None, cfg)
    one = B.resolve(props, z.area, 1.0, 0.8, None, cfg, override={"roof_parts": 1})
    assert base["decompose"] and not one["decompose"]
    assert len(B.plan(z, {**one, "roof": "half-hipped"})[0]) == 1
    assert {k: v for k, v in one.items() if k != "decompose"} == {k: v for k, v in base.items() if k != "decompose"}
    p = tmp_path / "overrides.json"
    p.write_text(json.dumps({"version": 1, "buildings": {"ms:x": {"roof_parts": 2}}}))
    monkeypatch.setattr(C, "OVERRIDES", p)
    with pytest.raises(ValueError, match="roof_parts"):
        OVR.load()
