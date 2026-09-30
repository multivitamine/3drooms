"""Side-by-side sheet: source photo (left) vs render from the matching camera (right).

    python tools/compare.py [room-id]
Writes output/<room>/compare.png
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
room = sys.argv[1] if len(sys.argv) > 1 else "room-01"
spec = json.loads((ROOT / "specs" / f"{room}.json").read_text(encoding="utf-8"))
photos = ROOT / "input" / room / "photos"
renders = ROOT / "output" / room / "renders"

rows = []
for name, cam in spec["cameras"].items():
    r = renders / f"{name}.png"
    if not r.exists():
        continue
    note = cam.get("note", "")
    photo = None
    if note.startswith("matches"):
        stem = note.split()[-1]
        hits = [p for p in photos.iterdir() if p.stem.endswith(stem.replace(".webp", "")) or p.name == stem]
        photo = Image.open(hits[0]).convert("RGB").resize((860, 600)) if hits else None
    rows.append((name, photo, Image.open(r).convert("RGB").resize((860, 600))))

sheet = Image.new("RGB", (1720, 600 * len(rows)), "white")
draw = ImageDraw.Draw(sheet)
for i, (name, photo, render) in enumerate(rows):
    if photo:
        sheet.paste(photo, (0, i * 600))
    sheet.paste(render, (860, i * 600))
    draw.text((10, i * 600 + 10), f"{name}: photo | render", fill="red")
out = ROOT / "output" / room / "compare.png"
sheet.save(out)
print("wrote", out)
