// 多仓闭环 P1 前端 + 验收 10-27 两条存量缺陷（按类），走真实的 core product.js 与 ERP transactions.js，不连站点：
//   1. 页内切换记录：页头入口全部清掉再按新记录重建——原生 `add_inner_button` 同时往「⋯」菜单加一项，只清工具栏会把上一张单的
//      「导出」「结束盘点」留在菜单里（盘点页从已过账切到进行中多出「导出」）。所有产品页都走 `p.clear_actions`（静态扫描兜底）。
//   2. 服务端拒绝读取：详情页换成原生无权限页（`frappe.show_not_permitted`），不白屏、不留未处理的 Promise 拒绝。
//   3. 单头「仓库 + 库位」（`p.place`）：明细行跟着单头走、逐行改过的不动；老单没有单头时从明细推出；不再读启动数据里的卖货仓。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const ROOT = path.join(__dirname, '..');
const read = file => fs.readFileSync(path.join(ROOT, file), 'utf8');
const CORE = read('apps/pearnly_core/pearnly_core/public/desk/product.js');
const TRANSACTIONS = read('apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/transactions.js');

function load({ companies = [] } = {}) {
  const shown = [], calls = [];
  const msg_dialog = { display: true, custom_onhide: () => calls.push('re_route'), hide() { this.display = false; calls.push('hide'); } };
  const frappe = { provide() {}, pearnly: {}, boot: { pearnly_pages: [], pearnly_company: 'CO-1', pearnly_companies: companies },
    re_route: {}, model: {}, realtime: { on() {}, off() {} }, ui: { Sidebar: class {} }, datetime: { str_to_user: v => v },
    breadcrumbs: { set_custom_breadcrumbs() {}, add() {} }, utils: { escape_html: v => v }, msg_dialog,
    show_not_permitted: name => shown.push(name), get_route: () => ['pearnly-purchase', 'PUR-OTHER'],
    call: async ({ method, args }) => { calls.push({ method, args }); return { message: { warehouse: 'WH-2', reason: 'last' } }; } };
  const context = { frappe, window: { addEventListener() {} }, document: { body: { classList: { add() {} } }, addEventListener() {} },
    $: () => ({ on() {} }), __: k => k, locals: {}, setTimeout, clearTimeout,
    localStorage: { getItem() { return null; }, setItem() {}, length: 0, key() {} } };
  vm.runInNewContext(CORE, context);
  vm.runInNewContext(TRANSACTIONS, context);
  return { p: frappe.pearnly, frappe, shown, calls, msg_dialog };
}

function page() {
  const cleared = [];
  const record = name => () => cleared.push(name);
  return { cleared, clear_inner_toolbar: record('toolbar'), clear_menu: record('menu'), clear_primary_action: record('primary'),
    clear_secondary_action: record('secondary'), clear_custom_actions: record('custom'), clear_icons: record('icons'),
    clear_indicator: record('indicator'), clear_fields: record('fields'), hide_form() {},
    page_form: { detach() {} }, main: { empty() { return { append() { return { removeClass() {} }; } }; } } };
}

test('切换记录：页头工具栏、「⋯」菜单、主次按钮、自定义按钮、图标一起清', () => {
  const { p } = load();
  const target = page();
  p.clear_page(target);
  for (const part of ['toolbar', 'menu', 'primary', 'secondary', 'custom', 'icons', 'indicator'])
    assert.ok(target.cleared.includes(part), part);
});

test('详情页无权限：换成原生无权限页，收起请求层那个会跳回上一页的弹窗，不抛出', async () => {
  const { p, shown, calls, msg_dialog } = load();
  const wrapper = {};
  const show = p.routed_page({ wrapper, root: 'pearnly-purchase', render: async () => {
    throw { status: 403, responseJSON: { exc_type: 'PermissionError' } };
  } });
  await show();
  assert.deepEqual(shown, ['pearnly-not-permitted']);
  assert.equal(msg_dialog.display, false);
  assert.ok(!calls.includes('re_route'), 'closing the native notice must not route away from the not-permitted page');
});

