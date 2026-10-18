"""Draw app/icon.png (256 px) and app/icon.ico (16-256 px): a drawing sheet with a red pencil on a blue tile.
Run once when the icon changes:  python tools/make_icon.py"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "app"
S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([6, 6, S - 6, S - 6], radius=48, fill=(31, 78, 121, 255))             # blue tile
d.polygon([(60, 40), (160, 40), (196, 76), (196, 216), (60, 216)], fill=(255, 255, 255, 255))   # sheet
d.polygon([(160, 40), (160, 76), (196, 76)], fill=(200, 214, 229, 255))                   # folded corner
for y in (104, 132, 160, 188):                                                            # drawing lines
    d.line([(82, y), (174 if y != 188 else 130, y)], fill=(31, 78, 121, 255), width=8)
d.line([(96, 206), (214, 88)], fill=(220, 53, 69, 255), width=22)                         # pencil
d.polygon([(84, 220), (90, 196), (108, 212)], fill=(255, 205, 120, 255))                  # pencil tip
img.save(OUT / "icon.png")
img.save(OUT / "icon.ico", format="ICO",
         sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icons written to", OUT)
