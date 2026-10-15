// Execute the standalone page's real inline code with fake DOM/media/HTTP.
// No camera, LINE, inventory, network or filesystem writes; true device decoding
// and LIFF identity remain separate acceptance checks.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('apps/pearnly_erp_portal/pearnly_erp_portal/www/stocktake.html', 'utf8');
// 页面的相机与读码用共用读码器（与后台扫码同一份），站点上它先于页面脚本加载。
const reader = fs.readFileSync('apps/pearnly_core/pearnly_core/public/scan/reader.js', 'utf8');
assert.match(html, /<script src="\{\{ versioned_asset\('pearnly_core', 'scan\/reader\.js'\) \}\}"><\/script>/);
const inline = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];
const source = inline.replace('        start();', `window.testPage = {
  call, startCamera, stopCamera, bindForm, render, lookup, setLiff: (value) => { liffId = value; },
  state: (value) => { csrf = value.csrf || ''; ticket = value.ticket || '';
    task = value.task || {name: 'COUNT-1'}; scanMode = Boolean(value.scanMode); },
};`);
assert.notEqual(source, inline, 'test seam must replace boot, not execute real HTTP');
const formats = ['ean_13','ean_8','upc_a','upc_e','code_128','code_39','itf','codabar','qr_code'];
const flush = () => new Promise(resolve => setImmediate(resolve));

function fixture(options = {}) {
  const calls = [], scripts = [], prepares = [], timers = new Map(), events = {};
  const nodes = new Map();
  let timerId = 0, uuid = 0, stopped = 0, acquired = 0, decoderCalls = 0;
  const video = { readyState: 2, paused: false, play: async () => {}, srcObject: null };
  function node(id = '') {
    return { id, value: '', files: [], disabled: false, textContent: '', innerHTML: '',
      style: {}, classList: {add() {}, remove() {}}, parentNode: null,
      prepend(child) { child.parentNode = this; (this.children = this.children || []).unshift(child); },
      removeChild(child) { child.parentNode = null; },
      querySelector(selector) { return selector === 'video' ? video : element(selector); },
      querySelectorAll() { return []; },
    };
  }
  function element(id) { if (!nodes.has(id)) nodes.set(id, node(id)); return nodes.get(id); }
  const stream = { getTracks: () => [{ stop: () => stopped++ }] };
  class Reader {
    static async getSupportedFormats() { return options.nativeFormats || formats; }
    constructor(config) { this.formats = config.formats; decoderCalls++; }
    async detect() { return options.detect ? options.detect() : [{rawValue: '  QR + & 123  ', format: 'qr_code'}]; }
  }
  const browser = {
    isSecureContext: true,
    location: {href: 'https://synthetic.invalid/stocktake?count=1'},
    setTimeout: handler => { timers.set(++timerId, handler); return timerId; },
    clearTimeout: id => timers.delete(id),
    addEventListener: (name, handler) => { events[name] = handler; },
    BarcodeDetector: options.fallback ? undefined : Reader,
  };
  const docEvents = {};
  const document = {getElementById: element, createElement: () => node(), visibilityState: 'visible',
    addEventListener: (name, handler) => { docEvents[name] = handler; },
    head: {appendChild(script) {
      scripts.push(script.src);
      browser.BarcodeDetectionAPI = {BarcodeDetector: Reader, prepareZXingModule: async config => prepares.push(config)};
      script.onload();
    }},
  };
  const item = {name: 'ROW-1', code: 'QR + & 123', product_name: 'Synthetic', qty: 1};
  const result = {item, rows: [item], entries: []};
  vm.runInNewContext(reader + '\n' + source, {
    window: browser, document, URLSearchParams, URL, console,
    navigator: {language: 'th', userAgent: options.userAgent || 'Mozilla/5.0 (Linux; Android 13; wv) Line/26.15.1', vibrate() {},
      mediaDevices: {enumerateDevices: async () => [], getUserMedia: () => {
      acquired++;
      return options.acquire ? options.acquire(stream) : Promise.resolve(stream);
    }}},
    crypto: {randomUUID: () => 'request-' + ++uuid},
    setTimeout: () => 0,
    setInterval: handler => {timers.set(++timerId, handler); return timerId;},
    clearInterval: id => timers.delete(id),
    fetch: async (url, init = {}) => {
      calls.push({url, ...init});
      if (options.fetch) return options.fetch(url, init, result);
      return {ok: true, json: async () => ({message: result})};
    },
  });
  const page = browser.testPage;
  page.state({csrf: 'synthetic-csrf', ticket: 'synthetic-ticket'});
  page.location = () => browser.location;
  return {page, calls, docEvents, scripts, prepares, timers, events, element, item, video,
    stopped: () => stopped, acquired: () => acquired, decoderCalls: () => decoderCalls,
    // 读码是一帧接一帧（setTimeout 链）：取出下一帧执行。
    async tick() { const [id, callback] = timers.entries().next().value || []; assert.ok(callback); timers.delete(id); await callback(); await flush(); },
  };
}