test('别的错误照常抛出（不被当成无权限吞掉）', async () => {
  const { p, shown } = load();
  const show = p.routed_page({ wrapper: {}, root: 'pearnly-purchase', render: async () => { throw new Error('boom'); } });
  await assert.rejects(show(), /boom/);
  assert.deepEqual(shown, []);
  await assert.rejects(p.guard_route(Promise.reject(new Error('other'))), /other/);
  await p.guard_route(Promise.reject({ exc_type: 'PermissionError' }));
  assert.deepEqual(shown, ['pearnly-not-permitted']);
});

function rows(values) {
  return values.map(value => ({ value: { ...value }, get(key) { return this.value[key] || ''; },
    set(changes) { Object.assign(this.value, changes); } }));
}

test('单头换仓：跟着单头的行换仓并取新单头库位；逐行改过仓的行不动', () => {
  const { p } = load();
  const lines = rows([{ warehouse: 'WH-1', location: 'A1' }, { warehouse: '' }, { warehouse: 'WH-9', location: 'Z' }]);
  p.place.follow(lines, { warehouse: 'WH-1', location: 'A1' }, { warehouse: 'WH-2', location: '' });
  assert.deepEqual(lines.map(row => row.value), [{ warehouse: 'WH-2', location: '' }, { warehouse: 'WH-2', location: '' },
    { warehouse: 'WH-9', location: 'Z' }]);
});

test('单头只换库位：同仓里库位空着或等于旧单头库位的行跟，逐行改过库位的不动', () => {
  const { p } = load();
  const lines = rows([{ warehouse: 'WH-1', location: '' }, { warehouse: 'WH-1', location: 'A1' }, { warehouse: 'WH-1', location: 'B7' }]);
  p.place.follow(lines, { warehouse: 'WH-1', location: 'A1' }, { warehouse: 'WH-1', location: 'A2' });
  assert.deepEqual(lines.map(row => row.value.location), ['A2', 'A2', 'B7']);
});

test('单头控件：换仓清空单头库位，行跟着走；同值不重复动', async () => {
  const { p } = load();
  const values = { warehouse: 'WH-1', location: 'A1' };
  const form = { get_value: key => values[key], set_value: async (key, value) => { values[key] = value; } };
  const lines = rows([{ warehouse: 'WH-1', location: 'A1' }]);
  const place = p.place.track(form, () => lines);
  values.warehouse = 'WH-2';
  await place.changed('warehouse');
  assert.equal(values.location, '');
  assert.deepEqual(lines[0].value, { warehouse: 'WH-2', location: '' });
  await place.changed('location');  // 清库位触发的那一次：值没变，不再动行
  assert.deepEqual(lines[0].value, { warehouse: 'WH-2', location: '' });
});

test('打开已有的单：整批填单头时不跟（不把刚填进来的库位当成换仓清掉），sync 之后人改才跟', async () => {
  const { p } = load();
  const values = { warehouse: '', location: '' };
  const form = { get_value: key => values[key], set_value: async (key, value) => { values[key] = value; } };
  const lines = rows([{ warehouse: 'WH-1', location: 'A1' }]);
  const place = p.place.track(form, () => lines, { live: false });
  Object.assign(values, { warehouse: 'WH-1', location: 'A1' });
  await place.changed('warehouse');
  assert.deepEqual({ ...values }, { warehouse: 'WH-1', location: 'A1' });
  place.sync();
  values.location = 'A2';
  await place.changed('location');
  assert.equal(lines[0].value.location, 'A2');
});

