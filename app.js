import { encodeJPEGMetadata } from './vendor/libultrahdr.js'

// ---------------------------------------------------------------- helpers --

const $ = s => document.querySelector(s)
const clamp = (v, a, b) => Math.min(b, Math.max(a, v))

/** sRGB code value (0..1) -> linear light */
function srgbToLinear(v) {
  return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)
}
const S2L = new Float32Array(256)
for (let i = 0; i < 256; i++) S2L[i] = srgbToLinear(i / 255)

const LUMA = [0.2126, 0.7152, 0.0722]

/** Separable box blur, repeated -> close enough to a gaussian. In place-ish. */
function boxBlur(src, w, h, r, passes = 3) {
  let a = src, b = new Float32Array(a.length)
  for (let p = 0; p < passes; p++) {
    // horizontal
    for (let y = 0; y < h; y++) {
      const row = y * w
      let acc = 0
      for (let x = -r; x <= r; x++) acc += a[row + clamp(x, 0, w - 1)]
      for (let x = 0; x < w; x++) {
        b[row + x] = acc / (2 * r + 1)
        acc += a[row + clamp(x + r + 1, 0, w - 1)] - a[row + clamp(x - r, 0, w - 1)]
      }
    }
    // vertical
    for (let x = 0; x < w; x++) {
      let acc = 0
      for (let y = -r; y <= r; y++) acc += b[clamp(y, 0, h - 1) * w + x]
      for (let y = 0; y < h; y++) {
        a[y * w + x] = acc / (2 * r + 1)
        acc += b[clamp(y + r + 1, 0, h - 1) * w + x] - b[clamp(y - r, 0, h - 1) * w + x]
      }
    }
  }
  return a
}

function canvasToJpeg(canvas, quality) {
  return new Promise(res => canvas.toBlob(b => b.arrayBuffer().then(
    ab => res({ data: new Uint8Array(ab), mimeType: 'image/jpeg', width: canvas.width, height: canvas.height })
  ), 'image/jpeg', quality))
}

// ------------------------------------------------------------------ core ----

/**
 * Build an Ultra HDR JPEG entirely in the browser.
 *
 * The SDR base is the picture exactly as it is today. The gain map carries a
 * per-pixel, per-channel multiplier that HDR displays apply on top of it:
 *
 *     hdr_linear = sdr_linear * 2^lerp(gainMapMin, gainMapMax, pixel^gamma)
 *
 * so a flat multiplier with no grading is mathematically a no-op on hue,
 * saturation and contrast — the picture just renders brighter.
 */
