// 列表刷新 `p.live`：实时事件为主（同一阵子的合成一次）、有任务在跑时短间隔兜底、否则 60 秒、看不见不刷、回到页面马上刷、
// 切走 / stop 后不再刷并摘掉监听、两次刷新至少隔 min_ms。走真实 product.js，假 frappe.realtime / document / window，不连站点。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname,
  '../apps/pearnly_core/pearnly_core/public/desk/product.js'), 'utf8');

function load() {
  const handlers = {}, listeners = {};
  const realtime = {
    on: (event, fn) => { (handlers[event] ||= new Set()).add(fn); },
    off: (event, fn) => { handlers[event]?.delete(fn); },
  };
  const document = {
    hidden: false, body: {classList: {add() {}}},
    addEventListener: (event, fn) => { (listeners[event] ||= new Set()).add(fn); },
    removeEventListener: (event, fn) => { listeners[event]?.delete(fn); },
  };
  const window = {
    addEventListener: (event, fn) => { (listeners['window:' + event] ||= new Set()).add(fn); },
    removeEventListener: (event, fn) => { listeners['window:' + event]?.delete(fn); },
  };
  const frappe = {provide() {}, pearnly: {}, boot: {}, re_route: {}, model: {}, realtime,
    ui: {Sidebar: class {}}, breadcrumbs: {set_custom_breadcrumbs() {}}, datetime: {str_to_user: v => v}};
  vm.runInNewContext(source, {frappe, window, document, $: () => ({on() {}}), __: k => k, locals: {},
    setTimeout, clearTimeout, localStorage: {getItem() { return null; }, setItem() {}, length: 0, key() {}}});
  const emit = (event, data) => [...(handlers[event] || [])].forEach(fn => fn(data));
  const fire = event => [...(listeners[event] || [])].forEach(fn => fn());
  return {p: frappe.pearnly, emit, fire, document, handlers, listeners};
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

test('事件合并成一次刷新，不相干的事件不刷', async () => {
  const {p, emit} = load();
  let count = 0;
  const live = p.live({refresh: async () => { count++; }, events: ['changed'], accept: d => d.kind === 'mine',
    settle_ms: 20, idle_ms: 10000, min_ms: 0});
  emit('changed', {kind: 'mine'}); emit('changed', {kind: 'mine'}); emit('changed', {kind: 'other'});
  await sleep(60);
  assert.equal(count, 1);
  emit('changed', {kind: 'other'});
  await sleep(40);
  assert.equal(count, 1);
  live.stop();
});

test('有任务在跑时按短间隔兜底，没有就等长间隔', async () => {
  const {p} = load();
  let count = 0, busy = true;
  const live = p.live({refresh: async () => { count++; }, busy: () => busy, busy_ms: 20, idle_ms: 10000, min_ms: 0});
  await sleep(75);
  assert.ok(count >= 2, `busy refreshes: ${count}`);
  busy = false;
  const before = count;
  await sleep(80);
  assert.ok(count <= before + 1, 'idle interval is long');
  live.stop();
});

test('看不见时不刷，回到页面 / 窗口拿回焦点马上刷一次', async () => {
  const {p, fire, document} = load();
  let count = 0;
  const live = p.live({refresh: async () => { count++; }, busy: () => true, busy_ms: 20, settle_ms: 5, min_ms: 0});
  document.hidden = true;
  await sleep(60);
  const hidden = count;
  await sleep(60);
  assert.equal(count, hidden);
  document.hidden = false;
  fire('visibilitychange');
  await sleep(15);
  assert.equal(count, hidden + 1);
  fire('window:focus');
  await sleep(15);
  assert.equal(count, hidden + 2);
  live.stop();
});

test('切走或 stop 后不再刷，监听全部摘掉', async () => {
  const {p, emit, handlers, listeners} = load();
  let count = 0, current = true;
  const live = p.live({refresh: async () => { count++; }, events: ['a', 'b'], busy: () => true, busy_ms: 10,
    settle_ms: 5, min_ms: 0, still_current: () => current});
  await sleep(35);
  current = false;
  await sleep(30);
  const after = count;
  emit('a', {});
  await sleep(30);
  assert.equal(count, after);
  assert.equal(handlers.a.size + handlers.b.size, 0);
  assert.equal(listeners.visibilitychange.size + listeners['window:focus'].size, 0);
  live.stop();  // 再调一次也没事
});

test('刷新抛错不影响下一次；正在刷时来的事件等这次完了再刷一次', async () => {
  const {p, emit} = load();
  let count = 0;
  const live = p.live({refresh: async () => { count++; await sleep(30); if (count === 1) throw new Error('x'); },
    events: ['e'], settle_ms: 1, idle_ms: 10000, min_ms: 0});
  void live.now();
  await sleep(5);
  emit('e', {});
  await sleep(100);
  assert.equal(count, 2);
  live.stop();
});

test('两次刷新至少隔 min_ms，事件来得太密就推到间隔满了再刷一次', async () => {
  const {p, emit} = load();
  let count = 0;
  const live = p.live({refresh: async () => { count++; }, events: ['e'], settle_ms: 1, idle_ms: 10000, min_ms: 60});
  void live.now();
  await sleep(5);
  assert.equal(count, 1);
  emit('e', {}); await sleep(10); emit('e', {});
  await sleep(20);
  assert.equal(count, 1, 'too soon after the last refresh');
  await sleep(60);
  assert.equal(count, 2, 'one refresh once the spacing passed');
  live.stop();
});
