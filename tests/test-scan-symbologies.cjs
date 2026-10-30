// 共用读码器的码制与分路（主会话 10-30：一次覆盖常见码制，原来那些码读得一样快）：主读每帧都跑、只读原来那些（加原生能读的二维）；
// 主读没读到时轮流补读（WASM 的二维缩半读、DataBar 与原生读不了的一维全分辨率）；补读读到的那一路下一帧先读，接着确认。
// 不碰相机与真解码：原生 BarcodeDetector 与 WASM 读码器都用替身，按格式回结果。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('apps/pearnly_core/pearnly_core/public/scan/reader.js', 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
// 读码器在 vm 里跑：它给的数组不是本文件这一边的，按值比
const same = (actual, expected, message) => assert.equal(JSON.stringify(actual), JSON.stringify(expected), message);
const CORE = ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'itf', 'codabar', 'qr_code'];

function fixture({ native = CORE, shows = {} } = {}) {
  // shows：格式 → 画面里那个码（读码器的格式里有它才读得到）；calls 记每一路被调用的格式组与画面尺寸
  const timers = new Map(), calls = [], results = [];
  let id = 0;
  const reader_of = engine => class {
    static async getSupportedFormats() { return native; }
    constructor({ formats }) { this.formats = formats; }
    async detect(frame) {
      calls.push({ engine, formats: this.formats, width: frame.width ?? frame.videoWidth });
      const hit = Object.entries(shows).find(([format]) => this.formats.includes(format));
      return hit ? [{ format: hit[0], rawValue: hit[1] }] : [];
    }
  };
  const canvas = { width: 0, height: 0, getContext: () => ({ drawImage() {} }) };
  const window = { isSecureContext: true, setTimeout: callback => { timers.set(++id, callback); return id; }, clearTimeout: key => timers.delete(key),
    BarcodeDetector: native ? reader_of('native') : undefined,
    BarcodeDetectionAPI: { BarcodeDetector: reader_of('wasm'), prepareZXingModule: async () => {} } };
  const track = { stop() {}, getCapabilities: () => ({}) };
  const stream = { getTracks: () => [track], getVideoTracks: () => [track] };
  vm.runInNewContext(source, { window, URL, document: { createElement: () => canvas },
    navigator: { userAgent: 'Mozilla/5.0 (Linux; Android 13) Chrome/156', mediaDevices: { getUserMedia: async () => stream, enumerateDevices: async () => [] } } });
  const video = { readyState: 2, videoWidth: 1920, videoHeight: 1080, play: async () => {} };
  const reader = window.PearnlyScan.reader({ video, on_result: (code, details) => { results.push({ code, ...details }); } });
  return { scan: window.PearnlyScan, reader, calls, results, video,
    async tick() { const next = timers.entries().next().value; assert.ok(next); timers.delete(next[0]); await next[1](); await flush(); } };
}

(async () => {
  const { scan } = fixture();
  for (const format of ['qr_code', 'data_matrix', 'pdf417', 'aztec']) assert.equal(scan.kind(format), 'qr', format);
  for (const format of ['ean_13', 'upc_e', 'code_93', 'codabar', 'databar', 'databar_expanded', 'databar_omni']) assert.equal(scan.kind(format), 'barcode', format);
  assert.equal(scan.kind('maxi_code'), null, 'formats we do not ask for are not guessed');
  console.log('PASS two-dimensional codes classify as qr, linear codes and every DataBar form as barcode');
})().catch(error => { console.error(error); process.exitCode = 1; });

(async () => {
  // 安卓 Chrome：原生能读原来九种和三种二维、读不了 DataBar 与 Code 93 → 主读原生（含二维），补读 WASM 一路（DataBar + Code 93）
  const native = [...CORE, 'data_matrix', 'pdf417', 'aztec'];
  const f = fixture({ native, shows: { ean_13: '8850001000019' } });
  // start() 当场读第一帧；之后每次 tick 是下一帧
  await f.reader.start(); await flush(); await flush();
  await f.tick();
  same(f.results.map(r => [r.code, r.format, r.kind]), [['8850001000019', 'ean_13', 'barcode']]);
  assert.ok(f.calls.every(call => call.engine === 'native'), 'an EAN in view is confirmed by the native pass alone, frame after frame');
  same([...f.calls[0].formats].sort(), [...native].sort(), 'the native pass reads every format it supports');
  console.log('PASS a native main pass confirms the old codes in two frames without any extra pass in between');

  const g = fixture({ native, shows: { databar_expanded: '01088500010000193103000150' } });
  await g.reader.start(); await flush(); await flush();
  same(g.calls.map(call => call.engine), ['native', 'wasm'], 'nothing read: one extra pass runs in the same frame');
  same(g.calls[1].formats, ['code_93', 'databar', 'databar_expanded', 'databar_limited']);
  assert.equal(g.calls[1].width, 1920, 'DataBar is read at full resolution');
  await g.tick();
  same(g.calls.slice(2).map(call => call.engine), ['wasm'], 'the pass that saw the code runs first to confirm it');
  same(g.results.map(r => [r.format, r.kind]), [['databar_expanded', 'barcode']]);
  console.log('PASS DataBar, which no native decoder reads, comes from the WASM extra pass and is confirmed by it');
})().catch(error => { console.error(error); process.exitCode = 1; });

(async () => {
  // iPhone / 没有原生的：主读 WASM（原来那些 + Code 93），补读一路：二维缩一半、一路：DataBar
  const f = fixture({ native: null, shows: { data_matrix: '2856' } });
  await f.reader.start(); await flush(); await flush();
  assert.equal(f.calls[0].engine, 'wasm');
  assert.ok(!f.calls[0].formats.some(format => ['data_matrix', 'pdf417', 'aztec', 'databar'].includes(format)), 'the main pass keeps the old cost');
  same(f.calls[1].formats, ['data_matrix', 'pdf417', 'aztec']);
  assert.equal(f.calls[1].width, 960, 'two-dimensional codes are read on a half resolution frame');
  await f.tick();
  same(f.results.map(r => [r.code, r.format, r.kind]), [['2856', 'data_matrix', 'qr']]);
  console.log('PASS without a native decoder, Data Matrix comes from the half resolution extra pass and classifies as qr');

  const g = fixture({ native: null });
  await g.reader.start(); await flush(); await flush();
  for (let i = 0; i < 3; i++) await g.tick();
  const extras = g.calls.filter((_call, index) => index % 2 === 1).map(call => call.formats[0]);
  same(extras, ['data_matrix', 'databar', 'data_matrix', 'databar'], 'idle frames take the extra passes in turn (Code 93 is in the WASM main pass)');
  console.log('PASS idle frames rotate the extra passes, one per frame after the main pass');

  // 原生少了原来九种里的一种（如 macOS 的 Chrome 没有 Codabar）：照旧整个走 WASM，不拿原生读一半
  const h = fixture({ native: CORE.filter(format => format !== 'codabar'), shows: { codabar: 'A40156B' } });
  await h.reader.start(); await flush(); await flush();
  await h.tick();
  assert.equal(h.calls[0].engine, 'wasm');
  same(h.results.map(r => r.code), ['A40156B']);
  console.log('PASS a native decoder missing one of the original formats falls back to WASM as before');
})().catch(error => { console.error(error); process.exitCode = 1; });
