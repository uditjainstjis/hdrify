# hdrify

Make any photo render **far brighter than white** on HDR displays — MacBook XDR, iPhone,
recent iPads. Runs entirely in your browser. Nothing is uploaded.

**→ [Try it](https://hdrify.vercel.app)**

## What it actually does

An Ultra HDR JPEG is two images in one file: a normal SDR JPEG, plus a *gain map* — a
second JPEG holding a per-pixel, per-channel multiplier — glued together with XMP metadata
and an MPF index. HDR-capable displays apply the multiplier:

```
hdr_linear = sdr_linear * 2^lerp(gainMapMin, gainMapMax, gainMapPixel^gamma)
```

Everything else ignores the gain map and shows the plain SDR image. So the file is safe to
post anywhere: it either glows, or it looks exactly like your original.

With the boost slider alone and nothing else touched, the multiplier is **flat** — one
constant applied in linear light. That's mathematically a no-op on hue, saturation and
contrast. The picture is pixel-identical; it just renders many times above SDR white.

## Controls

| Control | What it does |
|---|---|
| **Brightness boost** | Flat gain in linear light. Colours untouched, everything brighter. |
| **Highlights only** | Holds shadows and midtones at SDR, spends the boost on bright areas only, so highlights bloom out of a normal-looking picture. |
| **Warmth** | Splits the gain per channel — red harder than blue, or the reverse. A golden or icy cast that exists *only* in the HDR render. |
| **Vividness** | Saturation of the HDR intent. Below 1, highlights bleach toward white. |
| **Bloom** | Light spilling off the bright areas, the way real overexposure blooms. |

Warmth, vividness and bloom grade the **HDR layer only**. The SDR base stays your original
file, so on a non-HDR screen none of it shows up.

## Seeing it work

The preview needs **Chrome or Safari on an HDR display**, with screen brightness **not at
maximum** — macOS only renders above SDR white when there's brightness headroom left. At
100% brightness a perfectly good HDR file looks identical to the SDR one.

## Where it survives

Most services re-encode uploads and strip the gain map in the process, which silently turns
the file back into its SDR base. Roughly, from most to least likely to survive: direct file
sharing (AirDrop, Drive, iMessage) → sites that serve originals → social platforms →
profile pictures, which are the most aggressively re-processed surface almost everywhere.
Test before you rely on it.

## Video

Video uses a completely different mechanism — no gain map, just a 10-bit HEVC stream tagged
with the PQ transfer function (`smpte2084`), the same trick as
[dtinth/superwhite](https://github.com/dtinth/superwhite). That needs a real encoder, so
it's CLI-only:

```sh
brew install libultrahdr ffmpeg
pip install pillow numpy

python3 hdrify.py photo.jpg --boost 16 --warmth 0.6 --glow 0.5
python3 hdrify.py clip.mp4 --nits 1600
```

Note that PQ is **absolute**: mapping SDR white to 10000 nits doesn't make a brighter video,
it clips everything past your panel's peak to flat white. ~1600 nits matches XDR. Unlike the
image path, video has no gain map to tone-map it back down, so there's no safe fallback.

Chrome frequently can't *play* HEVC 10-bit even when the file is valid — if the preview is
blank, open the download in QuickTime or Safari.

## Credits

Gain map container writing by [@monogrid/gainmap-js](https://github.com/MONOGRID/gainmap-js)
(vendored, MIT). Format is Google's
[Ultra HDR](https://developer.android.com/media/platform/hdr-image-format); the CLI calls
Google's reference codec [libultrahdr](https://github.com/google/libultrahdr).

MIT.
