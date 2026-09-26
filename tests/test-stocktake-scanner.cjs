// Camera lifecycle without hardware: no duplicate delivery, cancellation, exact QR,
// and late permission resolution. Decoder accuracy remains a real-device check.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
// 读码器本体在共用层，ERP 的扫码动作在 ERP；站点上按这个顺序加载（2026-09-26 去重）。
const source = ['apps/pearnly_core/pearnly_core/public/desk/scan.js',
  'apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/scan.js'].map(path => fs.readFileSync(path, 'utf8')).join('\n');
const flush = () => new Promise(resolve => setImmediate(resolve));

function fixture({ acquire, decode, muted = 0 } = {}) {
  const callbacks = new Map(), result = [], details = [], errors = [], constraints = [];
  // 反馈那一层看得见的东西：震动次数与放过的音（音效/震动都归"静音"这一位管）。
  const vibrations = [], sounds = [];
  let id = 0, stops = 0;
  const track = { stop: () => stops++, getCapabilities: () => ({ torch: true }),
    applyConstraints: async value => constraints.push(value) };
  const stream = { getTracks: () => [track], getVideoTracks: () => [track] };
  const video = { readyState: 2, play: async () => {} };
  const $ = () => ({ find: () => ({ get: () => video, addClass() {}, removeClass() {} }) });
  const p = {}, formats = ['ean_13','ean_8','upc_a','upc_e','code_128','code_39','itf','codabar','qr_code'];
  class Detector {
    static async getSupportedFormats() { return formats; }
    async detect() { return decode ? decode() : [{ rawValue: '  盘点 QR 001  ', format: 'qr_code' }]; }
  }
  vm.runInNewContext(source, { window: { BarcodeDetector: Detector },
    navigator: { mediaDevices: { getUserMedia: () => acquire ? acquire(stream) : Promise.resolve(stream) },
      vibrate: ms => vibrations.push(ms) },
    frappe: { provide() {}, pearnly: p,
      utils: { play_sound: name => sounds.push(name) },
      boot: { user: { mute_sounds: muted } } },
    $, setTimeout: callback => { callbacks.set(++id, callback); return id; },
    clearTimeout: key => callbacks.delete(key),
  });
  // `p.sounds_muted` 是共享层（`pearnly_core/public/desk/product.js`）给的：这份测试只加载
  // scan.js，所以按同一份契约补一个 —— 判据就是官方那一位 `frappe.boot.user.mute_sounds`。
  p.sounds_muted = () => Boolean(muted);
  const reader = p.scan_reader({ host: {}, on_result: (code, metadata) => {result.push(code);details.push(metadata);}, on_error: error => errors.push(error) });
  return { p, reader, result, details, errors, constraints, video, vibrations, sounds, stops: () => stops,
    async tick() { const next = callbacks.entries().next().value; assert.ok(next); callbacks.delete(next[0]); await next[1](); await flush(); } };
}

(async () => {
  const f = fixture();
  await f.reader.start(); await flush();
  assert.equal(f.result.length, 0);
  await f.tick(); assert.deepEqual(f.result, ['盘点 QR 001']);
  assert.equal(f.details[0].kind, 'qr');
  assert.equal(f.details[0].format, 'qr_code');
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
  let format = 'ean_13';
  const typed = fixture({decode: () => [{rawValue:'8850000009912', format}]});
  await typed.reader.start(); await flush();
  format = 'qr_code';
  await typed.tick(); assert.equal(typed.result.length, 0, 'Identical text in different formats is not a stable detection');
  await typed.tick(); assert.equal(typed.details[0].kind, 'qr', 'Numeric QR must remain QR');
  typed.reader.resume(); format = 'ean_13';
  await typed.tick(); await typed.tick(); assert.equal(typed.details[1].kind, 'barcode');
  typed.reader.resume(); format = 'unknown';
  await typed.tick(); await typed.tick(); assert.equal(typed.details[2].kind, null, 'Unknown formats must not be guessed from text');
  typed.reader.stop();

  // 反馈：读到的那一刻只闪 + 震，**不出声**（出声会变成"一件事两声"）；结果那一声由
  // 调用方在成了/没成的时候放（官方 POS 的口径）。
  const feedback = fixture();
  await feedback.reader.start(); await flush();
  await feedback.tick();
  assert.deepEqual(feedback.result, ['盘点 QR 001']);
  assert.deepEqual(feedback.sounds, [], '读到的瞬间不放音效');
  assert.deepEqual(feedback.vibrations, [60], '读到的瞬间震一下');
  feedback.reader.stop();

  // 账户静音：震动跟音效同一个开关，一起停。
  const quiet = fixture({ muted: 1 });
  await quiet.reader.start(); await flush();
  await quiet.tick();
  assert.deepEqual(quiet.result, ['盘点 QR 001']);
  assert.deepEqual(quiet.vibrations, [], '静音时不震');
  assert.deepEqual(quiet.sounds, [], '静音时也不出声');
  // 结果音：命中 `submit`、没命中 `error`，跟官方 POS 用同两个音（静音与否由官方
  // `play_sound` 自己判断，所以这里只断言我们点的是哪一声）。
  quiet.p.scan_result_sound(true); quiet.p.scan_result_sound(false);
  assert.deepEqual(quiet.sounds, ['submit', 'error']);
  quiet.reader.stop();

  console.log('PASS two-frame decode, exact QR, pause/resume, torch, stream cleanup and late permission cancellation');
  console.log('PASS decoder format propagation, numeric QR and mixed-format stability');
  console.log('PASS read-time feedback is silent (flash + vibrate only), mute stops the vibration, result sound is submit/error');
})().catch(error => { console.error(error); process.exitCode = 1; });
