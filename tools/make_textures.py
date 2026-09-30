"""Generate draft textures for the room from the source photos + simple procedural patterns.

Run with system Python (needs Pillow + numpy):
    python tools/make_textures.py
Writes PNGs to assets/textures/.
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
PHOTOS = ROOT / "input" / "room-01" / "photos"
OUT = ROOT / "assets" / "textures"
OUT.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(7)


def hex_rgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)


def noise(size, scale, octaves=4):
    """Cheap value noise: sum of upscaled random grids."""
    acc = np.zeros((size, size), np.float32)
    amp, total = 1.0, 0.0
    for o in range(octaves):
        cells = max(2, int(size / scale * 2 ** o))
        grid = rng.random((cells, cells)).astype(np.float32)
        img = Image.fromarray((grid * 255).astype(np.uint8)).resize((size, size), Image.BICUBIC)
        acc += amp * (np.asarray(img, np.float32) / 255.0)
        total += amp
        amp *= 0.5
    return acc / total


def save(arr, name):
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(OUT / name)
    print("wrote", OUT / name)


def mural():
    # Frontal-ish crop of the dune/sea wallpaper from photo 2 (above the pillows).
    src = Image.open(PHOTOS / "cbh-860x600-beach-comfort-kamer-2.webp").convert("RGB")
    crop = src.crop((0, 0, 330, 300)).resize((1024, 980), Image.LANCZOS)
    # Pad top with the pale sky so the mural reaches the ceiling.
    out = Image.new("RGB", (1024, 1024), tuple(int(c) for c in np.asarray(crop)[4:12].mean((0, 1))))
    out.paste(crop, (0, 44))
    out.filter(ImageFilter.SMOOTH).save(OUT / "mural.png")
    print("wrote", OUT / "mural.png")


def floor_tiles():
    # 1024px = 1.2 m -> 2x2 tiles of 60 cm, warm light stone.
    s = 1024
    base = hex_rgb("#D6CAB6")
    n = noise(s, 64)[..., None]
    fine = noise(s, 8, 2)[..., None]
    img = base * (0.9 + 0.12 * n + 0.05 * fine)
    # per-tile tint
    for ty in range(2):
        for tx in range(2):
            img[ty * 512:(ty + 1) * 512, tx * 512:(tx + 1) * 512] *= 0.96 + 0.08 * rng.random()
    grout = hex_rgb("#B9AC98")
    for k in (0, 511, 512, 1023):
        img[k, :] = grout
        img[:, k] = grout
    save(img, "floor_tiles.png")


def oak():
    # 1024px = 1 m, grain along V.
    s = 1024
    base = hex_rgb("#B79C7E")
    x = np.linspace(0, 1, s, dtype=np.float32)[None, :]
    warp = noise(s, 256, 3)
    grain = np.sin((x * 90 + warp * 12) * np.pi) * 0.5 + 0.5
    streak = noise(s, 32, 2)
    img = base[None, None, :] * (0.86 + 0.1 * grain[..., None] + 0.08 * streak[..., None])
    # stretch noise along grain direction
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).filter(ImageFilter.BoxBlur(1))
    im = im.resize((s, s // 4)).resize((s, s), Image.BICUBIC)
    im.save(OUT / "oak.png")
    print("wrote", OUT / "oak.png")


def rug():
    # Faded vintage rug, 1024 x 1472 (1.6 x 2.3 m).
    w, h = 1024, 1472
    field = hex_rgb("#C9CED3")
    blue = hex_rgb("#6D86A3")
    dark = hex_rgb("#3E5570")
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    u, v = (xx / w - 0.5) * 2, (yy / h - 0.5) * 2
    img = np.broadcast_to(field, (h, w, 3)).copy()
    # borders
    d = np.minimum(np.minimum(xx, w - xx), np.minimum(yy, h - yy))
    img[(d > 40) & (d < 110)] = blue
    img[(d > 55) & (d < 95) & ((xx + yy) % 40 < 20)] = dark
    img[(d > 120) & (d < 130)] = dark
    # central medallion + diamond lattice
    r = np.sqrt((u * 1.1) ** 2 + (v * 0.8) ** 2)
    img[(r < 0.35) & (r > 0.3)] = dark
    img[r < 0.18] = blue
    lattice = (np.abs(np.sin(u * 9) + np.sin(v * 13)) < 0.25) & (d > 140)
    img[lattice] = img[lattice] * 0.5 + blue * 0.5
    # heavy fade / wear
    wear = noise(1024, 48)[..., None]
    wear = np.asarray(Image.fromarray((wear[..., 0] * 255).astype(np.uint8)).resize((w, h)), np.float32)[..., None] / 255
    img = img * (0.75 + 0.2 * wear) + field * (0.25 - 0.2 * wear) + 18
    save(img, "rug.png")


def sea_view():
    # Backdrop seen through the window: sky, horizon, North Sea, beach.
    w, h = 2048, 1024
    yy = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    sky = hex_rgb("#F4F6F7") * (1 - yy) + hex_rgb("#D9E3EA") * yy
    img = np.broadcast_to(sky, (h, w, 3)).copy()
    horizon, shore = int(h * 0.52), int(h * 0.74)
    sea_t = np.linspace(0, 1, shore - horizon, dtype=np.float32)[:, None, None]
    sea = hex_rgb("#8FA6B3") * (1 - sea_t) + hex_rgb("#B7C4C6") * sea_t
    ripples = noise(1024, 12, 2)
    ripples = np.asarray(Image.fromarray((ripples * 255).astype(np.uint8)).resize((w, shore - horizon)), np.float32)[..., None] / 255
    img[horizon:shore] = sea * (0.94 + 0.1 * ripples)
    sand_t = np.linspace(0, 1, h - shore, dtype=np.float32)[:, None, None]
    img[shore:] = hex_rgb("#E3D6BD") * (1 - sand_t) + hex_rgb("#D8C7A6") * sand_t
    save(img, "sea_view.png")


if __name__ == "__main__":
    mural()
    floor_tiles()
    oak()
    rug()
    sea_view()