export async function buildUltraHDR(imageBitmap, opts) {
  const { boost = 16, knee = 0, warmth = 0, vivid = 1, glow = 0, quality = 0.92 } = opts
  const w = imageBitmap.width, h = imageBitmap.height
  const n = w * h

  const cv = document.createElement('canvas')
  cv.width = w; cv.height = h
  const ctx = cv.getContext('2d', { willReadFrequently: true })
  ctx.drawImage(imageBitmap, 0, 0)
  const px = ctx.getImageData(0, 0, w, h).data

  // linear light of the SDR base
  const lin = new Float32Array(n * 3)
  for (let i = 0, j = 0; i < n; i++, j += 3) {
    const o = i * 4
    lin[j] = S2L[px[o]]; lin[j + 1] = S2L[px[o + 1]]; lin[j + 2] = S2L[px[o + 2]]
  }

  // the HDR intent
  const hdr = new Float32Array(n * 3)
  const tint = warmth
    ? [1 + 0.45 * clamp(warmth, -1, 1), 1, 1 - 0.45 * clamp(warmth, -1, 1)]
    : [1, 1, 1]

  for (let i = 0, j = 0; i < n; i++, j += 3) {
    const r = lin[j], g = lin[j + 1], b = lin[j + 2]
    const lum = r * LUMA[0] + g * LUMA[1] + b * LUMA[2]
    // how much of the boost this pixel receives
    const t = knee <= 0 ? 1 : clamp((lum - knee) / Math.max(1e-6, 1 - knee), 0, 1)
    const gain = 1 + (boost - 1) * t
    hdr[j] = r * gain * tint[0]
    hdr[j + 1] = g * gain * tint[1]
    hdr[j + 2] = b * gain * tint[2]
  }

  if (vivid !== 1) {
    for (let j = 0; j < hdr.length; j += 3) {
      const gy = hdr[j] * LUMA[0] + hdr[j + 1] * LUMA[1] + hdr[j + 2] * LUMA[2]
      hdr[j] = Math.max(0, gy + (hdr[j] - gy) * vivid)
      hdr[j + 1] = Math.max(0, gy + (hdr[j + 1] - gy) * vivid)
      hdr[j + 2] = Math.max(0, gy + (hdr[j + 2] - gy) * vivid)
    }
  }

  if (glow > 0) {
    const r = Math.max(1, Math.round(Math.min(w, h) / 90))
    for (let c = 0; c < 3; c++) {
      const plane = new Float32Array(n)
      for (let i = 0; i < n; i++) plane[i] = Math.max(0, hdr[i * 3 + c] - 1)
      const blurred = boxBlur(plane, w, h, r)
      for (let i = 0; i < n; i++) hdr[i * 3 + c] += blurred[i] * glow * 1.4
    }
  }

  // --- turn the hdr/sdr ratio into a gain map ------------------------------
  // log2 of the per-pixel, per-channel multiplier.
  //
  // NOTE: the XMP writer emits GainMapMin/Max as a single scalar, not per
  // channel, so all three channels MUST share one range — otherwise a decoder
  // denormalises them against a range they weren't encoded with and the colour
  // comes out wrong. Per-channel grading still survives: the gain map itself is
  // RGB, so each channel carries its own value inside that shared range.
  const log2 = new Float32Array(n * 3)
  let lo = Infinity, hi = -Infinity
  for (let i = 0, j = 0; i < n; i++, j += 3) {
    for (let c = 0; c < 3; c++) {
      // near-black pixels have a numerically meaningless ratio, and a gain
      // below 1 would darken the HDR render — clamp both away so the range
      // stays tight and precision isn't spent on noise
      const s = Math.max(lin[j + c], 1e-4)
      const v = Math.log2(clamp(hdr[j + c] / s, 1, 64))
      log2[j + c] = v
      if (v < lo) lo = v
      if (v > hi) hi = v
    }
  }
  // a completely flat gain collapses the range — give it a sliver of width so
  // the normalisation below stays finite
  if (hi - lo < 1e-4) hi = lo + 1e-4

  const gmCanvas = document.createElement('canvas')
  gmCanvas.width = w; gmCanvas.height = h
  const gmCtx = gmCanvas.getContext('2d')
  const gmData = gmCtx.createImageData(w, h)
  for (let i = 0, j = 0; i < n; i++, j += 3) {
    const o = i * 4
    for (let c = 0; c < 3; c++) {
      gmData.data[o + c] = clamp(Math.round(((log2[j + c] - lo) / (hi - lo)) * 255), 0, 255)
    }
    gmData.data[o + 3] = 255
  }
  gmCtx.putImageData(gmData, 0, 0)

  const [sdr, gainMap] = await Promise.all([
    canvasToJpeg(cv, quality),
    canvasToJpeg(gmCanvas, quality),
  ])

  const jpeg = encodeJPEGMetadata({
    sdr,
    gainMap,
    gamma: [1, 1, 1],
    offsetSdr: [0, 0, 0],
    offsetHdr: [0, 0, 0],
    gainMapMin: [lo, lo, lo],
    gainMapMax: [hi, hi, hi],
    hdrCapacityMin: 0,
    hdrCapacityMax: Math.max(0, hi),
  })
  return new Blob([jpeg], { type: 'image/jpeg' })
}

// exposed so the encoder can be driven from the console or a test harness
window.hdrify = { buildUltraHDR }

// -------------------------------------------------------------------- ui ----

const PRESETS = {
  Untouched: { boost: 16, knee: 0, warmth: 0, vivid: 1, glow: 0 },
  Golden: { boost: 12, knee: 0.35, warmth: 0.6, vivid: 1.25, glow: 0.5 },
  Neon: { boost: 20, knee: 0.25, warmth: -0.35, vivid: 2, glow: 0.65 },
  Blowout: { boost: 32, knee: 0.5, warmth: 0.15, vivid: 0.6, glow: 1 },
}

