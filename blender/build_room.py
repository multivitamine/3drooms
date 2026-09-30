"""Build a hotel room scene from a room spec (JSON) and export it for the web.

Usage (headless):
    blender -b -P blender/build_room.py -- specs/room-01.json [--no-render] [--no-export] [--cams a,b]

Outputs (output/<room id>/):
    <id>.blend          scene for manual tweaking
    <id>.glb            web model (also copied to web/models/)
    renders/<cam>.png   preview renders from cameras matching the source photos
"""
import json
import math
import shutil
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
TEX = ROOT / "assets" / "textures"

# ---------------------------------------------------------------- args / spec
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
spec_path = Path(argv[0]) if argv else ROOT / "specs" / "room-01.json"
if not spec_path.is_absolute():
    spec_path = ROOT / spec_path
DO_RENDER = "--no-render" not in argv
DO_EXPORT = "--no-export" not in argv
DO_BAKE = "--bake" in argv
BAKE_SIZE = next((int(a.split("=", 1)[1]) for a in argv if a.startswith("--bake-size=")), 4096)
BAKE_SAMPLES = next((int(a.split("=", 1)[1]) for a in argv if a.startswith("--bake-samples=")), 256)
LIGHTMAP_SCALE = 4.0  # lightmap PNG stores (irradiance / LIGHTMAP_SCALE), sRGB-encoded
CAMS = next((a.split("=", 1)[1].split(",") for a in argv if a.startswith("--cams=")), None)

spec = json.loads(spec_path.read_text(encoding="utf-8"))
OUT = ROOT / "output" / spec["id"]
OUT.mkdir(parents=True, exist_ok=True)

R = spec["room"]
W, D, H, BD = R["width"], R["depth"], R["height"], R["balcony_depth"]

# ---------------------------------------------------------------- scene reset
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
col = scene.collection


# ---------------------------------------------------------------- materials
def hex_lin(h):
    h = h.lstrip("#")
    srgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb] + [1.0]


MATS = {}


def mat(name):
    if name in MATS:
        return MATS[name]
    m_spec = spec["materials"][name]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = hex_lin(m_spec.get("color", "#FFFFFF"))
    bsdf.inputs["Roughness"].default_value = m_spec.get("roughness", 0.8)
    bsdf.inputs["Metallic"].default_value = m_spec.get("metallic", 0.0)
    if "texture" in m_spec:
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(str(TEX / m_spec["texture"]), check_existing=True)
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        if "emission" in m_spec:
            nt.links.new(tex.outputs["Color"], bsdf.inputs["Emission Color"])
    if "emission" in m_spec:
        bsdf.inputs["Emission Strength"].default_value = m_spec["emission"]
        if "texture" not in m_spec:
            bsdf.inputs["Emission Color"].default_value = hex_lin(m_spec["color"])
    if "alpha" in m_spec:
        bsdf.inputs["Alpha"].default_value = m_spec["alpha"]
        m.surface_render_method = "BLENDED"
    m.use_backface_culling = m_spec.get("cull", False)
    MATS[name] = m
    return m


def emissive(name, color, strength):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = hex_lin(color)
    bsdf.inputs["Emission Color"].default_value = hex_lin(color)
    bsdf.inputs["Emission Strength"].default_value = strength
    return m


# Wall-like surfaces are single-sided planes facing into the room, so the web
# dollhouse view can see through the walls nearest the camera.
for n in ("wall", "ceiling", "mural"):
    spec["materials"][n]["cull"] = True


# ---------------------------------------------------------------- geometry helpers
def finish(name, bm, material, matrix=None, smooth=False, uv_size=None):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    if matrix is not None:
        me.transform(matrix)
    if smooth:
        for p in me.polygons:
            p.use_smooth = True
    ob = bpy.data.objects.new(name, me)
    col.objects.link(ob)
    if material is not None:
        me.materials.append(material)
    size = uv_size or spec["materials"].get(material.name if material else "", {}).get("tex_size")
    if size and not me.uv_layers:
        world_uv(me, size)
    return ob


def world_uv(me, size):
    """Box-project UVs in metres so tiling textures keep real-world scale."""
    uv = me.uv_layers.new(name="UVMap")
    for poly in me.polygons:
        n = poly.normal
        ax = max(range(3), key=lambda i: abs(n[i]))
        for li in poly.loop_indices:
            co = me.vertices[me.loops[li].vertex_index].co
            u, v = {0: (co.y, co.z), 1: (co.x, co.z), 2: (co.x, co.y)}[ax]
            uv.data[li].uv = (u / size, v / size)


def box(name, x, y, z, material, bevel=0.0, matrix=None, subsurf=0):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    c = Vector(((x[0] + x[1]) / 2, (y[0] + y[1]) / 2, (z[0] + z[1]) / 2))
    s = Vector((x[1] - x[0], y[1] - y[0], z[1] - z[0]))
    for v in bm.verts:
        v.co = c + Vector((v.co.x * s.x, v.co.y * s.y, v.co.z * s.z))
    ob = finish(name, bm, material, matrix, smooth=bool(bevel or subsurf))
    if bevel:
        m = ob.modifiers.new("bevel", "BEVEL")
        m.width, m.segments, m.limit_method = bevel, 3, "ANGLE"
    if subsurf:
        m = ob.modifiers.new("subsurf", "SUBSURF")
        m.levels = m.render_levels = subsurf
    return ob