(async () => {
  const transport = fixture();
  await transport.page.call('api_line_stocktake_lookup', {name: 'COUNT / 1', code: 'QR + & ? 中文', qty: 0, missing: null});
  const requested = new URL(transport.calls[0].url, 'https://synthetic.invalid');
  assert.equal(requested.searchParams.get('name'), 'COUNT / 1');
  assert.equal(requested.searchParams.get('code'), 'QR + & ? 中文');
  assert.equal(requested.searchParams.get('qty'), '0');
  assert.equal(requested.searchParams.has('missing'), false);
  assert.equal(requested.searchParams.has('ticket'), false, 'ticket stays out of URL/history');
  assert.equal(transport.calls[0].headers['X-Stocktake-Line'], 'synthetic-ticket');
  await transport.page.call('api_line_stocktake_add', {name: 'COUNT-1', qty: '2'}, true);
  assert.equal(transport.calls[1].url.includes('?'), false);
  assert.deepEqual(JSON.parse(transport.calls[1].body), {ticket: 'synthetic-ticket', name: 'COUNT-1', qty: '2'});
  console.log('PASS GET task/code encoding, ticket header, unchanged POST body');

  const decoded = [], fallback = fixture({fallback: true});
  await fallback.page.startCamera(code => decoded.push(code)); await flush();
  assert.deepEqual(fallback.scripts, ['/assets/pearnly_core/vendor/barcode-detector/ponyfill.js']);
  assert.equal(fallback.prepares[0].overrides.locateFile('reader.wasm'),
    '/assets/pearnly_core/vendor/barcode-detector/zxing_reader.wasm');
  assert.equal(decoded.length, 0, 'the first frame alone is not a confirmed result');
  await fallback.tick(); assert.deepEqual(decoded, ['QR + & 123']);
  assert.equal(fallback.stopped(), 1);
  assert.equal(fallback.video.srcObject, null);
  assert.equal(fallback.timers.size, 0);
  await fallback.page.startCamera(() => {});
  assert.equal(fallback.scripts.length, 1, 'reuse already-loaded bundled decoder');
  fallback.events.pagehide();
  assert.equal(fallback.stopped(), 2);
  assert.equal(fallback.timers.size, 0);
  const partial = fixture({nativeFormats: ['qr_code']});
  await partial.page.startCamera(() => {});
  assert.equal(partial.scripts.length, 1, 'partial native decoder uses same full-format fallback as Desk');
  partial.page.stopCamera(true);
  console.log('PASS native/WASM fallback reuse, two-frame result, exact QR and pagehide cleanup');

  let grant;
  const late = fixture({acquire: stream => new Promise(resolve => {grant = () => resolve(stream);})});
  const pending = late.page.startCamera(() => {throw new Error('cancelled camera produced result');});
  await flush();
  late.page.stopCamera(true); grant(); await pending;
  assert.equal(late.stopped(), 1);
  assert.equal(late.timers.size, 0);
  const duplicate = fixture();
  await Promise.all([duplicate.page.startCamera(() => {}), duplicate.page.startCamera(() => {})]);
  assert.equal(duplicate.acquired(), 1, 'double start cannot retain two streams');
  duplicate.page.stopCamera(true);
  console.log('PASS late permission cancellation and duplicate camera start');

  // 相机打不开：在 LINE 里被拒 → 让人去手机设置里给 LINE 相机权限；被占用 → 说被占用。
  const named = name => Object.assign(new Error(name), {name});
  const refused = fixture({acquire: () => Promise.reject(named('NotAllowedError'))});
  await refused.page.startCamera(() => {}); await flush();
  // 没配 LIFF、弹不出授权：第 ③ 步，按环境给设置路径（安卓 LINE），留在页面上；切回来自动重开。
  assert.equal(refused.element('app').children[0].textContent, 'common.scan.in_line_android');
  refused.docEvents.visibilitychange(); await flush();
  assert.equal(refused.acquired(), 2, 'coming back from the phone settings reopens the camera');
  // LINE 普通内置浏览器不弹相机授权、LIFF 会弹：配了 LIFF 就把这一页换成 LIFF 打开（带标记，到了不再转）。
  const moved = fixture({acquire: () => Promise.reject(named('NotAllowedError'))});
  moved.page.setLiff('2000-abc');
  await moved.page.startCamera(() => {}); await flush();
  assert.equal(moved.page.location().href, 'https://liff.line.me/2000-abc/stocktake?count=1&pearnly_liff=1');
  const arrived = fixture({acquire: () => Promise.reject(named('NotAllowedError'))});
  arrived.page.setLiff('2000-abc');
  arrived.page.location().href = 'https://synthetic.invalid/stocktake?count=1&pearnly_liff=1';
  await arrived.page.startCamera(() => {}); await flush();
  assert.equal(arrived.page.location().href, 'https://synthetic.invalid/stocktake?count=1&pearnly_liff=1', 'never bounce twice');
  assert.equal(arrived.element('app').children[0].textContent, 'common.scan.in_line_android');
  const taken = fixture({acquire: () => Promise.reject(named('NotReadableError'))});
  await taken.page.startCamera(() => {}); await flush();
  assert.equal(taken.element('toast').textContent, 'common.scan.busy');
  // 请求一直挂着（LINE 普通内置浏览器）：5 秒后同样转 LIFF。
  const hanging = fixture({acquire: () => new Promise(() => {})});
  hanging.page.setLiff('2000-abc');
  void hanging.page.startCamera(() => {}); await flush();
  await hanging.tick();
  assert.match(hanging.page.location().href, /^https:\/\/liff\.line\.me\/2000-abc\/stocktake\?/);
  console.log('PASS inside LINE a refused or hanging camera reopens the page in LIFF once (native camera prompt there); elsewhere it says why');

  const continuous = fixture();
  continuous.page.state({scanMode: true});
  continuous.element('qty').value = '2';
  continuous.page.bindForm(continuous.item, [continuous.item]);
  await continuous.element('save').onclick();
  assert.equal(continuous.acquired(), 1, 'successful camera-mode save restarts scan');
  const lookup = continuous.calls.find(row => row.url.includes('api_line_stocktake_lookup'));
  assert.equal(new URL(lookup.url, 'https://synthetic.invalid').searchParams.get('code'), continuous.item.code);
  continuous.page.stopCamera(true);
  const manual = fixture();
  manual.element('qty').value = '2';
  manual.page.bindForm(manual.item, [manual.item]);
  await manual.element('save').onclick();
  assert.equal(manual.acquired(), 0, 'manual count does not acquire a camera');
  console.log('PASS save resumes camera mode only; manual entry stays manual');

  let attempts = 0;
  const retry = fixture({fetch: async (url, init, result) => {
    if (url.endsWith('api_line_stocktake_add') && ++attempts === 1) throw new Error('response lost');
    return {ok: true, json: async () => ({message: result})};
  }});
  retry.element('qty').value = '2';
  retry.page.bindForm(retry.item, [retry.item]);
  await retry.element('save').onclick();
  assert.equal(retry.element('save').disabled, false);
  await retry.element('save').onclick();
  const posts = retry.calls.filter(row => row.url.endsWith('api_line_stocktake_add'));
  assert.equal(posts.length, 2);
  assert.equal(JSON.parse(posts[0].body).request_id, JSON.parse(posts[1].body).request_id);
  let finish;
  const busy = fixture({fetch: (url, init, result) => url.endsWith('api_line_stocktake_add')
    ? new Promise(resolve => {finish = () => resolve({ok: true, json: async () => ({message: {}})});})
    : Promise.resolve({ok: true, json: async () => ({message: result})})});
  busy.element('qty').value = '2';
  busy.page.bindForm(busy.item, [busy.item]);
  const save = busy.element('save').onclick;
  const saving = save(); await flush(); await save();
  assert.equal(busy.calls.length, 1, 'double click must not submit a second observation');
  finish(); await saving;
  console.log('PASS lost-response retry reuses idempotency key and duplicate save clicks are blocked');
})().catch(error => {console.error(error); process.exitCode = 1;});
