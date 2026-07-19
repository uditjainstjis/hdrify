/**
 * Analytics — traffic + conversion funnel.
 *
 * Three backends, all free, all optional. Fill in the IDs below to switch one on;
 * with none configured this file is completely inert and costs nothing.
 *
 *  1. VERCEL WEB ANALYTICS — zero config, first-party, no cookies, no consent
 *     banner needed. Free on Hobby: 50k events/month. Gives visitors, pageviews,
 *     referrers, countries, devices.
 *     ENABLE: Vercel dashboard -> hdrify-web -> Analytics -> Enable.
 *     LIMIT: named custom events are Pro-only, so the funnel below is reported as
 *     virtual pageviews instead (each step becomes its own /e/... path).
 *
 *  2. GOOGLE ANALYTICS 4 — free with genuinely unlimited custom events, which is
 *     what Vercel's free tier won't do. Best choice for real conversion tracking.
 *     ENABLE: analytics.google.com -> create property -> copy the "G-XXXXXXXXXX"
 *     Measurement ID into GA4_ID below.
 *     COST: loads a third-party script from googletagmanager.com, sets cookies,
 *     and puts your visitors' data in Google's hands. That's a real tradeoff for
 *     a tool whose whole pitch is "nothing leaves your browser" — worth deciding
 *     deliberately rather than by default.
 *
 *  3. GOOGLE SHEET via Apps Script — your own data, no third party, no cookies,
 *     no limits worth worrying about. See sheets-logger.gs in this repo for the
 *     ~15 lines to paste into script.google.com; deploy it as a web app ("execute
 *     as me", "anyone can access") and put the resulting /exec URL in SHEET_URL.
 *     Fires with keepalive + no-cors so it never blocks or breaks the page.
 *
 * Nothing here ever sees the user's image. Only which step they reached.
 */

const GA4_ID = ''      // e.g. 'G-XXXXXXXXXX'
const SHEET_URL = ''   // e.g. 'https://script.google.com/macros/s/AKfy.../exec'

const isLocal = ['localhost', '127.0.0.1', ''].includes(location.hostname)

// ---------------------------------------------------------------- backends --

function loadVercel() {
  const s = document.createElement('script')
  s.defer = true
  s.src = '/_vercel/insights/script.js'
  s.onerror = () => { /* not enabled on the project yet — stay silent */ }
  document.head.appendChild(s)
}

function loadGA4() {
  if (!GA4_ID) return
  const s = document.createElement('script')
  s.async = true
  s.src = `https://www.googletagmanager.com/gtag/js?id=${GA4_ID}`
  document.head.appendChild(s)
  window.dataLayer = window.dataLayer || []
  window.gtag = function () { window.dataLayer.push(arguments) }
  window.gtag('js', new Date())
  window.gtag('config', GA4_ID)
}

/** Vercel reports a view on history changes; restore the URL immediately so the
 *  visitor's address bar and back button are untouched. */
function vercelVirtualPageview(step) {
  const real = location.pathname + location.search
  history.replaceState(history.state, '', `/e/${step}`)
  setTimeout(() => history.replaceState(history.state, '', real), 0)
}

function toSheet(step, detail) {
  if (!SHEET_URL) return
  const body = JSON.stringify({
    step,
    detail: detail || '',
    ref: document.referrer || 'direct',
    ua: navigator.userAgent.slice(0, 120),
    at: new Date().toISOString(),
  })
  // no-cors + keepalive: fire-and-forget, survives the page closing
  fetch(SHEET_URL, { method: 'POST', mode: 'no-cors', keepalive: true, body }).catch(() => {})
}

// ------------------------------------------------------------------- api ----

/**
 * Report a funnel step:
 *   image-loaded  picked an image      (top of funnel)
 *   rendered      an HDR file was made
 *   downloaded    kept it              (the conversion that matters)
 */
export function track(step, detail) {
  try {
    if (isLocal) { console.debug('[analytics]', step, detail || ''); return }
    vercelVirtualPageview(step)
    if (GA4_ID && window.gtag) window.gtag('event', step, detail ? { detail } : {})
    toSheet(step, detail)
  } catch {
    /* analytics must never break the tool */
  }
}

/** Report each step at most once per session — we want users, not slider fidgeting. */
const fired = new Set()
export function trackOnce(step, detail) {
  if (fired.has(step)) return
  fired.add(step)
  track(step, detail)
}

if (!isLocal) { loadVercel(); loadGA4() }