def cyl(name, cx, cy, z0, z1, r, material, r2=None, segs=32, matrix=None, caps=True):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=caps, segments=segs, radius1=r,
                          radius2=r if r2 is None else r2, depth=z1 - z0)
    bmesh.ops.translate(bm, verts=bm.verts, vec=(cx, cy, (z0 + z1) / 2))
    return finish(name, bm, material, matrix, smooth=True)


def sphere(name, center, radii, material, matrix=None, segs=24):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=segs // 2, radius=1.0)
    for v in bm.verts:
        v.co = Vector(center) + Vector((v.co.x * radii[0], v.co.y * radii[1], v.co.z * radii[2]))
    return finish(name, bm, material, matrix, smooth=True)


def quad(name, pts, material, uvs=None):
    """pts counter-clockwise seen from the side the face should be visible from."""
    bm = bmesh.new()
    face = bm.faces.new([bm.verts.new(p) for p in pts])
    if uvs:
        layer = bm.loops.layers.uv.new("UVMap")
        for loop, uv in zip(face.loops, uvs):
            loop[layer].uv = uv
    return finish(name, bm, material)


def wall_x(name, x, y0, y1, z0, z1, facing, material):
    """Vertical plane at constant x, visible from +x (facing=1) or -x (facing=-1)."""
    pts = [(x, y0, z0), (x, y1, z0), (x, y1, z1), (x, y0, z1)]
    return quad(name, pts if facing > 0 else pts[::-1], material)


def wall_y(name, y, x0, x1, z0, z1, facing, material):
    pts = [(x0, y, z0), (x1, y, z0), (x1, y, z1), (x0, y, z1)]
    return quad(name, pts[::-1] if facing > 0 else pts, material)


def wireframe(ob, thickness):
    m = ob.modifiers.new("wire", "WIREFRAME")
    m.thickness = thickness
    return ob


def at(x, y, rot_deg=0.0, z=0.0):
    return Matrix.Translation((x, y, z)) @ Matrix.Rotation(math.radians(rot_deg), 4, "Z")


# ---------------------------------------------------------------- shell
def build_shell():
    o = spec["openings"]
    # Floor + ceiling
    quad("Floor", [(0, 0, 0), (W, 0, 0), (W, D, 0), (0, D, 0)], mat("floor"))
    quad("Ceiling", [(0, 0, H), (0, D, H), (W, D, H), (W, 0, H)], mat("ceiling"))
    # Long walls
    wall_x("Wall_bed", 0.0, 0.0, D, 0.0, H, +1, mat("wall"))
    wall_x("Wall_unit", W, 0.0, D, 0.0, H, -1, mat("wall"))
    # Entrance wall with door opening (door left closed)
    ed = o["entrance_door"]
    wall_y("Wall_entrance", 0.0, 0.0, ed["x"][0], 0.0, H, +1, mat("wall"))
    wall_y("Wall_entrance_top", 0.0, ed["x"][0], ed["x"][1], ed["height"], H, +1, mat("wall"))
    box("Door_entrance", (ed["x"][0] + 0.02, ed["x"][1] - 0.02), (-0.04, 0.0), (0.0, ed["height"] - 0.01), mat("door"))
    box("Door_handle", (ed["x"][0] + 0.08, ed["x"][0] + 0.2), (0.0, 0.05), (1.02, 1.05), mat("black"))
    # Window wall: header + full-height glazing with white frames
    ww = o["window_wall"]
    head = ww["head"]
    wall_y("Wall_window_header", D, 0.0, W, head, H, -1, mat("wall"))
    fw = 0.06
    for i, xm in enumerate((0.0, ww["sliding_door"][0], ww["sliding_door"][1], W - fw)):
        box(f"Window_mullion_{i}", (xm, xm + fw), (D, D + 0.1), (0.0, head), mat("frame_white"))
    box("Window_head", (0.0, W), (D, D + 0.1), (head - fw, head), mat("frame_white"))
    box("Window_sill", (0.0, W), (D, D + 0.12), (0.0, 0.05), mat("frame_white"))
    sd = ww["sliding_door"]
    box("Glass_fixed", (fw, sd[0]), (D + 0.045, D + 0.055), (0.05, head - fw), mat("glass"))
    box("Glass_right", (sd[1] + fw, W - fw), (D + 0.045, D + 0.055), (0.05, head - fw), mat("glass"))
    # sliding door pushed open behind the right-hand pane
    box("Glass_slider", (sd[1] + 0.05, W - 0.02), (D + 0.075, D + 0.085), (0.05, head - fw), mat("glass"))
    box("Slider_frame", (sd[1] + 0.02, sd[1] + 0.07), (D + 0.07, D + 0.09), (0.0, head - fw), mat("frame_white"))

    # Bathroom + wardrobe: closed volumes in the draft (interior not built yet)
    b = o["bathroom"]
    bx, by = b["x"][1], b["y"][1]
    wall_y("Bath_wall_front", by, 0.0, bx, 0.0, H, +1, mat("wall"))
    wall_x("Bath_wall_side", bx, 0.0, by, 0.0, H, +1, mat("wall"))
    box("Door_bathroom", (bx, bx + 0.03), (b["door_y"][0], b["door_y"][1]), (0.0, 2.08), mat("door"))
    box("Door_bathroom_handle", (bx + 0.03, bx + 0.08), (b["door_y"][1] - 0.18, b["door_y"][1] - 0.06), (1.02, 1.05), mat("black"))
    closed = emissive("closed_area", "#D9D4CC", 0.0)
    quad("Closed_bathroom_top", [(0, 0, H - 0.001), (bx, 0, H - 0.001), (bx, by, H - 0.001), (0, by, H - 0.001)], closed)
    wr = o["wardrobe"]
    box("Wardrobe", (bx, wr["x"][1]), (wr["y"][0], wr["y"][1]), (0.0, H - 0.02), mat("oak"))
    box("Wardrobe_gap", (bx + 0.01, wr["x"][1] + 0.002), ((wr["y"][0] + wr["y"][1]) / 2 - 0.003, (wr["y"][0] + wr["y"][1]) / 2 + 0.003), (0.05, H - 0.1), mat("black"))
    # Floor under closed areas is not needed; skirting along visible walls
    sk = 0.07
    box("Skirting_bed", (0.0, 0.012), (by, D), (0.0, sk), mat("frame_white"))
    box("Skirting_unit", (W - 0.012, W), (0.0, D), (0.0, sk), mat("frame_white"))


