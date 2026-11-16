"""Headless Blender scene builder and renderer for Skvyra tiles.

    blender -b --factory-startup --python blender/scripts/build_scene.py -- \
        --tiles t+0+0 t+1+0 | --all | --around X Y R   [--lod 0] [--preset day|sunset|night]
        [--camera overview|oblique|street|flyover] [--engine eevee|cycles] [--samples 64]
        [--res 1920x1080] [--out renders/name.png] [--frames 240] [--save path.blend] [--no-render]

Coordinates: the glTF importer turns the tiles' Y-up frame back into Blender Z-up, which equals the
pipeline's local frame (x = east, y = north, z = metres above the EGM2008 geoid). Tree instances come
from the tile JSON in the same frame. Imported materials are replaced by shared procedural materials,
so the .blend stays editable and all tiles look consistent.
"""
import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(os.environ.get("SKVYRA_ROOT") or Path(__file__).resolve().parents[2])
TILES = ROOT / "assets" / "generated" / "tiles"
COLLS = ["Terrain", "Buildings", "Roads", "Vegetation", "Water", "Landmarks", "Decor (procedural)", "Lighting", "Cameras", "Helpers"]
TARGET = {"terrain": "Terrain", "walls": "Buildings", "roofs": "Buildings", "details": "Buildings",
          "road_paved": "Roads", "road_unpaved": "Roads", "road_foot": "Roads", "bridge": "Roads", "rail": "Roads",
          "water": "Water", "road_shoulder": "Roads", "decor_fence": "Decor (procedural)"}


def args_after_dashdash():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--tiles", nargs="*")
    p.add_argument("--all", action="store_true")
    p.add_argument("--around", nargs=3, type=float, metavar=("X", "Y", "R"))
    p.add_argument("--viewpoint", help="named camera from blender/presets/viewpoints.json (exact, for before/after)")
    p.add_argument("--lod", type=int, default=0)
    p.add_argument("--preset", default="day", choices=["day", "sunset", "night"])
    p.add_argument("--camera", default="oblique", choices=["overview", "oblique", "street", "flyover"])
    p.add_argument("--engine", default="eevee", choices=["eevee", "cycles"])
    p.add_argument("--samples", type=int, default=64)
    p.add_argument("--res", default="1920x1080")
    p.add_argument("--out", default=str(ROOT / "renders" / "preview.png"))
    p.add_argument("--frames", type=int, default=240)
    p.add_argument("--save")
    p.add_argument("--no-render", action="store_true")
    p.add_argument("--no-trees", action="store_true")
    return p.parse_args(argv)


def fail(msg):
    print(f"[skvyra-blender] ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    for name in COLLS:
        c = bpy.data.collections.new(name)
        scene.collection.children.link(c)
    return scene


def select_tiles(a, index):
    tiles = index["tiles"]
    if a.all:
        return tiles
    if a.tiles:
        want = set(a.tiles)
        sel = [t for t in tiles if t["id"] in want]
        missing = want - {t["id"] for t in sel}
        if missing:
            fail(f"tiles not found in index: {sorted(missing)}")
        return sel
    if a.viewpoint:
        vp = load_viewpoint(a.viewpoint)
        x, y = vp["target"][:2]
        cx, cy = vp["camera"][:2]
        r = min(2500.0, 300 + 1.6 * math.hypot(cx - x, cy - y) + 1.2 * (vp["camera"][2] - vp["target"][2]))
        if r >= 2500:
            return tiles
        mx, my = (x + cx) / 2, (y + cy) / 2
        return [t for t in tiles if t["b"][0] - r <= mx <= t["b"][2] + r and t["b"][1] - r <= my <= t["b"][3] + r]
    if a.around:
        x, y, r = a.around
        return [t for t in tiles if t["b"][0] - r <= x <= t["b"][2] + r and t["b"][1] - r <= y <= t["b"][3] + r]
    fail("choose --tiles, --all or --around")


# ----------------------------------------------------------------------------------------------------
# materials
# ----------------------------------------------------------------------------------------------------

def principled(nt):
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs[0], out.inputs[0])
    return bsdf


def vcol(nt):
    n = nt.nodes.new("ShaderNodeVertexColor")
    n.layer_name = "Color"
    return n


