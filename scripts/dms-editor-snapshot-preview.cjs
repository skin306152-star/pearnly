#!/usr/bin/env node
// LOCAL ONLY: real task build + synthetic API responses. No proxy, Site or outbound IO.
// Build first with the repository's pinned Node and `npm --prefix frontend run build`.
// Open the printed URL with draft=fresh|stale|pending|incomplete|save-failed.
// Ctrl-C stops the owned server; a 25-minute deadline prevents abandoned listeners.
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '..');
const assets = path.join(root, 'apps/pearnly_core/pearnly_core/public/pearnly-app');
const assetPrefix = '/assets/pearnly_core/pearnly-app/';
const apiPrefix = '/api/method/pearnly_dms.editor.browser_api.';
const index = fs.readFileSync(path.join(assets, 'index.html'));
function csvRows(raw) {
  const rows = []; let row = [], field = '', quoted = false;
  for (let i = 0; i < raw.length; i++) {
    const ch = raw[i];
    if (ch === '"') {
      if (quoted && raw[i + 1] === '"') { field += '"'; i++; }
      else quoted = !quoted;
    } else if (ch === ',' && !quoted) { row.push(field); field = ''; }
    else if (ch === '\n' && !quoted) { row.push(field.replace(/\r$/, '')); rows.push(row); row = []; field = ''; }
    else field += ch;
  }
  if (quoted) throw new Error('Unclosed translation CSV quote');
  if (field || row.length) { row.push(field); rows.push(row); }
  return rows;
}
const texts = {};
for (const language of ['th', 'en', 'zh', 'ja']) {
  const raw = fs.readFileSync(path.join(root, `apps/pearnly_dms/pearnly_dms/translations/${language}.csv`), 'utf8');
  for (const [key, value] of csvRows(raw)) {
    if (key.startsWith('dms.')) (texts[key] ||= {})[language] = value.replaceAll('\\n', '\n');
  }
}
if (!texts['dms.edit.customerTitle']?.zh || !texts['dms.snapshot.as_of']?.th) throw new Error('Incomplete local language fixture');
const events = [];
const option = (id, label) => [{id, label}];
const geo = {provinces: option('P', 'TEST Province'), districts: option('D', 'TEST District'),
  subdistricts: option('S', 'TEST Subdistrict'), zipcodes: option('Z', '00000')};
const customer = {name: 'SYNTHETIC UI ONLY', people_id: '0000000000000', prefix_id: 'T',
  birthday_be: '01/01/2540', phone: '0000000000', house_no: 'TEST ONLY',
  province_id: 'P', district_id: 'D', subdistrict_id: 'S', zipcode_id: 'Z'};

function draft(scenario) {
  return {form: {customer, answers: {}, payments: [], files: {}, advisor: {}},
    masters: {prefixes: option('T', 'TEST')}, manual_banks: [], geo,
    snapshot: {as_of: Date.parse('2026-09-24T01:23:00Z') / 1000,
      geo_as_of: Date.parse('2026-09-23T03:45:00Z') / 1000,
      stale: scenario === 'stale', geo_stale: scenario !== 'stale', generation: 'synthetic-ui-only'}};
}

const server = http.createServer(async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; font-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'");
  res.setHeader('X-Content-Type-Options', 'nosniff');
  if (req.headers.host !== `127.0.0.1:${server.address().port}`) {
    res.writeHead(403); res.end('Loopback Host required'); return;
  }
  const url = new URL(req.url, 'http://127.0.0.1');
  const json = (value, status = 200) => {
    res.writeHead(status, {'Content-Type': 'application/json'}); res.end(JSON.stringify(value));
  };
  const error = suffix => json({message: 'dms.err.ERR_DMS_SNAPSHOT_' + suffix}, 503);
  if (url.pathname === '/__smoke_events' && req.method === 'GET') return json(events);
  if (url.pathname.startsWith('/api/')) {
    let body = '';
    for await (const chunk of req) {
      body += chunk;
      if (body.length > 16384) { res.writeHead(413); res.end(); return; }
    }
    let params;
    try { params = req.method === 'POST' ? JSON.parse(body || '{}') : Object.fromEntries(url.searchParams); }
    catch { return json({message: 'Bad synthetic request'}, 400); }
    const method = url.pathname.startsWith(apiPrefix) ? url.pathname.slice(apiPrefix.length) : '';
    events.push({method: method || url.pathname, scenario: String(params.nonce || params.draft || ''), verb: req.method});
    if (url.pathname === '/api/method/pearnly_core.browser_text.browser_text' && req.method === 'GET') return json({message: texts});
    if (method === 'browser_config' && req.method === 'GET') return json({message: {liff_id: '', available: true, configured: false, csrf_token: 'synthetic-local-only'}});
    if (method === 'browser_auth' && req.method === 'POST') return json({message: {ticket: 'synthetic-local-only'}});
    if (method === 'browser_draft' && req.method === 'GET') {
      if (params.nonce === 'pending') return error('PENDING');
      if (params.nonce === 'incomplete') return error('INCOMPLETE');
      if (!['fresh', 'stale', 'save-failed'].includes(params.nonce)) return json({message: 'Unknown synthetic scenario'}, 400);
      return json({message: draft(params.nonce)});
    }
    // Never simulate a successful LINE delivery or external customer write.
    if (method === 'browser_save' && req.method === 'POST' && params.nonce === 'save-failed') return error('INCOMPLETE');
    return json({message: 'Unsupported local-only API'}, 404);
  }
  if (req.method !== 'GET') { res.writeHead(405); res.end(); return; }
  if (url.pathname === '/') {
    res.writeHead(200, {'Content-Type': 'text/html; charset=utf-8'}); res.end(index); return;
  }
  if (url.pathname.startsWith(assetPrefix)) {
    const file = path.resolve(assets, url.pathname.slice(assetPrefix.length));
    if (file.startsWith(assets + path.sep) && fs.existsSync(file) && fs.statSync(file).isFile()) {
      const mime = {'.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.html': 'text/html'}[path.extname(file)];
      if (mime) { res.writeHead(200, {'Content-Type': mime}); res.end(fs.readFileSync(file)); return; }
    }
  }
  res.writeHead(404); res.end('Local fixture route not found');
});
server.listen(0, '127.0.0.1', () => {
  console.log(JSON.stringify({pid: process.pid, url: `http://127.0.0.1:${server.address().port}/#/dms/editor?draft=fresh&editor=customer`,
    index_sha256: crypto.createHash('sha256').update(index).digest('hex'), outbound: 'none', expiry_minutes: 25}));
});
const stop = () => server.close(() => process.exit(0));
process.once('SIGINT', stop); process.once('SIGTERM', stop);
setTimeout(stop, 25 * 60 * 1000).unref();
