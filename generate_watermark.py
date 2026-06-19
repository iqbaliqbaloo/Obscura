"""Generate pipeline/assets/watermark.png — semi-transparent eye icon for video overlay."""
from PIL import Image, ImageDraw, ImageFont
import numpy as np, math, os
from pathlib import Path

OUT = Path("pipeline/assets")
OUT.mkdir(parents=True, exist_ok=True)

GOLD   = (212, 175, 55)
GOLD_L = (255, 220, 100)
WHITE  = (255, 255, 255)
PURPLE = (70,  18, 140)
BG2    = (16,   5,  42)

W, H   = 300, 300
ALPHA  = 170        # overall opacity (0=invisible, 255=fully opaque)

img  = Image.new("RGBA", (W, H), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

cx, cy = W // 2, H // 2

# Faint dark circle background so the eye reads on any video
for r, a in [(140, 30), (130, 50), (120, 70)]:
    draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=(*BG2, a))

# Outer ring
draw.ellipse([cx-110, cy-110, cx+110, cy+110], outline=(*GOLD, ALPHA), width=3)

# Eye outer
EYE = 90
draw.ellipse([cx-EYE, cy-EYE, cx+EYE, cy+EYE],
             fill=(*BG2, ALPHA), outline=(*GOLD, ALPHA), width=3)

# Iris
IRIS = 52
draw.ellipse([cx-IRIS, cy-IRIS, cx+IRIS, cy+IRIS],
             fill=(*PURPLE, ALPHA), outline=(180, 140, 40, ALPHA), width=2)

# Spokes
for deg in range(0, 360, 45):
    ang = math.radians(deg)
    x1 = cx + int((IRIS + 4) * math.cos(ang))
    y1 = cy + int((IRIS + 4) * math.sin(ang))
    x2 = cx + int((EYE - 6)  * math.cos(ang))
    y2 = cy + int((EYE - 6)  * math.sin(ang))
    draw.line([x1, y1, x2, y2], fill=(*GOLD, 60), width=1)

# Horizontal slit
draw.rectangle([cx - IRIS + 4, cy - 6, cx + IRIS - 4, cy + 6],
               fill=(*BG2, ALPHA))

# Pupil layers
for r, col in [(22, GOLD), (10, GOLD_L), (4, WHITE)]:
    draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=(*col, ALPHA))

img.save(OUT / "watermark.png")
print("OK Watermark saved:", OUT / "watermark.png")