def _attr(nt, name):
    n = nt.nodes.new("ShaderNodeAttribute")
    n.attribute_name = name
    n.attribute_type = "GEOMETRY"
    return n.outputs["Fac"]


def _math(nt, op, a, b=None, value=None):
    n = nt.nodes.new("ShaderNodeMath")
    n.operation = op
    for i, x in enumerate((a, b if b is not None else value)):
        if x is None:
            continue
        if isinstance(x, (int, float)):
            n.inputs[i].default_value = x
        else:
            nt.links.new(x, n.inputs[i])
    return n.outputs[0]


def _select(nt, style, table, default):
    """Piecewise constant lookup: value = table[round(style)] if present, else default."""
    out = None
    for k, v in table.items():
        eq = _math(nt, "COMPARE", style, float(k))
        eq.node.inputs[2].default_value = 0.5
        term = _math(nt, "MULTIPLY", eq, v - default)
        out = term if out is None else _math(nt, "ADD", out, term)
    return _math(nt, "ADD", out, default)


def mat_walls():
    """Facade window grid with per-type proportions (_STYLE: 1 house, 2 apartments, 3 commercial,
    4 public, 6 church, 7 historic; 0/5 none). Decorative, mirrors the web viewer shader."""
    m = bpy.data.materials.new("skvyra_walls")
    nt = m.node_tree
    nt.nodes.clear()
    bsdf = principled(nt)
    vc = vcol(nt)
    st = _attr(nt, "_STYLE")
    uv = nt.nodes.new("ShaderNodeUVMap")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(uv.outputs[0], sep.inputs[0])
    fx, fy = _math(nt, "FRACT", sep.outputs[0]), _math(nt, "FRACT", sep.outputs[1])
    ww = _select(nt, st, {2: 0.2, 4: 0.16, 6: 0.12, 7: 0.15, 10: 0.15}, 0.19)
    y0 = _select(nt, st, {2: 0.3, 4: 0.22, 6: 0.16, 7: 0.2, 10: 0.3}, 0.33)
    wh = _select(nt, st, {2: 0.42, 4: 0.55, 6: 0.68, 7: 0.6, 10: 0.4}, 0.38)
    dx = _math(nt, "ABSOLUTE", _math(nt, "SUBTRACT", fx, value=0.5))
    inx = _math(nt, "LESS_THAN", dx, ww)
    iny = _math(nt, "MULTIPLY", _math(nt, "GREATER_THAN", fy, y0), _math(nt, "LESS_THAN", fy, _math(nt, "ADD", y0, wh)))
    win = _math(nt, "MULTIPLY", inx, iny)
    frx = _math(nt, "LESS_THAN", dx, _math(nt, "ADD", ww, value=0.04))
    fry = _math(nt, "MULTIPLY", _math(nt, "GREATER_THAN", fy, _math(nt, "SUBTRACT", y0, value=0.06)),
                _math(nt, "LESS_THAN", fy, _math(nt, "ADD", _math(nt, "ADD", y0, wh), value=0.04)))
    frame = _math(nt, "SUBTRACT", _math(nt, "MULTIPLY", frx, fry), win)
    has = _math(nt, "MULTIPLY", _math(nt, "GREATER_THAN", sep.outputs[0], value=-0.5), _math(nt, "GREATER_THAN", sep.outputs[1], value=0.0))
    not_industrial = _math(nt, "COMPARE", st, 5.0)
    not_industrial.node.inputs[2].default_value = 0.5
    styled = _math(nt, "MULTIPLY", _math(nt, "GREATER_THAN", st, value=0.5), _math(nt, "SUBTRACT", 1.0, not_industrial))
    on = _math(nt, "MULTIPLY", has, styled)
    win, frame = _math(nt, "MULTIPLY", win, on), _math(nt, "MULTIPLY", frame, on)
    framed = nt.nodes.new("ShaderNodeMix"); framed.data_type = "RGBA"
    nt.links.new(frame, framed.inputs["Factor"]); nt.links.new(vc.outputs["Color"], framed.inputs[6])
    framed.inputs[7].default_value = (0.82, 0.82, 0.8, 1)
    mix = nt.nodes.new("ShaderNodeMix"); mix.data_type = "RGBA"
    nt.links.new(win, mix.inputs["Factor"]); nt.links.new(framed.outputs[2], mix.inputs[6])
    mix.inputs[7].default_value = (0.03, 0.04, 0.05, 1)
    nt.links.new(mix.outputs[2], bsdf.inputs["Base Color"])
    rough = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(win, rough.inputs[0]); rough.inputs[3].default_value = 0.85; rough.inputs[4].default_value = 0.08
    nt.links.new(rough.outputs[0], bsdf.inputs["Roughness"])
    m["window_mask"] = True
    return m


