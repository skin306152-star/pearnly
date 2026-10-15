// Camera lifecycle without hardware: no duplicate delivery, cancellation, exact QR,
// and late permission resolution. Decoder accuracy remains a real-device check.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
// 读码器本体在共用层，ERP 的扫码动作在 ERP；站点上按这个顺序加载（2026-09-26 去重）。
const source = ['apps/pearnly_core/pearnly_core/public/scan/reader.js',
  'apps/pearnly_core/pearnly_core/public/desk/scan.js',
  'apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/scan.js'].map(path => fs.readFileSync(path, 'utf8')).join('\n');
const flush = () => new Promise(resolve => setImmediate(resolve));

function fixture({ acquire, decode, muted = 0, devices = [], ua = 'Mozilla/5.0 (Linux; Android 13) Chrome/106', supported, capture } = {}) {
  const callbacks = new Map(), result = [], details = [], errors = [], constraints = [];
  // 反馈那一层看得见的东西：震动次数与放过的音（音效/震动都归"静音"这一位管）。
  const vibrations = [], sounds = [];
  let id = 0, stops = 0;
  const track = { stop: () => stops++, getCapabilities: () => ({ torch: true }),
    applyConstraints: async value => constraints.push(value) };
  const stream = { getTracks: () => [track], getVideoTracks: () => [track] };
  const video = { readyState: 2, play: async () => {} };
  const $ = () => ({ find: () => ({ get: () => video, addClass() {}, removeClass() {}, one() {} }) });
  const p = {}, formats = ['ean_13','ean_8','upc_a','upc_e','code_128','code_39','itf','codabar','qr_code'];
  class Detector {
    static async getSupportedFormats() { if (supported) return supported(); return formats; }
    async detect() { return decode ? decode() : [{ rawValue: '  盘点 QR 001  ', format: 'qr_code' }]; }
  }
  const requests = [], messages = [];
  const schedule = callback => { callbacks.set(++id, callback); return id; };
  const cancel = key => callbacks.delete(key);
  const window = { BarcodeDetector: Detector, isSecureContext: true, setTimeout: schedule, clearTimeout: cancel,
    location: { href: 'https://site.invalid/desk/pearnly-stock-count/C1' } };
  vm.runInNewContext(source, { window, URL,
    navigator: { userAgent: ua, mediaDevices: {
      getUserMedia: value => { requests.push(value); return acquire ? acquire(stream, value) : Promise.resolve(stream); },
      enumerateDevices: async () => devices },
      vibrate: ms => vibrations.push(ms) },
    __: text => text,
    frappe: { provide() {}, pearnly: p,
      utils: { play_sound: name => sounds.push(name) },
      boot: { user: { mute_sounds: muted }, pearnly_liff_id: '2000-abc' },
      ui: capture ? { Capture: capture } : undefined, msgprint: options => messages.push(options) },
    $, setTimeout: schedule, clearTimeout: cancel,
  });
  // `p.sounds_muted` 是共享层（`pearnly_core/public/desk/product.js`）给的：这份测试只加载
  // scan.js，所以按同一份契约补一个 —— 判据就是官方那一位 `frappe.boot.user.mute_sounds`。
  p.sounds_muted = () => Boolean(muted);
  const reader = p.scan_reader({ host: {}, on_result: (code, metadata) => {result.push(code);details.push(metadata);}, on_error: error => errors.push(error) });
  return { p, reader, window, messages, result, details, errors, constraints, video, vibrations, sounds, requests, stops: () => stops,
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


  // 相机打不开要说清是哪一类原因（安卓上被拒过权限 / 被占用 / 内置浏览器 / 识别器坏），
  // 不再一律说"请在 HTTPS 页面允许"。
  const named = name => Object.assign(new Error(name), { name });
  const blocked = fixture({ acquire: () => Promise.reject(named('NotAllowedError')) });
  await blocked.reader.start();
  assert.equal(blocked.p.scan_failure(blocked.errors[0]), 'denied');
  assert.equal(blocked.p.scan_failure_text(blocked.errors[0]), 'common.scan.denied_android', 'settings path for an Android browser');
  assert.equal(blocked.requests.length, 1, 'A refused permission is not retried');
  const line = fixture({ ua: 'Mozilla/5.0 (Linux; Android 13; wv) Line/14.1.0', acquire: () => Promise.reject(named('NotAllowedError')) });
  await line.reader.start();
  assert.equal(line.p.scan_failure(line.errors[0]), 'in_line');
  const facebook = fixture({ ua: 'Mozilla/5.0 (Linux; Android 13) [FBAN/EMA]', acquire: () => Promise.reject(named('NotAllowedError')) });
  await facebook.reader.start();
  assert.equal(facebook.p.scan_failure(facebook.errors[0]), 'in_app');
  // LINE 没有手机相机权限时，请求既不弹窗也不报错：5 秒后先给提示（读码器继续等），相机后来开了再通知页面收起提示。
  let grant_line;
  const hanging = fixture({ ua: 'Mozilla/5.0 (Linux; Android 13; wv) Line/26.15.1',
    acquire: stream => new Promise(resolve => { grant_line = () => resolve(stream); }) });
  const waits = [], starts = [];
  const slow_reader = hanging.p.scan_reader({ host: {}, on_result: () => {}, on_error: error => hanging.errors.push(error),
    on_waiting: reason => waits.push(reason), on_started: () => starts.push(1) });
  const starting = slow_reader.start(); await flush();
  await hanging.tick();
  assert.deepEqual(waits, ['in_line'], 'a hanging camera request inside LINE gets a hint');
  assert.equal(hanging.errors.length, 0, 'the reader keeps waiting');
  grant_line(); await starting;
  assert.equal(starts.length, 1, 'the page hears when the camera finally opens');
  slow_reader.stop();
  const busy = fixture({ acquire: () => Promise.reject(named('NotReadableError')) });
  await busy.reader.start();
  assert.equal(busy.p.scan_failure(busy.errors[0]), 'busy');
  assert.equal(busy.requests.length, 2, 'A busy or picky camera gets one plain retry');
  // 指定后置起不来的机型：按名字挑后置镜头再开一次，成功就照常读码。
  const picky = fixture({
    devices: [{ kind: 'videoinput', deviceId: 'front', label: 'camera2 1, facing front' },
      { kind: 'videoinput', deviceId: 'rear', label: 'camera2 0, facing back' }],
    acquire: (stream, value) => value.video?.facingMode ? Promise.reject(named('OverconstrainedError')) : Promise.resolve(stream),
  });
  await picky.reader.start(); await flush();
  assert.equal(JSON.stringify(picky.requests[1]), JSON.stringify({ video: { deviceId: { exact: 'rear' } } }));
  assert.equal(picky.errors.length, 0);
  await picky.tick(); assert.deepEqual(picky.result, ['盘点 QR 001']);
  picky.reader.stop();
  // 识别器加载失败：相机已经打开过也要关掉，原因归到"识别器"，不说成相机的事。
  const broken = fixture({ supported: () => Promise.reject(new Error('module')) });
  await broken.reader.start();
  assert.equal(broken.errors.length, 1);
  assert.equal(broken.p.scan_failure(broken.errors[0]), 'decoder');
  assert.equal(broken.stops(), 1, 'The opened camera is released when the reader cannot load');
  assert.equal(hanging.p.scan_failure(Object.assign(new Error('x'), { name: 'NotAllowedError' })), 'in_line');
  // 转 LIFF：只在 LINE 普通内置浏览器里、配了 LIFF、链接上还没有标记时才转，转一次。
  // LINE 把深链交给 LIFF 时只留第一个参数：转 LIFF 与「接着扫」合在一个参数里。
  assert.equal(hanging.p.scan_to_liff(true), true);
  assert.equal(hanging.window.location.href,
    'https://liff.line.me/2000-abc/desk/pearnly-stock-count/C1?pearnly_liff=scan');
  assert.equal(hanging.p.scan_to_liff(true), false, 'already in LIFF: never bounce twice');
  assert.equal(fixture().p.scan_to_liff(), false, 'outside LINE nothing moves');
  console.log('PASS camera failures are classified (blocked, LINE, other in-app browser, busy, decoder), a hanging LINE request gets a hint, a picky rear camera is retried by device');
  // 电脑上「拍照」（框架上传器）打不开相机：同一套第 ③ 步提示，不再是笼统的一句。
  class Capture {
    render_stream() { return Promise.reject(Object.assign(new Error('x'), { name: 'NotAllowedError' })); }
    show_for_desktop() { throw new Error('native message'); }
  }
  const photo = fixture({ ua: 'Mozilla/5.0 (Macintosh) Chrome/141', capture: Capture });
  await new Capture().show_for_desktop(); await flush();
  assert.equal(photo.messages[0].message, 'common.scan.denied', 'desktop photo capture gets the settings hint');
  console.log('PASS desktop photo capture failures use the same settings hint');
  console.log('PASS two-frame decode, exact QR, pause/resume, torch, stream cleanup and late permission cancellation');
  console.log('PASS decoder format propagation, numeric QR and mixed-format stability');
  console.log('PASS read-time feedback is silent (flash + vibrate only), mute stops the vibration, result sound is submit/error');
})().catch(error => { console.error(error); process.exitCode = 1; });
