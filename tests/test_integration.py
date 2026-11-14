"""End-to-end pipeline on the synthetic fixture, fully offline (subprocesses with SKVYRA_ROOT=tmp)."""
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
BLENDER = shutil.which("blender") or "/Applications/Blender.app/Contents/MacOS/Blender"


def run(root, *args, check=True):
    env = {**os.environ, "SKVYRA_ROOT": str(root), "PYTHONPATH": str(REPO / "pipeline")}
    p = subprocess.run([sys.executable, *args], cwd=REPO, env=env, capture_output=True, text=True, timeout=600)
    if check and p.returncode != 0:
        raise AssertionError(f"{args} failed ({p.returncode}):\n{p.stdout}\n{p.stderr}")
    return p


def cli(root, *args, check=True):
    return run(root, "-m", "skvyra.cli", *args, "--config", str(root / "config" / "city.json"), check=check)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("fixture")
    run(root, "tests/fixtures/make_fixture.py", str(root))
    cli(root, "all", "--offline", "--workers", "2")
    return root


def read_glb(path):
    data = path.read_bytes()
    magic, ver, total = struct.unpack("<III", data[:12])
    assert magic == 0x46546C67 and ver == 2 and total == len(data)
    jl = struct.unpack("<I", data[12:16])[0]
    js = json.loads(data[20:20 + jl])
    binary = data[20 + jl + 8:]
    return js, binary


def accessor(js, binary, i):
    a = js["accessors"][i]
    bv = js["bufferViews"][a["bufferView"]]
    n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
    arr = np.frombuffer(binary, np.float32, a["count"] * n, bv["byteOffset"])
    return arr.reshape(-1, n) if n > 1 else arr


def tiles_dir(root):
    return root / "assets" / "generated" / "tiles"


def test_normalization_counts_and_quarantine(built):
    rep = json.loads((built / "data" / "processed" / "normalize_report.json").read_text())
    c, s = rep["counts"], rep["stats"]
    assert c["buildings_osm"] == 7          # bowtie repaired into 2 parts; duplicate dropped
    assert c["buildings_ms"] == 1           # ML footprint overlapping OSM dropped, gap filler kept
    assert s["osm_building_duplicate"] == 1
    assert s["osm_building_repaired"] == 1
    assert s["suppressed_sensitive"] == 1   # military area never reconstructed
    reasons = {f["properties"]["reason"].split(":")[0] for f in
               json.loads((built / "data" / "processed" / "quarantine.geojson").read_text())["features"]}
    assert reasons == {"building_mapped_as_node", "unclosed_ring", "too_small"}


def test_every_building_is_in_exactly_one_tile(built):
    idx = json.loads((tiles_dir(built) / "index.json").read_text())
    ids = [b["id"] for t in idx["tiles"] for b in json.loads((tiles_dir(built) / f"{t['id']}.json").read_text())["buildings"]]
    assert len(ids) == len(set(ids)) == 8
    assert not idx["failed"]


def test_height_provenance(built):
    idx = json.loads((tiles_dir(built) / "index.json").read_text())
    metas = {b["sid"]: b for t in idx["tiles"] for b in json.loads((tiles_dir(built) / f"{t['id']}.json").read_text())["buildings"]}
    assert metas["osm:way/101"]["hM"] == "source_levels" and metas["osm:way/101"]["eave"] == pytest.approx(6.4)
    assert metas["osm:relation/201"]["hM"] == "source_height" and metas["osm:relation/201"]["h"] == pytest.approx(15)
    assert metas["osm:way/108"]["roof"] == "shed" and metas["osm:way/108"]["roofM"] == "source_tag"
    ml = [m for m in metas.values() if m["src"] == "ms_ml"]
    assert ml and all(m["conf"] == "inferred" and m["hM"] == "type_default" for m in ml)
    # GHSL never overrides an OSM height tag (the courtyard block sits in the 12 m cell)
    assert metas["osm:relation/201"]["hM"] == "source_height"
    # small houses keep the type default, but the GHSL cell value is still shown as context
    houses = [m for m in metas.values() if m["kind"] == "house" and m["src"] == "osm"]
    assert houses and all(m["hM"] in ("type_default", "source_levels") and m["ghsl"]["anbh"] == pytest.approx(3.5) for m in houses)
    assert all(m.get("hC") for m in metas.values())


def test_glb_files_are_valid_and_aligned(built):
    idx = json.loads((tiles_dir(built) / "index.json").read_text())
    for t in idx["tiles"]:
        x0, y0, x1, y1 = t["b"]
        for lod, f in t["files"].items():
            js, binary = read_glb(tiles_dir(built) / f["u"])
            names = {m["name"] for m in js["meshes"]}
            assert "terrain" in names
            for m in js["meshes"]:
                attrs = m["primitives"][0]["attributes"]
                pos = accessor(js, binary, attrs["POSITION"])
                assert np.isfinite(pos).all()
                assert "NORMAL" not in attrs  # flat-shaded: normals derived on the GPU (smaller tiles)
                assert js["accessors"][attrs["COLOR_0"]]["componentType"] == 5123
                east, north = pos[:, 0], -pos[:, 2]
                pad = 40 if m["name"] in ("walls", "roofs", "details") else 0.01  # buildings belong to one tile whole
                assert east.min() >= x0 - pad and east.max() <= x1 + pad, (t["id"], m["name"])
                assert north.min() >= y0 - pad and north.max() <= y1 + pad, (t["id"], m["name"])
                if m["name"] in ("walls", "roofs"):
                    assert "_FEATURE_ID" in attrs
            if lod == "2":
                assert "road_paved" not in names  # overview LOD: roads are only in the ground texture