# ---------------------------------------------------------------- furniture builders
def b_mural(it):
    y0, y1 = it["y"]
    z0, z1 = it["z"]
    x = 0.003
    quad("Mural", [(x, y0, z0), (x, y1, z0), (x, y1, z1), (x, y0, z1)], mat("mural"),
         uvs=[(0, 0), (1, 0), (1, 1), (0, 1)])


def b_bed(it):
    x0, x1 = it["x"]
    y0, y1 = it["y"]
    for i, (lx, ly) in enumerate(((x0 + 0.1, y0 + 0.1), (x0 + 0.1, y1 - 0.1), (x1 - 0.1, y0 + 0.1), (x1 - 0.1, y1 - 0.1))):
        cyl(f"Bed_leg_{i}", lx, ly, 0.0, 0.12, 0.025, mat("black"), segs=12)
    box("Bed_base", (x0, x1), (y0, y1), (0.12, 0.42), mat("bed_base"), bevel=0.02)
    box("Bed_mattress", (x0 + 0.01, x1 - 0.01), (y0 + 0.01, y1 - 0.01), (0.42, 0.62), mat("linen"), bevel=0.05)
    box("Bed_duvet", (x0 + 0.55, x1 + 0.04), (y0 - 0.05, y1 + 0.05), (0.5, 0.68), mat("linen"), bevel=0.07)
    # pillows: 2 sleeping + 2 decorative against the mural wall
    for i, (yc) in enumerate((y0 + 0.47, y1 - 0.47)):
        lean = Matrix.Translation((x0, 0, 0.62)) @ Matrix.Rotation(math.radians(-14), 4, "Y") @ Matrix.Translation((-x0, 0, -0.62))
        box(f"Pillow_back_{i}", (x0 + 0.05, x0 + 0.21), (yc - 0.37, yc + 0.37), (0.62, 1.16), mat("linen"), bevel=0.07, matrix=lean)
        box(f"Pillow_front_{i}", (x0 + 0.22, x0 + 0.38), (yc - 0.33, yc + 0.33), (0.64, 1.0), mat("linen"), bevel=0.07, matrix=lean)


def b_nightstand(it):
    x0, x1 = it["x"]
    y0, y1 = it["y"]
    z = it["z"]
    box(f"{it['id']}", (x0, x1), (y0, y1), (z - 0.1, z), mat("oak"), bevel=0.004)


def b_pendant_cage(it):
    x, y, z = it["pos"]
    cyl(f"{it['id']}_cord", x, y, z + 0.2, H, 0.004, mat("black"), segs=8)
    sphere(f"{it['id']}_cap", (x, y, z + 0.2), (0.1, 0.1, 0.07), mat("frame_white"))
    cage = cyl(f"{it['id']}_cage", x, y, z, z + 0.2, 0.085, mat("copper"), segs=10, caps=False)
    wireframe(cage, 0.008)
    sphere(f"{it['id']}_bulb", (x, y, z + 0.09), (0.035, 0.035, 0.05), emissive(f"{it['id']}_glow", "#FFC98A", 6.0), segs=12)
    add_point_light(f"{it['id']}_light", (x, y, z + 0.08), 12)


