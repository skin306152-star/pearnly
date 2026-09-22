// Camera lifecycle without hardware: no duplicate delivery, cancellation, exact QR,
// and late permission resolution. Decoder accuracy remains a real-device check.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/scan.js', 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function fixture({ acquire, decode } = {}) {
  const callbacks = new Map(), result = [], errors = [], constraints = [];
  let id = 0, stops = 0;
  const track = { stop: () => stops++, getCapabilities: () => ({ torch: true }),
    applyConstraints: async value => constraints.push(value) };
  const stream = { getTracks: () => [track], getVideoTracks: () => [track] };
  const video = { readyState: 2, play: async () => {} };
  const $ = () => ({ find: () => ({ get: () => video, addClass() {}, removeClass() {} }) });
  const p = {}, formats = ['ean_13','ean_8','upc_a','upc_e','code_128','code_39','itf','codabar','qr_code'];
  class Detector {
    static async getSupportedFormats() { return formats; }
    async detect() { return decode ? decode() : [{ rawValue: '  盘点 QR 001  ' }]; }
  }
  vm.runInNewContext(source, { window: { BarcodeDetector: Detector },
    navigator: { mediaDevices: { getUserMedia: () => acquire ? acquire(stream) : Promise.resolve(stream) } },
    frappe: { provide() {}, pearnly: p },
    $, setTimeout: callback => { callbacks.set(++id, callback); return id; },
    clearTimeout: key => callbacks.delete(key),
  });
  const reader = p.scan_reader({ host: {}, on_result: code => result.push(code), on_error: error => errors.push(error) });
  return { reader, result, errors, constraints, video, stops: () => stops,
    async tick() { const next = callbacks.entries().next().value; assert.ok(next); callbacks.delete(next[0]); await next[1](); await flush(); } };
}

(async () => {
  const f = fixture();
  await f.reader.start(); await flush();
  assert.equal(f.result.length, 0);
  await f.tick(); assert.deepEqual(f.result, ['盘点 QR 001']);
  await f.tick(); assert.equal(f.result.length, 1);
  assert.equal(f.reader.has_torch(), true);
  await f.reader.torch(true); assert.equal(f.constraints[0].advanced[0].torch, true);
  f.reader.resume(); await f.tick(); await f.tick(); assert.equal(f.result.length, 2);
  f.reader.stop(); assert.equal(f.stops(), 1); assert.equal(f.video.srcObject, null);
  let grant;
  const late = fixture({ acquire: stream => new Promise(resolve => { grant = () => resolve(stream); }) });
  const pending = late.reader.start(); await flush(); late.reader.stop(); grant(); await pending;
  assert.equal(late.stops(), 1); assert.equal(late.video.srcObject, null);
  const denied = fixture({ acquire: () => Promise.reject(new Error('permission denied')) });
  await denied.reader.start(); assert.equal(denied.errors.length, 1);
  assert.equal(denied.reader.has_torch(), false);
  console.log('PASS two-frame decode, exact QR, pause/resume, torch, stream cleanup and late permission cancellation');
})().catch(error => { console.error(error); process.exitCode = 1; });