def test_buildings_sit_on_terrain(built):
    """No floating buildings: every wall bottom is at or below the terrain directly under it."""
    sys.path.insert(0, str(REPO / "pipeline"))
    os.environ["SKVYRA_ROOT"] = str(built)
    try:
        from skvyra.geo import Frame
        from skvyra.terrain import Terrain
        cfg = json.loads((built / "config" / "city.json").read_text())
        ter = Terrain(built / "data" / "raw" / "dem_copernicus_glo30.tif", Frame(cfg["crs_metric"], cfg["origin_utm"]), step=10, verbose=False)
        idx = json.loads((tiles_dir(built) / "index.json").read_text())
        for t in idx["tiles"]:
            js, binary = read_glb(tiles_dir(built) / t["files"]["0"]["u"])
            for m in js["meshes"]:
                if m["name"] != "walls":
                    continue
                pos = accessor(js, binary, m["primitives"][0]["attributes"]["POSITION"])
                fid = accessor(js, binary, m["primitives"][0]["attributes"]["_FEATURE_ID"])
                for f in np.unique(fid):
                    p = pos[fid == f]
                    bottom = p[:, 1].min()
                    ground = ter.z(p[:, 0], -p[:, 2]).min()
                    assert bottom <= ground + 1e-3, (t["id"], f, p[:3].tolist(), ter.z(p[:3, 0], -p[:3, 2]).tolist(), cfg["origin_utm"])
    finally:
        os.environ.pop("SKVYRA_ROOT", None)


def digest(root):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(tiles_dir(root).glob("*.glb"))}


def test_rebuild_is_deterministic_and_incremental(built):
    before = digest(built)
    out = cli(built, "build", "--offline", "--workers", "2").stderr
    assert "0 failed" in out and f"({len(before) // 3} cached)" in out  # nothing changed -> nothing rebuilt
    cli(built, "build", "--offline", "--force", "--workers", "2")
    assert digest(built) == before  # forced rebuild is byte-identical


def test_overrides_apply_and_conflicts_are_reported(built):
    ov = built / "data" / "overrides"
    ov.mkdir(parents=True, exist_ok=True)
    (ov / "overrides.json").write_text(json.dumps({"version": 1, "buildings": {
        "osm:way/102": {"height": 11, "roof_shape": "hipped", "verified": True, "note": "test"},
        "osm:way/999999": {"height": 30, "note": "feature deleted upstream"}}}))
    out = cli(built, "build", "--offline", "--workers", "2").stderr
    assert "1 override(s) could not be matched" in out
    rep = json.loads((built / "data" / "processed" / "overrides_report.json").read_text())
    assert rep["applied"] == ["osm:way/102"] and rep["conflicts"][0]["source_id"] == "osm:way/999999"
    idx = json.loads((tiles_dir(built) / "index.json").read_text())
    metas = {b["sid"]: b for t in idx["tiles"] for b in json.loads((tiles_dir(built) / f"{t['id']}.json").read_text())["buildings"]}
    b = metas["osm:way/102"]
    assert (b["hM"], b["roof"], b["roofM"], b["conf"]) == ("override", "hipped", "override", "verified")
    (ov / "overrides.json").unlink()
    cli(built, "build", "--offline", "--workers", "2")


def test_failed_tile_is_reported_and_retried_alone(built):
    # corrupt one tile's cache marker: only that tile is rebuilt
    victim = sorted(tiles_dir(built).glob("t*.json"))[0]
    victim.write_text("{}")
    out = cli(built, "build", "--offline", "--workers", "1").stderr
    n = len(list(tiles_dir(built).glob("t*.json")))
    assert f"({n - 1} cached)" in out


def test_geometry_qa_passes_on_fixture(built):
    p = run(built, "scripts/qa_geometry.py", check=False)
    rep = json.loads((built / "data" / "processed" / "qa_report.json").read_text())
    assert p.returncode == 0, p.stdout + p.stderr
    assert rep["floating"] == 0 and rep["trees_inside_buildings"] == 0
    assert rep["buildings_overlapping_paved_roads_gt_1m2"] == 0


def test_offline_without_cache_fails_loudly(tmp_path):
    shutil.copytree(REPO / "config", tmp_path / "config")
    p = cli(tmp_path, "acquire", "--offline", check=False)
    assert p.returncode == 1 and "offline mode" in p.stderr


@pytest.mark.blender
def test_blender_builds_and_saves_scene(built, tmp_path):
    if not Path(BLENDER).exists():
        pytest.skip("Blender not installed")
    idx = json.loads((tiles_dir(built) / "index.json").read_text())
    ids = [t["id"] for t in idx["tiles"] if t["n"]["buildings"]][:3]
    out = tmp_path / "scene.blend"
    env = {**os.environ, "SKVYRA_ROOT": str(built)}
    p = subprocess.run([BLENDER, "-b", "--factory-startup", "--python", str(REPO / "blender/scripts/build_scene.py"), "--",
                        "--tiles", *ids, "--no-render", "--save", str(out)], env=env, capture_output=True, text=True, timeout=600)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    assert "[skvyra-blender] saved" in p.stdout and out.exists() and out.stat().st_size > 10_000
    # export round trip: re-export one imported tile to glTF from Blender and check it parses
    glb = tmp_path / "rt.glb"
    script = ("import bpy; bpy.ops.wm.open_mainfile(filepath=%r); "
              "bpy.ops.export_scene.gltf(filepath=%r, export_format='GLB', use_selection=False)") % (str(out), str(glb))
    p = subprocess.run([BLENDER, "-b", "--factory-startup", "--python-expr", script], capture_output=True, text=True, timeout=600)
    assert p.returncode == 0 and glb.exists()
    js, _ = read_glb(glb)
    names = {n.get("name", "") for n in js["nodes"]}
    assert any(n.endswith("_walls") for n in names) and any(n.endswith("_terrain") for n in names)