def b_floor_lamp(it):
    x, y = it["pos"]
    box(f"{it['id']}_base", (x - 0.06, x + 0.06), (y - 0.06, y + 0.06), (0.0, 0.02), mat("frame_white"), bevel=0.005)
    cyl(f"{it['id']}_pole", x, y, 0.02, 1.45, 0.014, mat("oak"), segs=12)
    cyl(f"{it['id']}_pole_top", x, y, 1.45, 1.55, 0.016, mat("frame_white"), segs=12)
    for i, (z, ang) in enumerate(((1.4, 60.0), (0.62, 30.0))):
        m = at(x, y, ang, z) @ Matrix.Rotation(math.radians(90), 4, "Y")
        cyl(f"{it['id']}_arm_{i}", 0.0, 0.0, 0.0, 0.14, 0.012, mat("frame_white"), segs=8, matrix=m)
        m2 = at(x, y, ang, z) @ Matrix.Translation((0.2, 0.0, 0.0)) @ Matrix.Rotation(math.radians(90), 4, "Y")
        cyl(f"{it['id']}_shade_{i}", 0.0, 0.0, -0.07, 0.07, 0.055, mat("brass"), segs=24, matrix=m2)


def b_radiator(it):
    box(it["id"], tuple(it["x"]), tuple(it["y"]), tuple(it["z"]), mat("frame_white"), bevel=0.01)


def b_armchair(it):
    x, y = it["pos"]
    M = at(x, y, it.get("rot", 0))
    v, cu = mat("velvet_taupe"), mat("mustard")
    for i, (lx, ly) in enumerate(((-0.25, -0.22), (0.25, -0.22), (-0.25, 0.22), (0.25, 0.22))):
        cyl(f"Chair_leg_{i}", lx, ly, 0.0, 0.22, 0.015, mat("black"), segs=8, matrix=M)
    box("Chair_seat", (-0.32, 0.32), (-0.3, 0.3), (0.22, 0.42), v, bevel=0.05, matrix=M)
    tilt = M @ Matrix.Translation((0, 0.3, 0.4)) @ Matrix.Rotation(math.radians(-12), 4, "X")
    box("Chair_back", (-0.34, 0.34), (-0.07, 0.05), (0.0, 0.5), v, bevel=0.05, matrix=tilt)
    for s in (-1, 1):
        box(f"Chair_arm_{s}", (s * 0.27 - 0.06, s * 0.27 + 0.06), (-0.22, 0.3), (0.35, 0.62), v, bevel=0.05, matrix=M)
    box("Chair_cushion", (-0.2, 0.2), (0.12, 0.24), (0.42, 0.66), cu, subsurf=2, matrix=tilt @ Matrix.Translation((0, -0.12, -0.02)))


def b_side_table(it):
    x, y = it["pos"]
    r, h = it["diameter"] / 2, it["height"]
    cyl(f"{it['id']}_top", x, y, h - 0.02, h, r, mat(it.get("color", "black")), segs=40)
    if it.get("legs") == "brass_tripod":
        for i in range(3):
            a = i * 2 * math.pi / 3
            m = (Matrix.Translation((x + 0.1 * math.cos(a), y + 0.1 * math.sin(a), 0.0))
                 @ Matrix.Rotation(-math.radians(10), 4, Vector((-math.sin(a), math.cos(a), 0))))
            cyl(f"{it['id']}_leg_{i}", 0, 0, 0.0, h - 0.02, 0.008, mat("brass"), segs=8, matrix=m)
        return
    cyl(f"{it['id']}_stem", x, y, 0.01, h - 0.02, 0.02, mat("black"), segs=12)
    cyl(f"{it['id']}_foot", x, y, 0.0, 0.012, r * 0.7, mat("black"), segs=32)


def b_plant(it):
    x, y, z = it["pos"]
    cyl(f"{it['id']}_vase", x, y, z, z + 0.12, 0.05, mat("glass"), r2=0.035, segs=24)
    green = emissive("leaf", "#6E8F4A", 0.0)
    green.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.6
    cyl(f"{it['id']}_stem", x, y, z + 0.02, z + 0.42, 0.004, green, segs=6)
    for i in range(9):
        a = i * 2.4
        zz = z + 0.2 + i * 0.025
        m = (Matrix.Translation((x + 0.09 * math.cos(a), y + 0.09 * math.sin(a), zz))
             @ Matrix.Rotation(a, 4, "Z") @ Matrix.Rotation(math.radians(25), 4, "Y"))
        sphere(f"{it['id']}_leaf_{i}", (0, 0, 0), (0.1, 0.018, 0.004), green, matrix=m, segs=12)


def b_rug(it):
    x0, x1 = it["x"]
    y0, y1 = it["y"]
    z = 0.004
    quad("Rug", [(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)], mat("rug"),
         uvs=[(0, 0), (1, 0), (1, 1), (0, 1)])