test('打开老单：单上没有单头时，有货的行都在同一个仓（库位）就当单头；各在各的仓就空着', () => {
  const { p } = load();
  assert.deepEqual({ ...p.place.of({ warehouse: 'WH-3', location: 'C' }) }, { warehouse: 'WH-3', location: 'C' });
  assert.deepEqual({ ...p.place.of({ lines: [{ warehouse: 'WH-1', location: 'A' }, { warehouse: 'WH-1', location: 'A' }, {}] }) },
    { warehouse: 'WH-1', location: 'A' });
  assert.deepEqual({ ...p.place.of({ lines: [{ warehouse: 'WH-1', location: 'A' }, { warehouse: 'WH-1', location: 'B' }] }) },
    { warehouse: 'WH-1', location: '' });
  assert.deepEqual({ ...p.place.of({ lines: [{ warehouse: 'WH-1' }, { warehouse: 'WH-2' }] }) }, { warehouse: '', location: '' });
});

test('预填问服务端 `warehouses.api_prefill`（单据类型 + 当前公司），不读启动数据', async () => {
  const { p, calls } = load();
  assert.equal(await p.place.prefill('purchase'), 'WH-2');
  const call = calls.find(item => item.method);
  assert.equal(call.method, 'pearnly_erp_portal.inventory.warehouses.api_prefill');
  assert.deepEqual({ ...call.args }, { kind: 'purchase', company: 'CO-1', context: '' });
});

test('当前公司接了 Express：不给新建仓、不录期初', () => {
  assert.equal(load({ companies: [{ name: 'CO-1', is_current: true, express_linked: true }] }).p.express_company(), true);
  assert.equal(load({ companies: [{ name: 'CO-1', is_current: true }] }).p.express_company(), false);
});

// 按类兜底：产品页只用 `p.clear_actions` / `p.clear_page` 清页头（不再出现只清工具栏的写法）；自己按路由重绘的页面用
// `p.guard_route` 包住；表单不再读启动数据里的卖货仓。
const DESK = ['apps/pearnly_core', 'apps/pearnly_erp_portal', 'apps/pearnly_cowork', 'apps/pearnly_dms']
  .flatMap(app => {
    const dir = path.join(ROOT, app, app.split('/')[1], 'public/desk');
    const walk = folder => fs.readdirSync(folder, { withFileTypes: true }).flatMap(entry => entry.isDirectory()
      ? walk(path.join(folder, entry.name)) : entry.name.endsWith('.js') ? [path.join(folder, entry.name)] : []);
    return fs.existsSync(dir) ? walk(dir) : [];
  });

test('按类：只清工具栏不清菜单的写法不再出现', () => {
  const offenders = [];
  for (const file of DESK) {
    if (file.includes('/pos/')) continue;  // 官方 POS 的控制器（照搬上游）自己管页头
    const lines = fs.readFileSync(file, 'utf8').split('\n');
    lines.forEach((line, index) => {
      if (!/\.clear_inner_toolbar\(\)/.test(line)) return;
      if (file.endsWith('pearnly_core/public/desk/product.js')) return;  // `p.clear_actions` 本身
      const near = lines.slice(Math.max(0, index - 3), index + 8).join('\n');
      if (!/\.clear_menu\(\)/.test(near)) offenders.push(`${path.relative(ROOT, file)}:${index + 1}`);
    });
  }
  assert.deepEqual(offenders, []);
});

test('按类：自己按路由重绘的页面，切到别的记录时无权限也换原生无权限页', () => {
  const offenders = DESK.filter(file => /on_page_show\s*=\s*\(\)\s*=>\s*\{[^}]*void show_route\(\)/.test(fs.readFileSync(file, 'utf8')))
    .map(file => path.relative(ROOT, file));
  assert.deepEqual(offenders, []);
});

test('表单不再读启动数据里的卖货仓（`frappe.boot.pearnly_warehouse`，登录时算一次、切公司不跟）', () => {
  const offenders = DESK.filter(file => /boot\.pearnly_warehouse\b/.test(fs.readFileSync(file, 'utf8')))
    .map(file => path.relative(ROOT, file));
  assert.deepEqual(offenders, []);
});
