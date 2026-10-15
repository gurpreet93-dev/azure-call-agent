// Google Apps Script for the "ClearView Leads (demo)" sheet: the web app the voice agent posts leads to.
// Setup: open the sheet -> Extensions -> Apps Script, paste this file, set TOKEN, then
// Deploy -> New deployment -> Web app, Execute as: Me, Who has access: Anyone -> copy the Web app URL.
//
// The URL will be public (it is in the app's code), so the script only accepts requests that carry TOKEN.
// Set TOKEN to the website's password (the APP_PASSWORD the server already has). Leave it '' only for a quick test.
const TOKEN = 'PASTE_THE_SITE_PASSWORD_HERE';

const HEADERS = ['Saved at', 'Conversation ID', 'Name', 'Mobile', 'Email', 'Address', 'City', 'District',
                 'Pincode', 'Requirement', 'Product', 'Language', 'Complete', 'Missing', 'Summary'];

function doPost(e) {
  let body;
  try { body = JSON.parse(e.postData.contents); } catch (err) { return reply({ok: false, error: 'not JSON'}); }
  if (TOKEN && body.token !== TOKEN) return reply({ok: false, error: 'bad token'});
  const row = body.row || {};
  if (!row['Name'] && !row['Mobile']) return reply({ok: false, error: 'no name or mobile'});

  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
    if (sheet.getLastRow() === 0) sheet.appendRow(HEADERS);
    const values = HEADERS.map(h => String(row[h] == null ? '' : row[h]).slice(0, 1000));
    // One row per call: saving the same call again (more details given later) updates its row.
    const id = row['Conversation ID'];
    const last = sheet.getLastRow();
    const ids = last > 1 ? sheet.getRange(2, 2, last - 1, 1).getValues().map(r => r[0]) : [];
    const at = id ? ids.indexOf(id) : -1;
    if (at >= 0) sheet.getRange(at + 2, 1, 1, values.length).setValues([values]);
    else sheet.appendRow(values);
    return reply({ok: true, updated: at >= 0});
  } finally {
    lock.releaseLock();
  }
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