def b_bench(it):
    x0, x1 = it["x"]
    y0, y1 = it["y"]
    h = it["height"]
    box("Bench_base", (x0, x1), (y0, y1), (0.02, h - 0.1), mat("oak"), bevel=0.004)
    box("Bench_frame", (x0 - 0.005, x1), (y0, y1), (h - 0.1, h - 0.07), mat("frame_white"))
    box("Bench_cushion", (x0, x1 - 0.02), (y0, y1), (h - 0.07, h), mat("mustard"), bevel=0.02)
    box("Bench_side_cushion", (x0 + 0.04, x1 - 0.02), (y0 - 0.02, y0 + 0.08), (h, h + 0.45), mat("mustard"), bevel=0.02)
    n = 3
    for i in range(n):
        yc = y0 + (i + 0.5) * (y1 - y0) / n
        m = Matrix.Translation((x0 - 0.001, yc, (h - 0.08) / 2)) @ Matrix.Rotation(math.radians(90), 4, "Y")
        cyl(f"Bench_knob_{i}", 0, 0, -0.004, 0.004, 0.025, mat("black"), segs=16, matrix=m)
    # teal scatter cushion leaning on the wall
    m = Matrix.Translation((x1 - 0.12, (y0 + y1) / 2 + 0.1, h + 0.2)) @ Matrix.Rotation(math.radians(-15), 4, "Y")
    box("Bench_pillow", (-0.07, 0.07), (-0.24, 0.24), (-0.2, 0.2), mat("teal"), subsurf=2, matrix=m)


def b_minibar_unit(it):
    x0, x1 = it["x"]
    y0, y1 = it["y"]
    h = it["height"]
    box("Unit_body", (x0, x1), (y0, y1), (0.0, h), mat("oak"), bevel=0.004)
    box("Unit_top", (x0 - 0.01, x1), (y0 - 0.01, y1), (h, h + 0.03), mat("frame_white"), bevel=0.003)
    box("Unit_back", (x1 - 0.03, x1), (y0, y1), (h + 0.03, h + 0.28), mat("oak"))
    ym = y0 + (y1 - y0) * 0.45
    box("Fridge_front", (x0 - 0.005, x0 + 0.01), (y0 + 0.03, ym), (0.42, h - 0.04), mat("black"))
    box("Shelf_niche", (x0 - 0.002, x0 + 0.01), (ym + 0.03, y1 - 0.03), (0.42, h - 0.04), emissive("niche_dark", "#5A4A3A", 0.0))
    box("Shelf_board", (x0 - 0.01, x0 + 0.2), (ym + 0.03, y1 - 0.03), (0.64, 0.66), mat("oak"))
    for i, yc in enumerate(((y0 + ym) / 2, (ym + y1) / 2)):
        m = Matrix.Translation((x0 - 0.001, yc, 0.2)) @ Matrix.Rotation(math.radians(90), 4, "Y")
        cyl(f"Unit_knob_{i}", 0, 0, -0.004, 0.004, 0.025, mat("black"), segs=16, matrix=m)
    # coffee station
    top = h + 0.03
    box("Coffee_machine", (x1 - 0.3, x1 - 0.06), (y0 + 0.12, y0 + 0.26), (top, top + 0.26), mat("black"), bevel=0.01)
    cyl("Kettle", x1 - 0.18, y0 + 0.42, top, top + 0.2, 0.075, mat("frame_white"), r2=0.06, segs=24)
    box("Tray", (x1 - 0.3, x1 - 0.05), (y0 + 0.52, y1 - 0.05), (top, top + 0.015), mat("black"))


def b_desk(it):
    x0, x1 = it["x"]
    y0, y1 = it["y"]
    h = it["height"]
    box("Desk_top", (x0, x1), (y0, y1), (h - 0.03, h), mat("oak"), bevel=0.003)
    box("Desk_side", (x0, x1), (y0, y0 + 0.03), (0.0, h - 0.03), mat("oak"))
    box("Desk_shelf", (x0, x1), (y0 + 0.03, y1), (0.25, 0.27), mat("oak"))


def b_copper_pendant(it):
    xw, yw = it["wall_x"] - 0.03, it["y"]
    hx, hy, hz = it["hang"]
    cyl("Copper_pipe", xw, yw, 1.0, H - 0.1, 0.012, mat("copper"), segs=12)
    arm_len = xw - hx
    m = Matrix.Translation((hx, hy, H - 0.1)) @ Matrix.Rotation(math.radians(90), 4, "Y")
    cyl("Copper_arm", 0, 0, 0.0, arm_len, 0.012, mat("copper"), segs=12, matrix=m)
    if abs(hy - yw) > 0.01:
        m = Matrix.Translation((xw, yw, H - 0.1)) @ Matrix.Rotation(math.radians(-90), 4, "X")
        cyl("Copper_arm2", 0, 0, 0.0, hy - yw, 0.012, mat("copper"), segs=12, matrix=m)
    cyl("Rattan_cord", hx, hy, hz + 0.32, H - 0.1, 0.004, mat("black"), segs=8)
    cyl("Rattan_cap", hx, hy, hz + 0.3, hz + 0.36, 0.035, mat("rattan"), r2=0.02, segs=16)
    shade = cyl("Rattan_shade", hx, hy, hz, hz + 0.3, 0.17, mat("rattan"), r2=0.035, segs=18, caps=False)
    wireframe(shade, 0.01)
    cyl("Rattan_band", hx, hy, hz, hz + 0.05, 0.172, mat("rattan"), r2=0.162, segs=32, caps=False)
    sphere("Rattan_bulb", (hx, hy, hz + 0.12), (0.04, 0.04, 0.055), emissive("rattan_glow", "#FFC98A", 6.0), segs=12)
    add_point_light("Rattan_light", (hx, hy, hz + 0.1), 18)


