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
    assert len(ids) == len(set(ids)) == 9  # 8 from the sources + 1 confirmed local outline
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


def test_suppressed_outline_keeps_its_slot_so_neighbours_keep_their_feature_ids(built):
    """Regression (Centre Realism 2026-11-17): suppressing one outline shifted the feature ids of every later building
    in its tile, and with them the per-building window pattern (curtains, frames, blank windows) seeded by the id."""
    idx = json.loads((tiles_dir(built) / "index.json").read_text())
    tid = next(t["id"] for t in idx["tiles"] if t["n"]["buildings"] >= 3)
    before = [b["sid"] for b in json.loads((tiles_dir(built) / f"{tid}.json").read_text())["buildings"]]
    victim = before[1]
    ov = built / "data" / "overrides"
    ov.mkdir(parents=True, exist_ok=True)
    (ov / "overrides.json").write_text(json.dumps({"version": 1, "buildings": {victim: {"suppress": True, "note": "test"}}}))
    try:
        cli(built, "build", "--offline", "--workers", "2")
        meta = json.loads((tiles_dir(built) / f"{tid}.json").read_text())
        assert [b["sid"] for b in meta["buildings"]] == before          # same order, same index for everyone
        assert meta["buildings"][1].get("suppressed") is True and "h" not in meta["buildings"][1]
        assert meta["index"]["n"]["buildings"] == len(before) - 1
        js, binary = read_glb(tiles_dir(built) / meta["files"]["0"]["u"])
        fids = set()
        for m in js["meshes"]:
            at = m["primitives"][0]["attributes"]
            if "_FEATURE_ID" in at:
                fids |= set(np.unique(accessor(js, binary, at["_FEATURE_ID"])).astype(int).tolist())
        assert 1 not in fids and 0 in fids and len(before) - 1 in fids  # no geometry in the kept slot
    finally:
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


def test_canopy_trees_reach_tiles_and_model_fill_is_untrusted(built):
    trees = json.loads((built / "data" / "processed" / "canopy_trees.geojson").read_text())["features"]
    assert len(trees) == 2  # one per synthetic crown, nothing on the constant-height plateau
    hs = sorted(f["properties"]["h"] for f in trees)  # uint8 + bilinear reprojection: within 1.5 m of the crown top
    assert abs(hs[0] - 9) <= 1.5 and abs(hs[1] - 14) <= 1.5
    assert all(f["properties"]["date"] == "2015-06-24" and f["properties"]["source"] == "chm_v2" for f in trees)
    idx = json.loads((tiles_dir(built) / "index.json").read_text())
    rows = [r for t in idx["tiles"] for r in json.loads((tiles_dir(built) / f"{t['id']}.json").read_text())["trees"]]
    chm = [r for r in rows if r[6] == 3]
    # the crown at (-300, 250) is real canopy in open land -> canopy-model tree with confidence + date
    assert len(chm) == 1 and abs(chm[0][0] + 300) < 3 and chm[0][7] in (1, 2)
    # the OSM forest around (250, 250) has < 10 % CHM canopy -> newer mapping than the imagery: land-cover rule
    assert any(r[6] == 1 and 150 <= r[0] <= 350 and 175 <= r[1] <= 325 for r in rows)


def test_local_outlines_enter_tiles_only_when_confirmed(built):
    rep = json.loads((built / "data" / "processed" / "footprints_report.json").read_text())
    assert rep["applied"] == ["local:synthetic-confirmed"] and rep["review"] == ["local:synthetic-draft"]
    idx = json.loads((tiles_dir(built) / "index.json").read_text())
    metas = [b for t in idx["tiles"] for b in json.loads((tiles_dir(built) / f"{t['id']}.json").read_text())["buildings"]]
    local = [b for b in metas if b["src"] == "local"]
    assert [b["sid"] for b in local] == ["local:synthetic-confirmed"] and local[0]["conf"] == "verified"
    assert local[0]["local"]["method"].startswith("SYNTHETIC")
    review = json.loads((built / "data" / "processed" / "review_footprints.geojson").read_text())["features"]
    assert [f["properties"]["id"] for f in review] == ["local:synthetic-draft"]


def test_review_workflow_dry_run_apply_and_revert(built, tmp_path):
    """Workbench change set -> CLI dry run (diff, validation) -> --yes (backup) -> rebuild -> revert."""
    root = tmp_path / "w"
    shutil.copytree(built, root)
    sys.path.insert(0, str(REPO / "tests" / "fixtures"))
    from make_fixture import ll, rect  # same synthetic frame as the fixture

    def feat(fid, ring, status="confirmed"):
        return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[ll(*p)["lon"], ll(*p)["lat"]] for p in ring]]},
                "properties": {"id": fid, "status": status, "confidence": "high", "method": "SYNTHETIC review test",
                               "date": "2026-11-14", "evidence": [{"url": "https://example.org/x", "license": "test only"}]}}
    fp = root / "data" / "overrides" / "footprints.geojson"
    before = fp.read_bytes()
    good = root / "good.json"
    direct = root / "direct.json"  # the workbench cannot confirm: a confirmed add is refused
    direct.write_text(json.dumps({"version": 1, "changes": [{"op": "add_outline", "feature": feat("local:review-new", rect(-260, 230, 10, 8))}]}))
    p = cli(root, "review", "apply", str(direct), "--yes", check=False)
    assert p.returncode == 1 and "approve_outline" in p.stdout and fp.read_bytes() == before
    noappr = root / "noappr.json"  # approval without who/basis/checks is refused
    noappr.write_text(json.dumps({"version": 1, "changes": [
        {"op": "add_outline", "feature": feat("local:review-new", rect(-260, 230, 10, 8), "approximate")},
        {"op": "approve_outline", "id": "local:review-new", "approval": {"by": "x"}}]}))
    p = cli(root, "review", "apply", str(noappr), "--yes", check=False)
    assert p.returncode == 1 and "evidence link is not enough" in p.stdout and fp.read_bytes() == before
    good.write_text(json.dumps({"version": 1, "changes": [
        {"op": "add_outline", "feature": feat("local:review-new", rect(-260, 230, 10, 8), "approximate")},
        {"op": "approve_outline", "id": "local:review-new",
         "approval": {"by": "test", "date": "2026-11-14", "basis": "site_survey", "checks": "synthetic"}}]}))
    p = cli(root, "review", "apply", str(good))
    assert "+ add outline local:review-new" in p.stdout and "! approve outline" in p.stdout and "Dry run" in p.stdout and fp.read_bytes() == before
    bad = root / "bad.json"
    bad.write_text(json.dumps({"version": 1, "changes": [{"op": "add_outline", "feature": feat(
        "local:review-bow", [(-300, 230), (-290, 240), (-290, 230), (-300, 240), (-300, 230)], "approximate")}]}))
    p = cli(root, "review", "apply", str(bad), "--yes", check=False)
    assert p.returncode == 1 and "Self-intersection" in p.stdout and fp.read_bytes() == before
    cli(root, "review", "apply", str(good), "--yes")
    assert any((root / "data" / "overrides" / "history").iterdir())
    cli(root, "normalize")
    cli(root, "build")
    idx = json.loads((tiles_dir(root) / "index.json").read_text())
    sids = {b["sid"] for t in idx["tiles"] for b in json.loads((tiles_dir(root) / f"{t['id']}.json").read_text())["buildings"]}
    assert "local:review-new" in sids
    cli(root, "review", "revert")
    assert fp.read_bytes() == before