def mat_roofs():
    """Roof material relief from _STYLE (1 metal tile, 2 slate, 3 corrugated, 4 seam, 0 bitumen) and
    roof UVs in metres (u along the eave, v up the slope)."""
    m = bpy.data.materials.new("skvyra_roofs")
    nt = m.node_tree
    nt.nodes.clear()
    bsdf = principled(nt)
    st = _attr(nt, "_STYLE")
    uv = nt.nodes.new("ShaderNodeUVMap")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(uv.outputs[0], sep.inputs[0])
    u, v = sep.outputs[0], sep.outputs[1]
    wave_period = _select(nt, st, {1: 0.185, 2: 0.115, 3: 0.076, 4: 0.55}, 1.0)
    wave = _math(nt, "SINE", _math(nt, "MULTIPLY", _math(nt, "DIVIDE", u, wave_period), value=6.2831))
    seam = _math(nt, "GREATER_THAN", _math(nt, "FRACT", _math(nt, "DIVIDE", u, value=0.55)), value=0.92)
    rows = _math(nt, "FRACT", _math(nt, "DIVIDE", v, value=0.35))
    is1, is4 = _math(nt, "COMPARE", st, value=1.0), _math(nt, "COMPARE", st, value=4.0)
    for n_ in (is1.node, is4.node):
        n_.inputs[2].default_value = 0.5
    height = _math(nt, "ADD", _math(nt, "MULTIPLY", wave, value=0.5),
                   _math(nt, "ADD", _math(nt, "MULTIPLY", rows, is1), _math(nt, "MULTIPLY", seam, is4)))
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.35
    bump.inputs["Distance"].default_value = 0.02
    nt.links.new(height, bump.inputs["Height"])
    nt.links.new(bump.outputs[0], bsdf.inputs["Normal"])
    nt.links.new(vcol(nt).outputs["Color"], bsdf.inputs["Base Color"])
    rough = _select(nt, st, {1: 0.45, 2: 0.9, 3: 0.5, 4: 0.4}, 0.95)
    nt.links.new(rough, bsdf.inputs["Roughness"])
    return m


def mat_simple(name, rough=0.8, metal=0.0, noise=0.0, use_vc=True, color=None, emit=None):
    m = bpy.data.materials.new(f"skvyra_{name}")
    nt = m.node_tree
    nt.nodes.clear()
    bsdf = principled(nt)
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    base = vcol(nt).outputs["Color"] if use_vc else None
    if color:
        bsdf.inputs["Base Color"].default_value = (*color, 1)
    if base is not None and noise:
        tex = nt.nodes.new("ShaderNodeTexNoise"); tex.inputs["Scale"].default_value = 0.6
        mr = nt.nodes.new("ShaderNodeMapRange"); mr.inputs[3].default_value = 1 - noise; mr.inputs[4].default_value = 1 + noise
        nt.links.new(tex.outputs[0], mr.inputs[0])
        mul = nt.nodes.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"; mul.inputs["Factor"].default_value = 1
        nt.links.new(base, mul.inputs[6]); nt.links.new(mr.outputs[0], mul.inputs[7])
        base = mul.outputs[2]
    if base is not None:
        nt.links.new(base, bsdf.inputs["Base Color"])
    if emit:
        bsdf.inputs["Emission Color"].default_value = (*emit[0], 1)
        bsdf.inputs["Emission Strength"].default_value = emit[1]
    return m