def curtain(name, x0, x1, z0, z1, y, folds, depth, material):
    """Pleated curtain: a sine-wave strip hanging at y."""
    bm = bmesh.new()
    n = max(8, int(folds * 8))
    rows = []
    for zz in (z0, z1):
        row = []
        for i in range(n + 1):
            t = i / n
            xx = x0 + (x1 - x0) * t
            yy = y - depth * (0.5 + 0.5 * math.sin(t * folds * 2 * math.pi))
            row.append(bm.verts.new((xx, yy, zz)))
        rows.append(row)
    for i in range(n):
        bm.faces.new((rows[0][i], rows[0][i + 1], rows[1][i + 1], rows[1][i]))
    return finish(name, bm, material, smooth=True)


def b_curtains(it):
    sd = spec["openings"]["window_wall"]["sliding_door"]
    y = D - 0.08
    top = H - 0.08
    box("Curtain_rail", (0.02, W - 0.02), (y - 0.1, y + 0.02), (top, top + 0.03), mat("frame_white"))
    curtain("Curtain_left", 0.02, 0.55, 0.02, top, y - 0.03, 5, 0.09, mat("curtain"))
    curtain("Curtain_right", W - 0.6, W - 0.02, 0.02, top, y - 0.03, 5, 0.09, mat("curtain"))
    curtain("Sheer_left", 0.5, sd[0] - 0.05, 0.02, top, y, 6, 0.05, mat("sheer"))
    curtain("Sheer_right", sd[1] - 0.05, W - 0.55, 0.02, top, y, 3, 0.05, mat("sheer"))


def b_balcony(it):
    y0, y1 = D, D + BD
    box("Balcony_floor", (-0.1, W + 0.1), (y0, y1), (-0.06, -0.001), mat("balcony_floor"))
    wall_x("Balcony_side_l", -0.001, y0, y1, -0.06, H, +1, mat("wall"))
    wall_x("Balcony_side_r", W + 0.001, y0, y1, -0.06, H, -1, mat("wall"))
    box("Balcony_slab_above", (-0.1, W + 0.1), (y0 + 0.1, y1), (H + 0.2, H + 0.4), mat("ceiling"))
    box("Balcony_glass", (0.0, W), (y1 - 0.03, y1 - 0.02), (0.05, 1.05), mat("glass"))
    box("Balcony_rail", (0.0, W), (y1 - 0.045, y1 - 0.005), (1.05, 1.09), mat("frame_white"))
    for i, xp in enumerate((0.0, W / 2, W - 0.04)):
        box(f"Balcony_post_{i}", (xp, xp + 0.04), (y1 - 0.045, y1 - 0.005), (0.0, 1.05), mat("frame_white"))
    # small round table + two rattan chairs
    tx, ty = W * 0.5, y0 + BD * 0.55
    cyl("Balcony_table_top", tx, ty, 0.7, 0.72, 0.28, mat("black"), segs=32)
    cyl("Balcony_table_leg", tx, ty, 0.0, 0.7, 0.02, mat("black"), segs=12)
    for i, (cx, rot) in enumerate(((tx - 0.62, -70.0), (tx + 0.62, 70.0))):
        M = at(cx, ty, rot)
        for j, (lx, ly) in enumerate(((-0.2, -0.2), (0.2, -0.2), (-0.2, 0.2), (0.2, 0.2))):
            cyl(f"Bchair{i}_leg{j}", lx, ly, 0.0, 0.4, 0.012, mat("black"), segs=8, matrix=M)
        cyl(f"Bchair{i}_seat", 0, 0, 0.4, 0.46, 0.26, mat("rattan"), segs=24, matrix=M)
        back = cyl(f"Bchair{i}_back", 0, 0.05, 0.46, 0.85, 0.27, mat("rattan"), r2=0.3, segs=24, caps=False, matrix=M)
        wireframe(back, 0.012)
    # sea view backdrop (unlit image far away, horizon at eye height)
    hw, hh, dist = 400.0, 200.0, 150.0
    horizon_from_top = 0.52
    z_top = 1.45 + horizon_from_top * hh
    quad("Sea_view", [(W / 2 - hw / 2, D + dist, z_top - hh), (W / 2 + hw / 2, D + dist, z_top - hh),
                      (W / 2 + hw / 2, D + dist, z_top), (W / 2 - hw / 2, D + dist, z_top)][::-1],
         mat("sea_view"), uvs=[(0, 1), (1, 1), (1, 0), (0, 0)])


def b_empty(it):
    pass


BUILDERS = {k[2:]: v for k, v in globals().items() if k.startswith("b_")}


# ---------------------------------------------------------------- lights / world / cameras
def add_point_light(name, loc, watts):
    ld = bpy.data.lights.new(name, "POINT")
    ld.energy = watts
    ld.color = (1.0, 0.8, 0.6)
    ld.shadow_soft_size = 0.05
    ob = bpy.data.objects.new(name, ld)
    ob.location = loc
    col.objects.link(ob)


