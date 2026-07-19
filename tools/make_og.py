#!/usr/bin/env python3
"""Generate the 1200x630 Open Graph card for hdrify.

Pure Pillow, no network. Matches the site palette:
  bg #08080a · fg #ececf0 · dim #7d7d87 · line #232329 · accent #8b7bff
Run:  python3 tools/make_og.py
"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1200, 630
BG = (8, 8, 10)
FG = (236, 236, 240)
DIM = (125, 125, 135)
LINE = (35, 35, 41)
ACC = (139, 123, 255)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, os.pardir, "og.png")

SFNS = "/System/Library/Fonts/SFNS.ttf"
FALLBACK = "/System/Library/Fonts/HelveticaNeue.ttc"


def font(size, weight=None):
    """SF Pro at a given optical size, with a graceful fallback."""
    try:
        f = ImageFont.truetype(SFNS, size)
        if weight:
            try:
                f.set_variation_by_name(weight)
            except Exception:
                pass
        return f
    except Exception:
        return ImageFont.truetype(FALLBACK, size)


def radial_glow(size, colour, strength=1.0):
    """A soft circular bloom on a transparent layer — the 'HDR' motif."""
    d = size * 2
    layer = Image.new("L", (d, d), 0)
    dr = ImageDraw.Draw(layer)
    steps = 90
    for i in range(steps, 0, -1):
        t = i / steps
        r = size * t
        a = int(255 * strength * (1 - t) ** 2.2)
        dr.ellipse((size - r, size - r, size + r, size + r), fill=a)
    layer = layer.filter(ImageFilter.GaussianBlur(size / 9))
    tint = Image.new("RGB", (d, d), colour)
    out = Image.new("RGBA", (d, d))
    out.paste(tint, (0, 0))
    out.putalpha(layer)
    return out


img = Image.new("RGB", (W, H), BG)

# --- ambient light: a violet bloom bleeding in from the right ---------------
img.paste(
    radial_glow(430, (120, 104, 255), 0.55),
    (W - 470, -150),
    radial_glow(430, (120, 104, 255), 0.55),
)
warm = radial_glow(300, (255, 176, 96), 0.30)
img.paste(warm, (W - 330, H - 300), warm)

d = ImageDraw.Draw(img, "RGBA")

# --- hairline frame, echoing the site's 1px borders ------------------------
d.rectangle((48, 44, W - 49, H - 45), outline=LINE, width=1)

# --- the SDR -> HDR ramp: a strip that runs from flat grey to blown-out ----
BX, BY, BW, BH = 96, 132, 640, 10
for x in range(BW):
    t = x / (BW - 1)
    # sits at SDR white for the first half, then climbs into the headroom
    k = max(0.0, (t - 0.42) / 0.58)
    lift = k ** 0.75
    r = int(150 + 105 * lift)
    g = int(150 + 100 * lift)
    b = int(155 + 100 * lift)
    d.rectangle((BX + x, BY, BX + x + 1, BY + BH), fill=(r, g, b))
# bloom spilling off the bright end
spill = radial_glow(130, (255, 252, 246), 0.62)
img.paste(spill, (BX + BW - 165, BY - 125), spill)
d = ImageDraw.Draw(img, "RGBA")

f_lbl = font(15, "Medium")
d.text((BX, BY - 30), "SDR WHITE", font=f_lbl, fill=DIM)
w_hdr = d.textlength("BRIGHTER THAN WHITE", font=f_lbl)
d.text((BX + BW - w_hdr, BY - 30), "BRIGHTER THAN WHITE", font=f_lbl, fill=ACC)

# --- wordmark + headline ---------------------------------------------------
f_mark = font(30, "Semibold")
d.text((96, 196), "hdrify", font=f_mark, fill=FG)
d.text((96 + d.textlength("hdrify", font=f_mark) + 16, 202),
       "free · in-browser · no upload", font=font(20, "Regular"), fill=DIM)

f_h = font(74, "Bold")
d.text((96, 258), "Make any photo glow", font=f_h, fill=FG)
d.text((96, 346), "past white.", font=f_h, fill=ACC)

f_p = font(24, "Regular")
d.text((96, 452),
       "Turn a JPEG or PNG into an Ultra HDR image with a gain map, so it renders",
       font=f_p, fill=DIM)
d.text((96, 486),
       "far above SDR white on Android, MacBook XDR, iPhone and iPad.",
       font=f_p, fill=DIM)

# --- footer chips ----------------------------------------------------------
f_c = font(18, "Medium")
x = 96
for label in ("Ultra HDR + gain map", "Runs on-device", "No signup"):
    tw = d.textlength(label, font=f_c)
    d.rounded_rectangle((x, 530, x + tw + 34, 570), radius=20,
                        fill=(20, 20, 25), outline=LINE)
    d.text((x + 17, 541), label, font=f_c, fill=DIM)
    x += tw + 34 + 12

img.save(OUT, "PNG", optimize=True)
print("wrote", os.path.normpath(OUT), img.size)
