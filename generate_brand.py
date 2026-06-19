"""
Obscura — Logo (1000x1000) + YouTube Banner (2560x1440)
Saves to pipeline/assets/
"""
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import numpy as np
import math, os
from pathlib import Path

OUT = Path("pipeline/assets")
OUT.mkdir(parents=True, exist_ok=True)

# ── Palette ───────────────────────────────────────────────────────────────────
BG       = (4,  4, 14)          # near-black background
BG2      = (16, 5, 42)          # dark violet (gradient center)
GOLD     = (212, 175, 55)       # rich gold
GOLD_L   = (255, 220, 100)      # bright gold highlight
WHITE    = (255, 255, 255)
DIM      = (160, 150, 200)      # muted lavender (tagline)
PURPLE   = (70,  18, 140)       # deep purple (iris)

# ── Font helper ───────────────────────────────────────────────────────────────
def font(size, bold=True):
    candidates = (
        ["C:/Windows/Fonts/impact.ttf",
         "C:/Windows/Fonts/arialbd.ttf",
         "C:/Windows/Fonts/calibrib.ttf",
         "C:/Windows/Fonts/trebucbd.ttf"]
        if bold else
        ["C:/Windows/Fonts/arial.ttf",
         "C:/Windows/Fonts/calibri.ttf",
         "C:/Windows/Fonts/tahoma.ttf"]
    )
    for p in candidates:
        if os.path.exists(p):
            try: return ImageFont.truetype(p, size)
            except: pass
    return ImageFont.load_default()

# ── Numpy radial gradient ─────────────────────────────────────────────────────
def radial_bg(w, h, inner, outer):
    cx, cy = w / 2, h / 2
    y_idx, x_idx = np.mgrid[0:h, 0:w]
    dist = np.sqrt((x_idx - cx)**2 + (y_idx - cy)**2)
    dist = np.clip(dist / (max(w, h) * 0.72), 0, 1)
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    for c in range(3):
        arr[:, :, c] = (inner[c] * (1 - dist) + outer[c] * dist).astype(np.uint8)
    return Image.fromarray(arr, "RGB")

# ── Glow ring helper ──────────────────────────────────────────────────────────
def glow_ring(draw, cx, cy, r, color, width=3, layers=5, start_alpha=90):
    for i in range(layers, 0, -1):
        a   = int(start_alpha * (i / layers) ** 2)
        ri  = r + (layers - i) * 6
        col = (*color, a)
        overlay = Image.new("RGBA", draw.im.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.ellipse([cx - ri, cy - ri, cx + ri, cy + ri],
                   outline=col, width=max(1, width - 1))
        return overlay  # just return one layer for simplicity

def alpha_circle(base_img, cx, cy, r, color, alpha, width=2):
    overlay = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    d.ellipse([cx - r, cy - r, cx + r, cy + r],
              outline=(*color, alpha), width=width)
    return Image.alpha_composite(base_img.convert("RGBA"), overlay).convert("RGB")

# ─────────────────────────────────────────────────────────────────────────────
#  LOGO  1000 × 1000
# ─────────────────────────────────────────────────────────────────────────────
W, H = 1000, 1000
logo = radial_bg(W, H, BG2, BG)
draw = ImageDraw.Draw(logo)

cx, cy = W // 2, H // 2

# Outer decorative ring (faint gold)
for dr, alpha in [(400, 25), (395, 45), (390, 70)]:
    logo = alpha_circle(logo, cx, cy, dr, GOLD, alpha, width=1)
draw = ImageDraw.Draw(logo)

# Eye outer circle
EYE = 270
draw.ellipse([cx-EYE, cy-EYE, cx+EYE, cy+EYE],
             fill=(10, 4, 28), outline=GOLD, width=5)

# Iris
IRIS = 155
draw.ellipse([cx-IRIS, cy-IRIS, cx+IRIS, cy+IRIS],
             fill=PURPLE, outline=(180, 140, 40), width=3)

# Eight spokes (eye lines)
for deg in range(0, 360, 45):
    ang = math.radians(deg)
    x1 = cx + int((IRIS + 8)  * math.cos(ang))
    y1 = cy + int((IRIS + 8)  * math.sin(ang))
    x2 = cx + int((EYE  - 12) * math.cos(ang))
    y2 = cy + int((EYE  - 12) * math.sin(ang))
    draw.line([x1, y1, x2, y2], fill=(*GOLD, 80), width=1)

# Pupil glow (layered circles)
for r, col in [(72, (90, 30, 170)),
               (55, (110, 40, 200)),
               (38, GOLD),
               (18, GOLD_L),
               ( 6, WHITE)]:
    draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=col)

# Horizontal slit across iris (camera-shutter feel)
slit_h = 12
draw.rectangle([cx - IRIS + 10, cy - slit_h,
                cx + IRIS - 10, cy + slit_h],
               fill=(4, 4, 14))
# Restore pupil center
for r, col in [(38, GOLD), (18, GOLD_L), (6, WHITE)]:
    draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=col)