def mat_terrain(img):
    """Satellite colour + world-space grass/soil detail (two noise octaves) and a matching bump, so close-up
    renders are not a 10 m blur. Decorative, same intent as the web ground shader."""
    m = bpy.data.materials.new("skvyra_terrain")
    nt = m.node_tree
    nt.nodes.clear()
    bsdf = principled(nt)
    bsdf.inputs["Roughness"].default_value = 1.0
    t = nt.nodes.new("ShaderNodeTexImage"); t.image = img; t.extension = "EXTEND"
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    fine = nt.nodes.new("ShaderNodeTexNoise"); fine.inputs["Scale"].default_value = 4.0; fine.inputs["Detail"].default_value = 6.0
    clump = nt.nodes.new("ShaderNodeTexNoise"); clump.inputs["Scale"].default_value = 0.6
    nt.links.new(geo.outputs["Position"], fine.inputs["Vector"]); nt.links.new(geo.outputs["Position"], clump.inputs["Vector"])
    mixf = nt.nodes.new("ShaderNodeMath"); mixf.operation = "MULTIPLY_ADD"
    nt.links.new(fine.outputs["Fac"], mixf.inputs[0]); mixf.inputs[1].default_value = 0.35; mixf.inputs[2].default_value = 0.78
    mul = nt.nodes.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"; mul.inputs["Factor"].default_value = 1.0
    nt.links.new(t.outputs[0], mul.inputs[6]); nt.links.new(mixf.outputs[0], mul.inputs[7])
    tint = nt.nodes.new("ShaderNodeMix"); tint.data_type = "RGBA"; tint.blend_type = "OVERLAY"
    nt.links.new(clump.outputs["Fac"], tint.inputs["Factor"]); tint.inputs["Factor"].default_value = 0.15
    nt.links.new(mul.outputs[2], tint.inputs[6]); tint.inputs[7].default_value = (0.45, 0.5, 0.3, 1)
    nt.links.new(tint.outputs[2], bsdf.inputs["Base Color"])
    bump = nt.nodes.new("ShaderNodeBump"); bump.inputs["Strength"].default_value = 0.25; bump.inputs["Distance"].default_value = 0.05
    nt.links.new(fine.outputs["Fac"], bump.inputs["Height"]); nt.links.new(bump.outputs[0], bsdf.inputs["Normal"])
    return m


def mat_water():
    m = bpy.data.materials.new("skvyra_water")
    nt = m.node_tree
    nt.nodes.clear()
    bsdf = principled(nt)
    bsdf.inputs["Base Color"].default_value = (0.02, 0.05, 0.06, 1)
    bsdf.inputs["Roughness"].default_value = 0.04
    bump = nt.nodes.new("ShaderNodeBump"); bump.inputs["Strength"].default_value = 0.08
    wave = nt.nodes.new("ShaderNodeTexNoise"); wave.inputs["Scale"].default_value = 3.0
    nt.links.new(wave.outputs[0], bump.inputs["Height"]); nt.links.new(bump.outputs[0], bsdf.inputs["Normal"])
    return m


# ----------------------------------------------------------------------------------------------------
# import
# ----------------------------------------------------------------------------------------------------

