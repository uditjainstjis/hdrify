#!/usr/bin/env python3
"""hdrify — turn any image or video into super-bright HDR media that glows on
MacBook XDR / iPhone displays (and falls back gracefully everywhere else).

Images -> Ultra HDR JPEG (SDR base + ISO gain map, via libultrahdr)
Videos -> HEVC 10-bit PQ MP4 (via ffmpeg), same trick as dtinth/superwhite

Requires: brew install libultrahdr ffmpeg   +   pip install pillow numpy
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".gif"}


def _need(binary):
    if not shutil.which(binary):
        sys.exit(f"missing dependency: {binary} (try: brew install {binary})")


# ---------------------------------------------------------------- images ----

def _box_blur(a, r, np, passes=3):
    """Separable box blur, repeated -> close enough to a gaussian, no scipy needed."""
    for _ in range(passes):
        for axis in (0, 1):
            pad = [(0, 0)] * a.ndim
            pad[axis] = (r, r)
            p = np.pad(a, pad, mode="edge")
            c = np.cumsum(p, axis=axis)
            lo = [slice(None)] * a.ndim
            hi = [slice(None)] * a.ndim
            lo[axis] = slice(0, a.shape[axis])
            hi[axis] = slice(2 * r, 2 * r + a.shape[axis])
            a = (c[tuple(hi)] - c[tuple(lo)]) / (2.0 * r)
    return a



def hdrify_image(src, dst, boost=16.0, knee=0.0, warmth=0.0, vivid=1.0,
                 glow=0.0, quality=95, target_nits=1000.0):
    """Build an Ultra HDR JPEG: untouched SDR base + a gain map that drives the picture
    above SDR white on HDR displays.

    boost  flat multiplier in linear light. On its own (everything else default) the
           picture is pixel-for-pixel identical — same hue, saturation, contrast —
           just rendered `boost`x above SDR white.

    Everything below grades the HDR layer ONLY. The SDR base stays original, so on a
    non-HDR screen none of it shows up; it's a look that exists only in the highlights
    of an HDR render.

    knee   0 = lift the whole image. >0 holds shadows/mids at SDR and spends the boost
           on bright areas only, so highlights bloom out of a normal-looking picture.
    warmth -1..+1. Splits the gain per channel (this is what made your face glow orange:
           red boosted harder than blue). + = warm/golden, - = cool/blue.
    vivid  saturation of the HDR intent. 1 = untouched, >1 pushes colour harder as it
           brightens, <1 lets highlights bleach toward white.
    glow   0..1. Adds a soft bloom of the bright areas into the HDR layer, so light
           spills off edges the way real overexposure does."""
    import numpy as np
    from PIL import Image

    _need("ultrahdr_app")
    im = Image.open(src).convert("RGB")
    w, h = im.size
    # libultrahdr wants even dimensions
    if w % 2 or h % 2:
        w, h = w - (w % 2), h - (h % 2)
        im = im.crop((0, 0, w, h))

    s = np.asarray(im).astype(np.float32) / 255.0
    lin = np.where(s <= 0.04045, s / 12.92, ((s + 0.055) / 1.055) ** 2.4)

    LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)
    lum = (lin @ LUMA)[..., None]

    # --- how much of the boost each pixel receives ---------------------------
    if knee <= 0:
        t = np.float32(1.0)                       # everything, evenly
    else:
        t = np.clip((lum - knee) / max(1e-6, 1.0 - knee), 0, 1)
    gain = 1.0 + (boost - 1.0) * t

    # --- split that gain across channels: the "orange face" knob -------------
    if warmth:
        w_ = float(np.clip(warmth, -1, 1))
        tint = np.array([1.0 + 0.45 * w_, 1.0, 1.0 - 0.45 * w_], np.float32)
        gain = gain * tint

    hdr = lin * gain

    # --- saturation of the HDR intent ---------------------------------------
    if vivid != 1.0:
        g = (hdr @ LUMA)[..., None]
        hdr = np.clip(g + (hdr - g) * float(vivid), 0, None)

    # --- optional bloom: bright areas spill light into their surroundings ----
    if glow > 0:
        bright = np.clip(hdr - 1.0, 0, None)      # only what exceeds SDR white
        r = max(1, int(min(w, h) / 90))
        blurred = _box_blur(bright, r, np)        # 3 box passes ~= gaussian
        hdr = hdr + blurred * float(glow) * 1.4

    # Bound the gain map's range explicitly. Letting libultrahdr derive the
    # minimum from the data lands it near zero (-14 stops on a dark photo), which
    # stretches the map's 256 levels across a huge range and leaves almost no
    # precision where the boost actually lives — the image stops glowing.
    ratio = hdr / np.maximum(lin, 1e-6)
    peak = float(np.clip(np.max(ratio), boost, 64.0))
    graded = knee > 0 or warmth != 0 or vivid != 1.0 or glow > 0
    # ungraded: a flat map, every pixel at `boost`. graded: never darken below 1x.
    floor = boost if not graded else 1.0

    with tempfile.TemporaryDirectory() as td:
        raw = os.path.join(td, "hdr.rgbaf16")
        sdr = os.path.join(td, "sdr.jpg")
        np.concatenate([hdr, np.ones((h, w, 1), np.float32)], 2).astype(np.float16).tofile(raw)
        if os.path.splitext(src)[1].lower() in (".jpg", ".jpeg") and (w, h) == Image.open(src).size:
            shutil.copyfile(src, sdr)  # keep the original bytes as the SDR base
        else:
            im.save(sdr, quality=quality, subsampling=0)
        subprocess.run(
            ["ultrahdr_app", "-m", "0", "-p", raw, "-a", "4", "-t", "0",
             "-w", str(w), "-h", str(h), "-i", sdr,
             "-C", "1", "-c", "0",
             "-k", f"{floor:.4f}", "-K", f"{peak:.4f}",
             # -L is the target display peak; libultrahdr turns it into
             # hdrCapacityMax = L / 203, and a decoder applies the gain only in
             # proportion to how close the real display gets to that capacity.
             # -L 10000 asks for 49x of headroom, so a laptop with ~4x applied a
             # 16x map as 1.6x — no visible glow. 1000 nits (capacity ~4.9) is
             # what the first build used, and it is the value that visibly
             # glowed: a 4x display then applies ~90% of the boost.
             "-L", f"{target_nits:.0f}",
             "-q", str(quality), "-z", dst],
            check=True, capture_output=True)
    return dst


# ---------------------------------------------------------------- videos ----

def _srgb_to_linear_expr():
    """ffmpeg lutrgb expression: sRGB code value -> linear light, rescaled to full range."""
    v = "(val/maxval)"
    return (f"clip(if(lte({v},0.04045),{v}/12.92,"
            f"pow(({v}+0.055)/1.055,2.4))*maxval,0,maxval)")


REFERENCE_WHITE = 203.0   # nits; the SDR diffuse-white anchor used by BT.2408


def _linear_to_pq_expr(nits, knee=0.5):
    """ffmpeg lutrgb expression: linear light -> PQ code value.

    Scaling the whole picture so SDR white lands on `nits` makes every midtone
    several times too bright: mid-grey ends up around 334 nits instead of ~44.
    Chrome hides this by tone-mapping, but a faithful player (QuickTime) shows
    it blown out and washed. So anchor diffuse white at the BT.2408 reference of
    203 nits and spend the extra range only above `knee`, which is what actually
    reads as "normal picture, glowing highlights"."""
    L = "(val/maxval)"
    t = f"clip(({L}-{knee:.6f})/{max(1e-6, 1.0 - knee):.6f},0,1)"
    scale = f"({REFERENCE_WHITE:.1f}+{max(0.0, nits - REFERENCE_WHITE):.4f}*{t})"
    y = f"{L}*{scale}/10000"
    n = f"pow({y},0.1593017578125)"
    return f"clip(pow((0.8359375+18.8515625*{n})/(1+18.6875*{n}),78.84375)*maxval,0,maxval)"


# sRGB(D65) -> BT.2020(D65) primaries, applied in LINEAR light.
# PQ is essentially always paired with BT.2020: tagging PQ content as bt709
# produces a file Chrome will render but QuickTime washes out. So convert the
# gamut for real rather than mislabelling it.
_SRGB_TO_BT2020 = ("colorchannelmixer="
                   "rr=0.6274:rg=0.3293:rb=0.0433:"
                   "gr=0.0691:gg=0.9195:gb=0.0114:"
                   "br=0.0164:bg=0.0880:bb=0.8956")


def hdrify_video(src, dst, nits=1600, knee=0.5, crf=18):
    _need("ffmpeg")
    lin = _srgb_to_linear_expr()
    pq = _linear_to_pq_expr(nits, knee)
    vf = ("format=rgb48,"
          f"lutrgb=r='{lin}':g='{lin}':b='{lin}',"      # to linear light
          f"{_SRGB_TO_BT2020},"                          # sRGB -> BT.2020 gamut
          f"lutrgb=r='{pq}':g='{pq}':b='{pq}',"          # linear -> PQ
          "scale=out_color_matrix=bt2020nc:out_range=tv,format=yuv420p10le")
    subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-vf", vf,
         "-c:v", "libx265", "-crf", str(crf), "-preset", "medium",
         "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1",
         "-color_primaries", "bt2020", "-color_trc", "smpte2084",
         "-colorspace", "bt2020nc", "-color_range", "tv",
         "-x265-params",
         "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:range=limited",
         "-c:a", "copy", "-movflags", "+faststart", dst],
        check=True, capture_output=True)
    return dst


# ------------------------------------------------------------------ entry ----

def hdrify(src, dst=None, boost=16.0, knee=0.0, nits=1600, warmth=0.0, vivid=1.0, glow=0.0):
    ext = os.path.splitext(src)[1].lower()
    if ext in IMAGE_EXT:
        dst = dst or os.path.splitext(src)[0] + "_hdr.jpg"
        return hdrify_image(src, dst, boost=boost, knee=knee, warmth=warmth, vivid=vivid, glow=glow)
    if ext in VIDEO_EXT:
        dst = dst or os.path.splitext(src)[0] + "_hdr.mp4"
        return hdrify_video(src, dst, nits=nits, knee=knee if knee > 0 else 0.5)
    sys.exit(f"unsupported file type: {ext}")


def main():
    p = argparse.ArgumentParser(description="make an image or video glow on HDR displays")
    p.add_argument("input")
    p.add_argument("-o", "--output")
    p.add_argument("--boost", type=float, default=16.0, help="image: x above SDR white (default 16 = max)")
    p.add_argument("--knee", type=float, default=0.0, help="image: 0=lift everything, 0.5=highlights only")
    p.add_argument("--nits", type=float, default=1600, help="video: nits for SDR white (default 1600 = MacBook XDR peak)")
    p.add_argument("--warmth", type=float, default=0.0, help="image: -1 cool .. +1 warm/golden")
    p.add_argument("--vivid", type=float, default=1.0, help="image: HDR saturation, 1 = untouched")
    p.add_argument("--glow", type=float, default=0.0, help="image: 0-1 bloom off the highlights")
    a = p.parse_args()
    print(hdrify(a.input, a.output, boost=a.boost, knee=a.knee, nits=a.nits,
                 warmth=a.warmth, vivid=a.vivid, glow=a.glow))


if __name__ == "__main__":
    main()