const ctl = {
  boost: $('#boost'), knee: $('#knee'), warmth: $('#warmth'),
  vivid: $('#vivid'), glow: $('#glow'),
}
let bitmap = null, fileName = 'image', outBlob = null, outURL = null, seq = 0, timer = null

function fmt() {
  $('#o-boost').textContent = (+ctl.boost.value).toFixed(1) + '×'
  $('#o-knee').textContent = ctl.knee.value === '0' ? 'off' : (+ctl.knee.value).toFixed(2)
  $('#o-warmth').textContent = (+ctl.warmth.value).toFixed(2)
  $('#o-vivid').textContent = (+ctl.vivid.value).toFixed(2)
  $('#o-glow').textContent = ctl.glow.value === '0' ? 'off' : (+ctl.glow.value).toFixed(2)
}

function opts() {
  return {
    boost: +ctl.boost.value, knee: +ctl.knee.value, warmth: +ctl.warmth.value,
    vivid: +ctl.vivid.value, glow: +ctl.glow.value,
  }
}

function schedule(delay = 140) {
  if (!bitmap) return
  clearTimeout(timer)
  timer = setTimeout(render, delay)
}

async function render() {
  const my = ++seq
  const frame = $('#fb')
  frame.classList.add('busy')
  try {
    const blob = await buildUltraHDR(bitmap, opts())
    if (my !== seq) return
    outBlob = blob
    if (outURL) URL.revokeObjectURL(outURL)
    outURL = URL.createObjectURL(blob)
    frame.innerHTML = `<img src="${outURL}">`
    $('#dl').disabled = false
    $('#status').textContent = `live · ${(+ctl.boost.value).toFixed(1)}× above SDR white · ${(blob.size / 1024 | 0)} KB`
    $('#error').textContent = ''
  } catch (e) {
    $('#error').textContent = e.message
  } finally {
    if (my === seq) frame.classList.remove('busy')
  }
}

async function load(file) {
  if (!file) return
  if (!file.type.startsWith('image/')) {
    $('#error').textContent = 'Images only here — video needs the local CLI (see the README).'
    return
  }
  $('#error').textContent = ''
  fileName = file.name.replace(/\.[^.]+$/, '')
  bitmap = await createImageBitmap(file)
  $('#drop').classList.add('hide')
  $('#compare').style.display = 'grid'
  $('#fa').innerHTML = `<img src="${URL.createObjectURL(file)}">`
  $('#status').textContent = `${file.name} · ${bitmap.width}×${bitmap.height}`
  schedule(0)
}

// wiring
Object.values(ctl).forEach(s => s.addEventListener('input', () => { fmt(); schedule() }))
fmt()

const bar = $('#presets')
for (const name in PRESETS) {
  const b = document.createElement('button')
  b.className = 'chip'
  b.textContent = name
  b.onclick = () => {
    const p = PRESETS[name]
    for (const k in p) ctl[k].value = p[k]
    fmt(); schedule(0)
  }
  bar.appendChild(b)
}

$('#drop').onclick = () => $('#file').click()
$('#file').onchange = e => load(e.target.files[0])
$('#drop').ondragover = e => { e.preventDefault(); $('#drop').classList.add('on') }
$('#drop').ondragleave = () => $('#drop').classList.remove('on')
$('#drop').ondrop = e => { e.preventDefault(); $('#drop').classList.remove('on'); load(e.dataTransfer.files[0]) }
document.body.addEventListener('dragover', e => e.preventDefault())
document.body.addEventListener('drop', e => { e.preventDefault(); if (!bitmap) load(e.dataTransfer.files[0]) })

$('#dl').onclick = () => {
  if (!outBlob) return
  const a = document.createElement('a')
  a.href = URL.createObjectURL(outBlob)
  a.download = `${fileName}_hdr.jpg`
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 10000)
}

$('#reset').onclick = () => {
  bitmap = null; outBlob = null
  $('#compare').style.display = 'none'
  $('#drop').classList.remove('hide')
  $('#fa').innerHTML = $('#fb').innerHTML = ''
  $('#dl').disabled = true
  $('#file').value = ''
  $('#status').textContent = 'drop an image to begin'
}