def import_tiles(tiles, lod, preset):
    night = preset == "night"
    mats = {"walls": mat_walls(), "roofs": mat_roofs(),
            "details": mat_simple("details", 0.9), "road_paved": mat_simple("road_paved", 0.92, noise=0.12),
            "road_unpaved": mat_simple("road_unpaved", 1.0, noise=0.2), "road_foot": mat_simple("road_foot", 0.95, noise=0.1),
            "bridge": mat_simple("bridge", 0.8), "rail": mat_simple("rail", 1.0, noise=0.2), "water": mat_water(),
            "road_shoulder": mat_simple("road_shoulder", 1.0, noise=0.25), "decor_fence": mat_simple("decor_fence", 0.6),
            "details": mat_simple("details", 0.8)}
    if night:  # warm lit windows; purely decorative
        w = mats["walls"].node_tree
        bsdf = next(n for n in w.nodes if n.type == "BSDF_PRINCIPLED")
        mask_src = next(n for n in w.nodes if n.type == "MIX").inputs["Factor"].links[0].from_socket
        bsdf.inputs["Emission Color"].default_value = (1.0, 0.62, 0.3, 1)
        w.links.new(mask_src, bsdf.inputs["Emission Strength"])
    coll = {c.name: c for c in bpy.data.collections}
    t0 = time.time()
    for k, t in enumerate(tiles):
        f = TILES / t["files"][str(lod)]["u"]
        if not f.exists():
            print(f"[skvyra-blender] WARNING missing tile file {f.name} - skipped (rebuild with `skvyra build`)")
            continue
        before = set(bpy.data.objects)
        res = bpy.ops.import_scene.gltf(filepath=str(f))
        if "FINISHED" not in res:
            fail(f"glTF import failed for {f}")
        for ob in set(bpy.data.objects) - before:
            kind = ob.name.split(".")[0]
            target = coll[TARGET.get(kind, "Helpers")]
            for c in list(ob.users_collection):
                c.objects.unlink(ob)
            target.objects.link(ob)
            ob.name = f"{t['id']}_{kind}"
            ob["tile"] = t["id"]
            if ob.type != "MESH":
                continue
            if kind == "terrain":
                img = next((n.image for n in ob.data.materials[0].node_tree.nodes if n.type == "TEX_IMAGE"), None)
                ob.data.materials[0] = mat_terrain(img) if img else mats["roofs"]
                if img:
                    img.name = f"{t['id']}_ground"
                    img.pack()  # keep the .blend self-contained
            elif kind in mats:
                ob.data.materials[0] = mats[kind]
            for poly in ob.data.polygons:
                poly.use_smooth = kind in ("terrain", "water")
        if (k + 1) % 10 == 0:
            print(f"[skvyra-blender] imported {k + 1}/{len(tiles)} tiles ({time.time() - t0:.0f}s)")
    for m in list(bpy.data.materials):  # imported glTF materials are now unused
        if m.users == 0:
            bpy.data.materials.remove(m)


def tree_prototypes():
    helpers = bpy.data.collections["Helpers"]
    protos = {}
    leaf = mat_simple("leaves", 0.8, use_vc=False, color=(0.07, 0.16, 0.04))
    leaf_c = mat_simple("conifer", 0.8, use_vc=False, color=(0.03, 0.09, 0.04))
    bark = mat_simple("bark", 0.9, use_vc=False, color=(0.12, 0.08, 0.05))
    for kind in (0, 1, 2, 3, 4):
        bpy.ops.mesh.primitive_cylinder_add(vertices=6, radius=0.08, depth=0.35 if kind != 4 else 0.05, location=(0, 0, 0.175 if kind != 4 else 0.02))
        trunk = bpy.context.object
        trunk.data.materials.append(bark)
        if kind == 4:  # garden shrub: low, wide blob (decorative layer)
            bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=0.5, location=(0, 0, 0.45))
        elif kind == 3:  # columnar thuja
            bpy.ops.mesh.primitive_uv_sphere_add(segments=10, ring_count=8, radius=0.5, location=(0, 0, 0.5))
        elif kind == 1:
            bpy.ops.mesh.primitive_cone_add(vertices=8, radius1=0.32, depth=0.8, location=(0, 0, 0.6))
        else:
            bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=0.5, location=(0, 0, 0.62))
        crown = bpy.context.object
        crown.data.materials.append(leaf_c if kind in (1, 3) else leaf)
        if kind in (0, 2):
            crown.scale = (1, 1, 0.85)
        bpy.ops.object.select_all(action="DESELECT")
        trunk.select_set(True); crown.select_set(True)
        bpy.context.view_layer.objects.active = crown
        bpy.ops.object.join()
        proto = bpy.context.object
        proto.name = f"tree_proto_{kind}"
        for c in list(proto.users_collection):
            c.objects.unlink(proto)
        helpers.objects.link(proto)
        proto.hide_render = True
        proto.hide_viewport = True
        protos[kind] = proto
    return protos