def setup_lighting():
    world = bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.8, 0.86, 0.95, 1.0)
    bg.inputs["Strength"].default_value = 0.8

    sun = bpy.data.lights.new("Sun", "SUN")
    sun.energy = 3.5
    sun.angle = math.radians(2)
    sun_ob = bpy.data.objects.new("Sun", sun)
    sun_ob.rotation_euler = (math.radians(55), 0.0, math.radians(200))
    col.objects.link(sun_ob)

    # Soft daylight pouring in through the window (invisible to camera)
    area = bpy.data.lights.new("Window_fill", "AREA")
    area.shape = "RECTANGLE"
    area.size, area.size_y = W - 0.1, spec["openings"]["window_wall"]["head"] - 0.1
    area.energy = 220
    area.color = (0.95, 0.97, 1.0)
    a_ob = bpy.data.objects.new("Window_fill", area)
    a_ob.location = (W / 2, D - 0.15, 1.2)
    a_ob.rotation_euler = (math.radians(-90), 0.0, 0.0)  # face -y (into room)
    a_ob.visible_camera = False
    a_ob.visible_glossy = False
    col.objects.link(a_ob)


def add_cameras():
    cams = {}
    for name, c in spec["cameras"].items():
        cd = bpy.data.cameras.new(name)
        cd.lens = c["lens"]
        cd.sensor_width = 36
        cd.clip_start = 0.05
        ob = bpy.data.objects.new(f"CAM_{name}", cd)
        ob.location = c["pos"]
        direction = Vector(c["look"]) - Vector(c["pos"])
        ob.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        col.objects.link(ob)
        cams[name] = (ob, c)
    return cams


def setup_render():
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 64
    scene.cycles.use_denoising = True
    scene.render.resolution_x, scene.render.resolution_y = 860, 600
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = -1.1
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for backend in ("OPTIX", "CUDA"):
            try:
                prefs.compute_device_type = backend
                prefs.get_devices()
                if any(d.type == backend for d in prefs.devices):
                    for d in prefs.devices:
                        d.use = d.type == backend
                    scene.cycles.device = "GPU"
                    print(f"[build] Cycles on GPU ({backend})")
                    return
            except TypeError:
                continue
    except Exception as e:  # noqa: BLE001
        print("[build] GPU setup failed, using CPU:", e)
    scene.cycles.device = "CPU"



# ---------------------------------------------------------------- light baking
def bakeable(ob):
    if ob.type != "MESH" or not ob.data.materials:
        return False
    if any(m.type == "WIREFRAME" for m in ob.modifiers):
        return False
    for m in ob.data.materials:
        s = spec["materials"].get(m.name, {})
        if "alpha" in s or "emission" in s or s.get("metallic", 0) > 0.5 or m.name.endswith("glow"):
            return False
    return True


def blur_norm(a, w, r):
    """Box blur of a*w divided by blurred w (keeps blur inside baked UV islands)."""
    import numpy as np

    def box(x):
        for axis in (0, 1):
            c = np.cumsum(np.pad(x, [(r + 1, r) if i == axis else (0, 0) for i in range(x.ndim)], mode="edge"), axis=axis)
            x = (np.take(c, range(2 * r + 1, c.shape[axis]), axis=axis) - np.take(c, range(0, c.shape[axis] - 2 * r - 1), axis=axis)) / (2 * r + 1)
        return x

    num = box(a * w[..., None])
    den = box(w)[..., None]
    return num / np.maximum(den, 1e-6), den[..., 0]


