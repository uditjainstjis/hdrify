/**
 * Free conversion logging into a Google Sheet — no third party, no cookies,
 * no analytics vendor, and the data is yours.
 *
 * SETUP
 *  1. Create a Google Sheet. First row, columns A-F:
 *       timestamp | step | detail | referrer | userAgent | clientTime
 *  2. Extensions -> Apps Script. Paste this file over the default Code.gs.
 *  3. Put the Sheet's ID (the long string in its URL) into SHEET_ID below.
 *  4. Deploy -> New deployment -> type "Web app".
 *       Execute as:      Me
 *       Who has access:  Anyone            <- required; the browser posts anonymously
 *  5. Copy the /exec URL it gives you into SHEET_URL in analytics.js, then redeploy.
 *
 * The page posts with mode:'no-cors', so it never reads a response and can't be
 * blocked by CORS. Nothing about the user's image is ever sent — only which
 * funnel step they reached.
 */

const SHEET_ID = 'PASTE_YOUR_SHEET_ID_HERE'

function doPost(e) {
  try {
    const d = JSON.parse(e.postData.contents)
    SpreadsheetApp.openById(SHEET_ID).getSheets()[0].appendRow([
      new Date(),
      d.step || '',
      d.detail || '',
      d.ref || '',
      d.ua || '',
      d.at || '',
    ])
  } catch (err) {
    // swallow: a logging failure must never surface to the visitor
  }
  return ContentService.createTextOutput('ok')
}

// Lets you confirm the deployment is live by opening the /exec URL in a browser.
function doGet() {
  return ContentService.createTextOutput('hdrify logger up')
}