def add_trees(tiles, protos):
    """One point cloud per tree kind; geometry nodes instance the prototype, scaled per tree height."""
    pts = {0: [], 1: [], 2: [], 3: [], 4: []}
    for t in tiles:
        meta = json.loads((TILES / f"{t['id']}.json").read_text())
        for x, y, z, h, r, kind, src in meta["trees"]:
            pts[kind].append((x, y, z, h, r))
    veg = bpy.data.collections["Vegetation"]
    for kind, rows in pts.items():
        if not rows:
            continue
        me = bpy.data.meshes.new(f"trees_{kind}")
        me.vertices.add(len(rows))
        me.vertices.foreach_set("co", [c for x, y, z, h, r in rows for c in (x, y, z - 0.2)])
        sc = me.attributes.new("tree_scale", "FLOAT_VECTOR", "POINT")
        sc.data.foreach_set("vector", [c for x, y, z, h, r in rows for c in (r * 2, r * 2, h)])
        rot = me.attributes.new("tree_rot", "FLOAT_VECTOR", "POINT")
        rot.data.foreach_set("vector", [c for x, y, z, h, r in rows for c in (0, 0, (x * 12.9898 + y * 78.233) % 6.283)])
        ob = bpy.data.objects.new(f"trees_{['deciduous', 'conifer', 'fruit', 'thuja', 'shrub'][kind]}", me)
        veg.objects.link(ob)
        ng = bpy.data.node_groups.new(f"trees_gn_{kind}", "GeometryNodeTree")
        ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        gi, go = ng.nodes.new("NodeGroupInput"), ng.nodes.new("NodeGroupOutput")
        inst = ng.nodes.new("GeometryNodeInstanceOnPoints")
        oi = ng.nodes.new("GeometryNodeObjectInfo"); oi.inputs["Object"].default_value = protos[kind]
        oi.inputs["As Instance"].default_value = True
        sa = ng.nodes.new("GeometryNodeInputNamedAttribute"); sa.data_type = "FLOAT_VECTOR"; sa.inputs["Name"].default_value = "tree_scale"
        ra = ng.nodes.new("GeometryNodeInputNamedAttribute"); ra.data_type = "FLOAT_VECTOR"; ra.inputs["Name"].default_value = "tree_rot"
        ng.links.new(gi.outputs[0], inst.inputs["Points"])
        ng.links.new(oi.outputs["Geometry"], inst.inputs["Instance"])
        ng.links.new(sa.outputs["Attribute"], inst.inputs["Scale"])
        ng.links.new(ra.outputs["Attribute"], inst.inputs["Rotation"])
        ng.links.new(inst.outputs[0], go.inputs[0])
        mod = ob.modifiers.new("trees", "NODES")
        mod.node_group = ng
    return sum(len(v) for v in pts.values())


# ----------------------------------------------------------------------------------------------------
# lighting, camera, render
# ----------------------------------------------------------------------------------------------------
PRESETS = {  # sun elevation, azimuth (deg from north, clockwise), strength, sky strength, exposure
    "day": (38, 205, 3.6, 0.25, -1.0), "sunset": (6, 255, 3.0, 0.18, -0.6), "night": (-12, 20, 0.0, 0.03, 1.2)}


def lighting(scene, preset):
    elev, az, sun_str, sky_str, exposure = PRESETS[preset]
    world = bpy.data.worlds.new("skvyra_world")
    scene.world = world
    nt = world.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    sky = nt.nodes.new("ShaderNodeTexSky")
    sky.sky_type = "MULTIPLE_SCATTERING"
    sky.sun_elevation = math.radians(max(elev, -5))
    sky.sun_rotation = math.radians(180 - az)
    sky.sun_disc = False  # the sun lamp provides direct light; avoid double counting
    nt.links.new(sky.outputs[0], bg.inputs[0])
    bg.inputs[1].default_value = sky_str
    nt.links.new(bg.outputs[0], out.inputs[0])
    if preset == "night":
        bg.inputs[0].default_value = (0.005, 0.008, 0.02, 1)
        nt.links.remove(bg.inputs[0].links[0])
        bpy.ops.object.light_add(type="SUN")
        moon = bpy.context.object; moon.name = "moon"; moon.data.energy = 0.08
        moon.data.color = (0.6, 0.7, 1.0); moon.rotation_euler = (math.radians(50), 0, math.radians(30))
        _move(moon, "Lighting")
    else:
        bpy.ops.object.light_add(type="SUN")
        sun = bpy.context.object
        sun.name = "sun"
        sun.data.energy = sun_str
        sun.data.angle = math.radians(0.6)
        if preset == "sunset":
            sun.data.color = (1.0, 0.72, 0.48)
        # light travels from azimuth `az` at elevation `elev`
        sun.rotation_euler = (math.radians(90 - elev), 0, math.radians(180 - az))
        _move(sun, "Lighting")
    for vt in ("Khronos PBR Neutral", "AgX", "Filmic", "Standard"):  # enum is dynamic; try in preference order
        try:
            scene.view_settings.view_transform = vt
            break
        except TypeError:
            continue
    scene.view_settings.exposure = exposure



