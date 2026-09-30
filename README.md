# 3D hotel rooms

Turns a few marketing photos (+ the hotel's published floor plan) into an interactive 3D room for the website.

## Pipeline
1. `input/<room>/photos/`, `input/<room>/plan/`: source material.
2. `specs/<room>.json`: the room written down: size, openings, furniture, colours, cameras.
   Every item has `source`: `photo` (seen), `plan` (floor plan only) or `invented`.
3. `python tools/make_textures.py`: mural crop, floor, oak, rug and sea textures → `assets/textures/`.
4. Build + export + preview renders (Blender 4.5, headless):
   ```
   "C:\Program Files\Blender Foundation\Blender 4.5\blender.exe" -b -P blender/build_room.py -- specs/room-01.json
   ```
   Flags: `--no-render`, `--no-export`, `--cams=photo_kamer,dollhouse`,
   `--bake` (bake lighting into `<room>_lightmap.webp`, ~15 min; `--bake-size=2048 --bake-samples=256`).
   Output: `output/<room>/<room>.blend`, `.glb` (also copied to `web/models/`), `renders/*.png`.
5. `python tools/compare.py room-01` → `output/room-01/compare.png` (photo vs render per camera).

## Viewer
```
cd web && python -m http.server 8765
```
Open http://127.0.0.1:8765/viewer.html. Options: `?spot=0..3`, `?mode=doll`, `?model=models/other.glb`,
`?lighting=live` (ignore the lightmap), `?exposure=0.32`.
The `.glb` and `_lightmap.webp` must come from the same `--bake` run (the lightmap uses the model's 2nd UV set).
Embed on the site: upload `web/` and use `<iframe src=".../viewer.html" style="width:100%;height:600px;border:0" allow="fullscreen"></iframe>`.

## Draft status (room-01, Beach Comfort)
- Size estimated from floor plan assuming a 180 cm bed: 3.3 × 5.88 m, ceiling 2.6 m.
- Left empty/closed on purpose: bathroom interior, wardrobe interior, TV, entrance side details.
- Lighting: baked (Cycles, 2048 px lightmap, 0.18 MB).
- Not done yet: real CC0 furniture models, higher-res mural.