def bake_lightmap():
    import numpy as np

    obs = [o for o in bpy.data.objects if bakeable(o)]
    print(f"[bake] {len(obs)} objects", flush=True)
    bpy.ops.object.select_all(action="DESELECT")
    for o in obs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = obs[0]
    bpy.ops.object.convert(target="MESH")  # apply bevels/subsurf so UVs match exported geometry

    for o in obs:
        if not o.data.uv_layers:
            world_uv(o.data, 1.0)  # keep lightmap as the 2nd UV set (TEXCOORD_1) everywhere
    # Cycles bakes (and re-syncs the scene) once per object, so merge static geometry.
    # Objects the viewer addresses by name stay separate.
    keep = {"Ceiling", "Floor", "Rug", "Balcony_floor", "Balcony_slab_above"}
    rest = [o for o in obs if o.name not in keep]
    bpy.ops.object.select_all(action="DESELECT")
    for o in rest:
        o.select_set(True)
    bpy.context.view_layer.objects.active = rest[0]
    bpy.ops.object.join()
    rest[0].name = rest[0].data.name = "Room_static"
    obs = [bpy.data.objects[n] for n in keep if n in bpy.data.objects] + [bpy.data.objects["Room_static"]]
    bpy.ops.object.select_all(action="DESELECT")
    for o in obs:
        me = o.data
        lm = me.uv_layers.new(name="Lightmap")
        me.uv_layers[0].active_render = True
        me.uv_layers.active = lm
        o.select_set(True)
    bpy.context.view_layer.objects.active = obs[-1]
    print(f"[bake] merged into {len(obs)} objects", flush=True)

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    print("[bake] unwrapping", flush=True)
    bpy.ops.uv.smart_project(angle_limit=math.radians(60), island_margin=0.0, scale_to_bounds=False)
    bpy.ops.uv.average_islands_scale()
    print("[bake] packing islands", flush=True)
    bpy.ops.uv.pack_islands(margin=0.002, rotate=True, shape_method="AABB")
    bpy.ops.object.mode_set(mode="OBJECT")

    img = bpy.data.images.new("Lightmap", BAKE_SIZE, BAKE_SIZE, alpha=True, float_buffer=True)
    img.generated_color = (0, 0, 0, 0)
    added = []
    for m in {m for o in obs for m in o.data.materials}:
        n = m.node_tree.nodes.new("ShaderNodeTexImage")
        n.image = img
        m.node_tree.nodes.active = n
        added.append((m, n))

    setup_render()
    scene.cycles.samples = BAKE_SAMPLES
    scene.render.bake.margin = 0
    scene.render.bake.use_clear = True
    print(f"[bake] baking {BAKE_SIZE}px @ {BAKE_SAMPLES} samples ...", flush=True)
    bpy.ops.object.bake(type="DIFFUSE", pass_filter={"DIRECT", "INDIRECT"}, uv_layer="Lightmap", margin=0, use_clear=True)
    for m, n in added:
        m.node_tree.nodes.remove(n)
    print("[bake] baked, post-processing", flush=True)

    # Denoise (island-aware blur), fill gutters, encode to 8-bit sRGB.
    px = np.array(img.pixels[:], dtype=np.float32).reshape(BAKE_SIZE, BAKE_SIZE, 4)
    rgb, mask = px[..., :3], (px[..., 3] > 0.5).astype(np.float32)
    r = max(1, BAKE_SIZE // 2048)
    den_rgb, _ = blur_norm(rgb, mask, r)
    rgb = np.where(mask[..., None] > 0, den_rgb, 0)
    filled, cov = rgb.copy(), mask.copy()
    for _ in range(4):  # grow islands outward so mip/bilinear sampling doesn't pick up black
        b, bc = blur_norm(filled, cov, 3 * r)
        grow = (cov == 0) & (bc > 0)
        filled[grow] = b[grow]
        cov = np.maximum(cov, grow.astype(np.float32))
    x = np.clip(filled / LIGHTMAP_SCALE, 0, 1)
    srgb = np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)
    out = bpy.data.images.new("Lightmap_out", BAKE_SIZE, BAKE_SIZE, alpha=False)
    out.colorspace_settings.name = "Non-Color"
    out.pixels = np.concatenate([srgb, np.ones_like(srgb[..., :1])], axis=-1).ravel()
    path = OUT / f"{spec['id']}_lightmap.webp"
    scene.render.image_settings.file_format = "WEBP"
    scene.render.image_settings.quality = 88
    scene.render.image_settings.color_mode = "RGB"
    out.save_render(str(path), scene=scene)
    web_models = ROOT / "web" / "models"
    web_models.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, web_models / path.name)
    lit = filled[mask > 0]
    print(f"[bake] wrote {path} ({path.stat().st_size / 1e6:.2f} MB); irradiance p50={np.median(lit):.2f} p99={np.percentile(lit, 99):.2f}")


# ---------------------------------------------------------------- export
def export_glb():
    for ob in bpy.data.objects:
        ob.select_set(ob.type == "MESH")
    path = OUT / f"{spec['id']}.glb"
    bpy.ops.export_scene.gltf(
        filepath=str(path),
        export_format="GLB",
        use_selection=True,
        export_apply=True,
        export_yup=True,
        export_image_format="AUTO",
        export_extras=True,
    )
    web_models = ROOT / "web" / "models"
    web_models.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, web_models / path.name)
    print(f"[build] exported {path} ({path.stat().st_size / 1e6:.2f} MB)")


# ---------------------------------------------------------------- main
def main():
    build_shell()
    for it in spec["items"]:
        BUILDERS[it["type"]](it)
    setup_lighting()
    cams = add_cameras()
    if DO_BAKE:
        bake_lightmap()
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / f"{spec['id']}.blend"))
    print(f"[build] {len([o for o in bpy.data.objects if o.type == 'MESH'])} meshes")
    if DO_EXPORT:
        export_glb()
    if DO_RENDER:
        setup_render()
        rdir = OUT / "renders"
        rdir.mkdir(exist_ok=True)
        for name, (ob, c) in cams.items():
            if CAMS and name not in CAMS:
                continue
            hidden = [bpy.data.objects[n] for n in c.get("hide", []) if n in bpy.data.objects]
            for h in hidden:
                h.hide_render = True
            scene.camera = ob
            scene.render.filepath = str(rdir / f"{name}.png")
            bpy.ops.render.render(write_still=True)
            for h in hidden:
                h.hide_render = False
            print(f"[build] rendered {name}")


main()