# ── "OBSCURA" text ────────────────────────────────────────────────────────────
fn_big  = font(118, bold=True)
fn_tag  = font(34,  bold=False)

text = "OBSCURA"
bb   = draw.textbbox((0, 0), text, font=fn_big)
tw   = bb[2] - bb[0]
tx   = (W - tw) // 2
ty   = cy + EYE + 52

# Subtle drop shadow
draw.text((tx + 3, ty + 3), text, fill=(20, 8, 50),   font=fn_big)
draw.text((tx,     ty),     text, fill=WHITE,          font=fn_big)

# Gold underline
line_y = ty + (bb[3] - bb[1]) + 10
draw.rectangle([tx, line_y, tx + tw, line_y + 3], fill=GOLD)

# Tagline
tag  = "HIDDEN  ·  REVEALED"
bbt  = draw.textbbox((0, 0), tag, font=fn_tag)
draw.text(((W - (bbt[2]-bbt[0])) // 2, line_y + 12), tag,
          fill=GOLD, font=fn_tag)

logo = logo.filter(ImageFilter.SMOOTH)
logo.save(OUT / "obscura_logo.png", quality=97)
print("OK Logo saved:", OUT / "obscura_logo.png")

# ─────────────────────────────────────────────────────────────────────────────
#  BANNER  2560 × 1440
# ─────────────────────────────────────────────────────────────────────────────
BW, BH = 2560, 1440
banner = radial_bg(BW, BH, BG2, BG)
bd     = ImageDraw.Draw(banner)

bcx, bcy = BW // 2, BH // 2 - 80

# Faint decorative rings (background atmosphere)
for r, a in [(700, 12), (680, 20), (650, 30)]:
    banner = alpha_circle(banner, bcx, bcy, r, GOLD, a, width=1)
bd = ImageDraw.Draw(banner)

# Horizontal accent lines
for y_off, alpha in [(-460, 35), (460, 35)]:
    y = bcy + y_off
    bd.line([220, y, BW - 220, y], fill=(*GOLD, alpha), width=1)

# Eye element (compact, above title)
BE = 130
bd.ellipse([bcx-BE, bcy-BE, bcx+BE, bcy+BE],
           fill=(10, 4, 28), outline=GOLD, width=4)
BI = 75
bd.ellipse([bcx-BI, bcy-BI, bcx+BI, bcy+BI],
           fill=PURPLE, outline=(180, 140, 40), width=2)
for deg in range(0, 360, 45):
    ang = math.radians(deg)
    x1 = bcx + int((BI + 5)  * math.cos(ang))
    y1 = bcy + int((BI + 5)  * math.sin(ang))
    x2 = bcx + int((BE - 8)  * math.cos(ang))
    y2 = bcy + int((BE - 8)  * math.sin(ang))
    bd.line([x1, y1, x2, y2], fill=(*GOLD, 70), width=1)
slit_bh = 7
bd.rectangle([bcx - BI + 6, bcy - slit_bh, bcx + BI - 6, bcy + slit_bh],
             fill=(4, 4, 14))
for r, col in [(22, GOLD), (10, GOLD_L), (4, WHITE)]:
    bd.ellipse([bcx-r, bcy-r, bcx+r, bcy+r], fill=col)

# ── "OBSCURA" main title ──────────────────────────────────────────────────────
fn_title  = font(210, bold=True)
fn_sub    = font(58,  bold=False)
fn_handle = font(44,  bold=False)

title = "OBSCURA"
bb_t  = bd.textbbox((0, 0), title, font=fn_title)
ttw   = bb_t[2] - bb_t[0]
tth   = bb_t[3] - bb_t[1]
ttx   = (BW - ttw) // 2
tty   = bcy + BE + 40

bd.text((ttx + 5, tty + 5), title, fill=(20, 8, 50), font=fn_title)
bd.text((ttx,     tty),     title, fill=WHITE,        font=fn_title)

# Gold underline bar
bar_y = tty + tth + 12
bd.rectangle([ttx, bar_y, ttx + ttw, bar_y + 5], fill=GOLD)

# Tagline
sub = "HIDDEN  ·  REVEALED  ·  URDU FACTS CHANNEL"
bb_s = bd.textbbox((0, 0), sub, font=fn_sub)
bd.text(((BW - (bb_s[2]-bb_s[0])) // 2, bar_y + 18), sub,
        fill=GOLD, font=fn_sub)

# Social handle
handle = "@Obscura"
bb_h   = bd.textbbox((0, 0), handle, font=fn_handle)
bd.text(((BW - (bb_h[2]-bb_h[0])) // 2, bar_y + 92), handle,
        fill=DIM, font=fn_handle)

# Safe-zone guide note (remove before uploading)
# YouTube safe zone: center 1546×423 of the 2560×1440 canvas

banner = banner.filter(ImageFilter.SMOOTH)
banner.save(OUT / "obscura_banner.png", quality=97)
print("OK Banner saved:", OUT / "obscura_banner.png")
