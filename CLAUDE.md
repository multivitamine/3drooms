# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A pipeline that turns a hotel's marketing photos (+ its published floor plan) into an interactive 3D room for the website. There is no build system, test suite or linter. The "build" is a headless Blender script, and verification is visual: compare renders with the photos and screenshot the viewer.

## Commands

Blender 4.5 LTS is used (5.2 is also installed but untested):
```
"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe" -b -P blender/build_room.py -- specs/room-01.json [flags]
```
- Flags:
  - `--no-render`: skip the Cycles preview renders.
  - `--no-export`: skip the `.glb` export.
  - `--cams=photo_kamer,dollhouse`: render only these cameras.
  - `--bake`: bake the lightmap. Takes about 15 min, tuned with `--bake-size=2048 --bake-samples=256`.
- Python `print` output is buffered when piped, so run with `PYTHONUNBUFFERED=1` to see `[build]`/`[bake]` progress lines.
- `python tools/make_textures.py`: regenerates `assets/textures/*.png`. Needs system Python with Pillow + numpy; Blender's Python doesn't have Pillow.
- `python tools/compare.py room-01`: writes `output/room-01/compare.png`, each photo next to the render from its matching camera. This is the main check after changing the spec.
- Viewer: `cd web && python -m http.server 8765`, then open `http://127.0.0.1:8765/viewer.html`.
  - URL params: `?spot=0..3`, `?mode=doll`, `?lighting=live`, `?exposure=0.32`, `?model=models/x.glb`, `?lightmap=...`.
- Headless screenshot of the viewer. It is slow (~90 s per shot with software WebGL), so take one at a time:
  ```
  chrome.exe --headless=new --use-angle=swiftshader --enable-unsafe-swiftshader --ignore-gpu-blocklist --window-size=1200,800 --virtual-time-budget=40000 --screenshot=out.png "http://127.0.0.1:8765/viewer.html?spot=1"
  ```

## Architecture

**`specs/<room>.json` is the single source of truth.**
- Coordinates are in metres, Blender style, Z up:
  - x = room width: 0 is the bed/mural wall, `W` is the bench/minibar wall.
  - y = room depth: 0 is the entrance wall, `D` is the window wall.
  - The balcony is at y > D.
- Every item has a `source` tag: `photo` (seen in a photo), `plan` (only in the floor plan) or `invented`.
  - Draft policy agreed with the user: anything no photo shows is left empty or closed. The bathroom is a closed box, and the TV is `type: "empty"`.
- The room scale comes from the hotel's floor-plan SVG (`input/room-01/plan/`), assuming a 180 cm bed: 1 m = 230 px when the plan is rendered at 4×.

**`blender/build_room.py`:**
- Each spec item's `type` maps to a function `b_<type>` found by scanning `globals()` (`BUILDERS`). To add a furniture type, add a `b_<type>(it)` function; nothing else needs registering.
- Materials come from `spec.materials`.
- Meshes are built directly in world coordinates. Tiling textures get box-projected UVs in metres (`world_uv`, driven by the material's `tex_size`).
- Walls, ceiling and mural are **single-sided planes with backface culling**. That is why the web dollhouse view can see through the walls nearest the camera. Cycles ignores culling, so the dollhouse camera uses a `hide` list in the spec instead.

**Lighting bake (`--bake`, see `bake_lightmap()`):**
- It merges all bakeable meshes into `Room_static`, except the objects the viewer looks up by name (`keep` set).
  - The merge exists because Cycles re-syncs the whole scene for every object it bakes; with 121 separate objects the bake was impractically slow.
- It adds a second UV layer `Lightmap`, which exports as `TEXCOORD_1` and appears in three.js as `uv1`.
  - Meshes without UVs first get a dummy `UVMap`, so the lightmap is always the second UV set.
- It unwraps with smart_project → average_islands_scale → `pack_islands(shape_method="AABB")`. The default CONCAVE packer effectively hangs on this many islands.
- It bakes DIFFUSE lighting only (direct + indirect, without surface colour), smooths it without bleeding across UV islands, and writes an sRGB WebP storing `irradiance / LIGHTMAP_SCALE`.
- Not baked: transparent, emissive, metallic and wireframe objects. The viewer lights those with an environment map.

**`web/viewer.html`** (three.js from a CDN, no build step):
- `B(x, y, z)` converts spec/Blender coordinates to three.js coordinates `(x, z, -y)`. Viewpoints (`SPOTS`) are written in spec coordinates.
- If `<model>_lightmap.webp` loads, every mesh that has `uv1` gets a cloned material with `lightMap`, and live lights and shadows are turned off. Otherwise live lights are used.
- The viewer depends on names coming from the Blender side:
  - Material names: `glass`, `sheer`, `sea_view`, `*_glow`.
  - Object names: `Ceiling`, `Balcony_slab_above` and `Sea_view` are hidden in dollhouse mode. `Floor`, `Rug` and `Balcony_floor` are the click-to-walk targets.
  - Renaming any of these in Blender breaks the viewer.

## Gotchas

- **`LIGHTMAP_SCALE` is defined twice** (`build_room.py` and `viewer.html`); the two values must match.
- **The `.glb` and `_lightmap.webp` must come from the same `--bake` run.** A build without `--bake` overwrites the `.glb` (which then has no `uv1`) but leaves the old lightmap in place. The viewer then switches into baked mode with nothing baked, and the room renders nearly unlit. Rebake, or use `?lighting=live`.
- **Every export is copied to `web/models/`**, which is committed so the viewer works right after cloning. Regenerated `output/` artifacts are committed too. `.blend1` files are gitignored.
- **Input photos are low resolution (860×600).** The mural texture is a crop of one photo (`make_textures.py`, `mural()`).
