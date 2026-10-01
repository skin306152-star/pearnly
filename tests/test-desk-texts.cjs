// 页面文案缓存（`p.texts`）：同一会话每个前缀只取一次；有语言包指纹时存进浏览器，刷新不再取；指纹一变旧的作废。
// 走真实 product.js，假 frappe.call / localStorage，不连站点。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname,
  '../apps/pearnly_core/pearnly_core/public/desk/product.js'), 'utf8');
const PACK = {'cowork.recon.title': {th: 'กระทบยอด', zh: '对账 {0}'}};

function storage(broken = false) {
  const data = new Map();
  const fail = () => { if (broken) throw new Error('SecurityError'); };
  return {
    data,
    get length() { fail(); return data.size; },
    key: i => { fail(); return [...data.keys()][i] ?? null; },
    getItem: key => { fail(); return data.has(key) ? data.get(key) : null; },
    setItem: (key, value) => { fail(); data.set(key, String(value)); },
    removeItem: key => { fail(); data.delete(key); },
  };
}

function load({version = 'v1', store = storage(), reply = () => PACK} = {}) {
  const calls = [];
  const frappe = {
    provide() {}, pearnly: {}, boot: {lang: 'zh', pearnly_text_version: version}, re_route: {}, model: {},
    ui: {Sidebar: class {}}, breadcrumbs: {set_custom_breadcrumbs() {}},
    datetime: {str_to_user: value => value},
    call: async ({method, args}) => { calls.push([method, args.prefix]); return {message: reply()}; },
  };
  vm.runInNewContext(source, {frappe, window: {}, document: {body: {classList: {add() {}}}},
    $: () => ({on() {}}), __: key => key, localStorage: store, JSON, locals: {}});
  return {p: frappe.pearnly, calls, store};
}

test('同一会话同一前缀只请求一次（并发的也合成一次）', async () => {
  const {p, calls} = load();
  const [a, b] = await Promise.all([p.texts('cowork.recon.'), p.texts('cowork.recon.')]);
  await p.texts('cowork.recon.');
  assert.equal(calls.length, 1);
  assert.equal(a('cowork.recon.title', 'A'), '对账 A');
  assert.equal(b('missing.key'), 'missing.key');
});

test('刷新后（新会话、同指纹）直接用浏览器里的，不再请求；指纹变了重新取并清掉旧的', async () => {
  const store = storage();
  store.setItem('other-app-key', 'keep');
  await load({store}).p.texts('cowork.recon.');
  const again = load({store});
  assert.equal((await again.p.texts('cowork.recon.'))('cowork.recon.title', 'B'), '对账 B');
  assert.equal(again.calls.length, 0);
  const next = load({version: 'v2', store});
  await next.p.texts('cowork.recon.');
  assert.equal(next.calls.length, 1);
  assert.deepEqual([...store.data.keys()].sort(), ['other-app-key', 'pearnly_text:v2:cowork.recon.']);
});

test('没有指纹或浏览器存储不可用时照旧去取，不报错；空包不存', async () => {
  const none = load({version: null});
  await none.p.texts('cowork.recon.');
  assert.equal(none.store.data.size, 0);
  const broken = load({store: storage(true)});
  assert.equal((await broken.p.texts('cowork.recon.'))('cowork.recon.title', 'C'), '对账 C');
  assert.equal(broken.calls.length, 1);
  const empty = load({reply: () => ({})});
  await empty.p.texts('cowork.recon.');
  assert.equal(empty.store.data.size, 0);
});

test('请求失败不留下坏的缓存，下一次重新请求', async () => {
  let fail = true;
  const {p, calls} = load({reply: () => { if (fail) throw new Error('offline'); return PACK; }});
  await assert.rejects(p.texts('cowork.recon.'));
  fail = false;
  assert.equal((await p.texts('cowork.recon.'))('cowork.recon.title', 'D'), '对账 D');
  assert.equal(calls.length, 2);
});
