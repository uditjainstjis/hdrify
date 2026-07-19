# Vendored

`libultrahdr.js` — the pure-JS Ultra HDR container writer from
[@monogrid/gainmap-js](https://github.com/MONOGRID/gainmap-js) v3.4.0
(`dist/libultrahdr.js`), MIT licensed. Copyright (c) MONOGRID.

It exports a single function, `encodeJPEGMetadata`, which glues an SDR JPEG and a
gain map JPEG into one Ultra HDR file (XMP + MPF). No runtime dependencies —
despite the name there is no WASM and no three.js involved.

Vendored rather than installed so the site stays a dependency-free static build.

**Known limitation:** its XMP writer emits `GainMapMin`/`GainMapMax` as a single
scalar rather than per channel. `app.js` therefore normalises all three channels
against one shared range — per-channel grading still works, since the gain map
itself is RGB.