def _move(ob, coll):
    for c in list(ob.users_collection):
        c.objects.unlink(ob)
    bpy.data.collections[coll].objects.link(ob)


def load_viewpoint(name):
    vps = json.loads((ROOT / "blender" / "presets" / "viewpoints.json").read_text())["viewpoints"]
    if name not in vps:
        fail(f"unknown viewpoint {name!r}; known: {sorted(vps)}")
    return vps[name]


def camera_viewpoint(scene, name):
    vp = load_viewpoint(name)
    cam_data = bpy.data.cameras.new("cam")
    cam_data.sensor_fit = "VERTICAL"
    cam_data.angle_y = math.radians(vp.get("fov_deg", 45))  # same vertical FOV as the web viewer
    cam_data.clip_start, cam_data.clip_end = 1.0, 30000
    cam = bpy.data.objects.new("camera", cam_data)
    bpy.data.collections["Cameras"].objects.link(cam)
    cam.location = vp["camera"]
    direction = Vector(vp["target"]) - Vector(vp["camera"])
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam
    return cam


def camera(scene, tiles, kind, frames):
    x0 = min(t["b"][0] for t in tiles); y0 = min(t["b"][1] for t in tiles)
    x1 = max(t["b"][2] for t in tiles); y1 = max(t["b"][3] for t in tiles)
    zmin = min(t["z"][0] for t in tiles); zmax = max(t["z"][1] for t in tiles)
    cx, cy, w = (x0 + x1) / 2, (y0 + y1) / 2, max(x1 - x0, y1 - y0)
    target = bpy.data.objects.new("cam_target", None)
    bpy.data.collections["Cameras"].objects.link(target)
    target.location = (cx, cy, zmin + 5)
    cam_data = bpy.data.cameras.new("cam")
    cam_data.clip_start, cam_data.clip_end = 1.0, 30000
    cam = bpy.data.objects.new("camera", cam_data)
    bpy.data.collections["Cameras"].objects.link(cam)
    scene.camera = cam
    tr = cam.constraints.new("TRACK_TO"); tr.target = target; tr.track_axis = "TRACK_NEGATIVE_Z"; tr.up_axis = "UP_Y"
    if kind == "overview":
        cam_data.lens = 35
        cam.location = (cx, cy - 0.35 * w, zmax + 1.05 * w)
    elif kind == "oblique":
        cam_data.lens = 35
        cam.location = (cx - 0.35 * w, cy - 0.6 * w, zmax + 0.38 * w)
    elif kind == "street":
        cam_data.lens = 24
        cam.location = (cx - 60, cy - 80, zmin + 35)
    else:  # flyover: smooth orbit-and-descend path around the selection centre
        cam_data.lens = 28
        scene.frame_start, scene.frame_end = 1, frames
        for f in range(1, frames + 1, max(1, frames // 12)):
            a = (f - 1) / max(1, frames - 1)
            ang = math.radians(-120 + 140 * a)
            rad = w * (0.75 - 0.35 * a)
            cam.location = (cx + rad * math.sin(ang), cy - rad * math.cos(ang), zmax + w * (0.45 - 0.3 * a))
            cam.keyframe_insert("location", frame=f)
            target.location = (cx + 40 * a, cy + 60 * a, zmin + 5)
            target.keyframe_insert("location", frame=f)
        for ob in (cam, target):
            for fc in _fcurves(ob):
                for kp in fc.keyframe_points:
                    kp.interpolation = "BEZIER"
                    kp.handle_left_type = kp.handle_right_type = "AUTO_CLAMPED"
    return cam


def import_context(index):
    """Satellite-coloured context ground around the reconstructed tiles (helper, not reconstruction)."""
    ctx = index.get("context")
    if not ctx or not (TILES / ctx["u"]).exists():
        return
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(TILES / ctx["u"]))
    for ob in set(bpy.data.objects) - before:
        _move(ob, "Helpers")
        if ob.type == "MESH":
            img = next((n.image for n in ob.data.materials[0].node_tree.nodes if n.type == "TEX_IMAGE"), None)
            if img:
                img.pack()
                ob.data.materials[0] = mat_terrain(img)
            ob.name = "context_ground (Sentinel-2, not reconstructed)"


def horizon(tiles):
    """Large flat ground beyond the reconstructed area so the scene has no void (labelled helper)."""
    zmin = min(t["z"][0] for t in tiles)
    cx = (min(t["b"][0] for t in tiles) + max(t["b"][2] for t in tiles)) / 2
    cy = (min(t["b"][1] for t in tiles) + max(t["b"][3] for t in tiles)) / 2
    bpy.ops.mesh.primitive_plane_add(size=40000, location=(cx, cy, zmin - 3))
    ob = bpy.context.object
    ob.name = "horizon_ground (not reconstructed)"
    m = mat_simple("horizon", 1.0, use_vc=False, color=(0.33, 0.33, 0.18))
    ob.data.materials.append(m)
    _move(ob, "Helpers")


def _fcurves(ob):
    ad = ob.animation_data
    if not ad or not ad.action:
        return []
    if hasattr(ad.action, "fcurves"):
        return list(ad.action.fcurves)
    # Blender 5 layered actions
    out = []
    for layer in ad.action.layers:
        for strip in layer.strips:
            for cb in strip.channelbags:
                out += list(cb.fcurves)
    return out


def render_settings(scene, a):
    w, h = (int(v) for v in a.res.split("x"))
    scene.render.resolution_x, scene.render.resolution_y = w, h
    scene.render.resolution_percentage = 100
    if a.engine == "cycles":
        scene.render.engine = "CYCLES"
        prefs = bpy.context.preferences.addons["cycles"].preferences
        try:
            prefs.compute_device_type = "METAL"
            prefs.get_devices()
            for d in prefs.devices:
                d.use = True
            scene.cycles.device = "GPU"
        except Exception as e:  # CPU fallback is slower but correct
            print(f"[skvyra-blender] GPU unavailable ({e}); rendering on CPU")
        scene.cycles.samples = a.samples
        scene.cycles.use_denoising = True
    else:
        scene.render.engine = "BLENDER_EEVEE"
        scene.eevee.taa_render_samples = a.samples
        try:
            scene.eevee.use_shadows = True
            scene.eevee.use_raytracing = True
        except AttributeError:
            pass
    scene.render.film_transparent = False


def main():
    a = args_after_dashdash()
    index_path = TILES / "index.json"
    if not index_path.exists():
        fail(f"{index_path} not found - run the pipeline first (`skvyra build`)")
    index = json.loads(index_path.read_text())
    scene = reset()
    tiles = select_tiles(a, index)
    if not tiles:
        fail("no tiles selected")
    t0 = time.time()
    import_tiles(tiles, a.lod, a.preset)
    n_trees = 0 if a.no_trees else add_trees(tiles, tree_prototypes())
    horizon(tiles)
    import_context(index)
    lighting(scene, a.preset)
    if a.viewpoint:
        camera_viewpoint(scene, a.viewpoint)
    else:
        camera(scene, tiles, a.camera, a.frames)
    render_settings(scene, a)
    scene["skvyra"] = json.dumps({"tiles": [t["id"] for t in tiles], "lod": a.lod, "frame": index["frame"],
                                  "attribution": "© OpenStreetMap contributors (ODbL); Microsoft ML footprints (CDLA-Permissive-2.0); "
                                                 "Copernicus DEM © DLR e.V./Airbus, provided under COPERNICUS by EU and ESA"})
    print(f"[skvyra-blender] scene: {len(tiles)} tiles, {n_trees} trees, {len(bpy.data.objects)} objects in {time.time() - t0:.1f}s")
    if a.save:
        Path(a.save).parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(Path(a.save).resolve()), compress=True)
        print(f"[skvyra-blender] saved {a.save}")
    if a.no_render:
        return
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    t1 = time.time()
    if a.camera == "flyover" and not a.viewpoint:
        scene.render.filepath = str(out.with_suffix("")) + "_"
        scene.render.image_settings.file_format = "PNG"
        bpy.ops.render.render(animation=True)
    else:
        scene.render.filepath = str(out)
        bpy.ops.render.render(write_still=True)
    print(f"[skvyra-blender] rendered {out} in {time.time() - t1:.1f}s")


if __name__ == "__main__":
    main()
