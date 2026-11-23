// 产品列表回来时条件还在（评估 docs/work/2026-11-23-list-keep-filters-评估.md）：取数前存进本人 User Settings，建列表时取回
// 页头字段 / 筛选 / 排序 / 每页条数；链接带来的 route_options 优先不取回；换公司不串；表单里的列表不存；白名单外的条件、
// 已不在选项里的 Select 值丢掉。走真实 list.js，假 BaseList / user_settings，不连站点。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../apps/pearnly_core/pearnly_core/public/desk/list.js'), 'utf8');

// 什么方法都接、都返回自己的 jQuery 替身
const chain = new Proxy(function () {}, { get: (target, key) => (key === 'length' ? 0 : chain), apply: () => chain });

function harness({ company = 'CO-A', route = 'pearnly-products', route_options = null, store = {} } = {}) {
  const routerChanges = [];
  const frappe = {
    pearnly: {}, ui: {}, meta: {}, boot: { pearnly_company: company }, route_options,
    get_route: () => [route], utils: { debounce: fn => fn }, router: { on: (event, fn) => { routerChanges.push(fn); } },
    model: { with_doctype: async () => {}, user_settings: { save: (doctype, key, value) => { store[`${doctype}|${key}`] = value; } } },
    get_user_settings: (doctype, key) => store[`${doctype}|${key}`],
    get_meta: () => ({ fields: [] }),
    views: {
      BaseList: class {
        constructor(options) { Object.assign(this, options); this.filters = []; }
        setup_main_section() {
          this.$frappe_list = chain; this.$result = chain; this.$paging_area = chain; this.$no_result = chain;
          this.filter_area = { get: () => this.filters };
        }
        freeze() {} prepare_data() {} toggle_result_area() {} get_no_result_message() { return ''; } reset_defaults() {}
        on_row_checked() {} get_checked_items() { return []; }
      },
      ListView: { prototype: { render_list() {}, apply_column_widths() {}, get_no_result_message() { return ''; }, setup_check_events() {}, get_checked_items() { return []; }, clear_checked_items() {}, update_checkbox() {} } },
    },
  };
  vm.runInNewContext(source, { frappe, window: {}, $: () => chain, __: v => v, setTimeout, clearTimeout });
  return { frappe, p: frappe.pearnly, store, navigate: () => routerChanges.forEach(fn => fn()) };
}

function control(fieldname, fieldtype = 'Data', options) {
  return { df: { fieldname, fieldtype, options }, value: '', get_value() { return this.value; },
    async set_value(v) { this.value = v; this.change?.(); } };
}

async function open(h, { fields, container } = {}) {
  const loads = [];
  const page = { main: chain, wrapper: chain, page_form: chain, fields_dict: fields || {} };
  const list = h.p.list({ page, container, doctype: 'ERP Product', columns: [{ fieldname: 'product_name' }],
    filter_fields: [{ fieldname: 'is_active' }], sort_fields: ['product_name'],
    load: async args => { loads.push({ ...args, search: page.fields_dict.search?.get_value() }); return { items: [], total: 0 }; } });
  list.resize_list = () => {};  // 量宽度靠真 DOM，这里不测
  for (const c of Object.values(page.fields_dict)) c.change = () => list.reload();
  await list.ready;
  return { list, loads, page };
}

test('输了搜索、筛选、排序、每页条数，回来重建列表时都在，只取一次数', async () => {
  const store = {};
  const first = await open(harness({ store }), { fields: { search: control('search') } });
  first.page.fields_dict.search.value = 'TEST';
  first.list.filters = [['ERP Product', 'is_active', '=', 1]];
  first.list.sort_by = 'product_name'; first.list.sort_order = 'desc'; first.list.selected_page_count = 100;
  await first.list.reload();
  const back = await open(harness({ store }), { fields: { search: control('search') } });
  assert.equal(back.loads.length, 1, '填回时不连着取好几次');
  assert.deepEqual(back.loads[0], { start: 0, page_length: 100, sort_by: 'product_name', sort_order: 'desc', search: 'TEST' });
  assert.deepEqual(back.list.filters, [['ERP Product', 'is_active', '=', 1]]);
});

test('链接带来的条件（route_options）优先：这次不取回，但照样存', async () => {
  const store = { 'ERP Product|pearnly:pearnly-products:CO-A': { fields: { search: 'OLD' }, filters: [] } };
  const h = harness({ store, route_options: { search: 'FROM-LINK' } });
  const { loads } = await open(h, { fields: { search: control('search') } });
  assert.equal(loads[0].search, '');
  assert.ok(store['ERP Product|pearnly:pearnly-products:CO-A'], '照样存');
});

test('换公司不带条件：另一家公司的列表是空的条件', async () => {
  const store = {};
  const a = await open(harness({ store, company: 'CO-A' }), { fields: { search: control('search') } });
  a.page.fields_dict.search.value = 'WH-A';
  await a.list.reload();
  const b = await open(harness({ store, company: 'CO-B' }), { fields: { search: control('search') } });
  assert.equal(b.loads[0].search, '');
  const again = await open(harness({ store, company: 'CO-A' }), { fields: { search: control('search') } });
  assert.equal(again.loads[0].search, 'WH-A');
});

test('表单里的列表（container）不存不取', async () => {
  const store = {};
  await open(harness({ store }), { container: {}, fields: { search: control('search') } });
  assert.deepEqual(store, {});
});

test('白名单外的筛选、已不在选项里的 Select 值丢掉', async () => {
  const store = { 'ERP Product|pearnly:pearnly-products:CO-A': {
    filters: [['ERP Product', 'is_active', '=', 1], ['ERP Product', 'gone_field', '=', 'x']],
    fields: { kind: 'removed', search: 'KEEP' } } };
  const { list, page } = await open(harness({ store }),
    { fields: { search: control('search'), kind: control('kind', 'Select', 'all\nstock') } });
  assert.deepEqual(list.filters, [['ERP Product', 'is_active', '=', 1]]);
  assert.equal(page.fields_dict.kind.value, '');
  assert.equal(page.fields_dict.search.value, 'KEEP');
});

test('页头字段一改就整张重建（库存页「显示方式」）：填回不死循环，重建出来的列表照样带筛选', async () => {
  const store = { 'ERP Stock Balance|pearnly:pearnly-products:CO-A': {
    fields: { view: 'product', search: 'X' }, filters: [['ERP Stock Balance', 'is_active', '=', 1]] } };
  const h = harness({ store });
  const page = { main: chain, wrapper: chain, page_form: chain, fields_dict: {} };
  page.fields_dict.search = control('search');
  page.fields_dict.view = control('view', 'Select', [{ value: 'position' }, { value: 'product' }]);
  page.fields_dict.view.value = 'position';
  const built = [];
  let list = null;
  const build = () => {
    list?.destroyed !== undefined && (list.destroyed = true);
    list = h.p.list({ page, doctype: 'ERP Stock Balance', columns: [{ fieldname: 'product_name' }],
      filter_fields: [{ fieldname: 'is_active' }],
      load: async () => { built.push({ view: page.fields_dict.view.value, search: page.fields_dict.search.value }); return { items: [], total: 0 }; } });
    list.resize_list = () => {};
    return list;
  };
  page.fields_dict.search.change = () => list?.reload();
  page.fields_dict.view.change = () => { if (list) build(); };
  const first = build();
  await first.ready;
  await new Promise(r => setTimeout(r, 20));
  await list.ready;
  assert.ok(built.length <= 2, `取数 ${built.length} 次，没有死循环`);
  assert.deepEqual(built.at(-1), { view: 'product', search: 'X' });
  assert.deepEqual(list.filters, [['ERP Stock Balance', 'is_active', '=', 1]]);
  assert.equal(page.pearnly_restoring, false);
});

test('同一次进页面里，人改字段引起的重建不再取回旧存档；换了路由再回来才取回', async () => {
  const store = { 'ERP Product|pearnly:pearnly-products:CO-A': { fields: { search: 'OLD' }, filters: [] } };
  const h = harness({ store });
  const page = { main: chain, wrapper: chain, page_form: chain, fields_dict: { search: control('search') } };
  const make = async () => {
    const loads = [];
    const list = h.p.list({ page, doctype: 'ERP Product', columns: [{ fieldname: 'product_name' }],
      load: async () => { loads.push(page.fields_dict.search.value); return { items: [], total: 0 }; } });
    list.resize_list = () => {};
    await list.ready;
    return loads;
  };
  assert.deepEqual(await make(), ['OLD']);          // 进页面：取回
  page.fields_dict.search.value = 'NEW';            // 人改了，页面重建列表
  assert.deepEqual(await make(), ['NEW']);          // 不把 OLD 盖回来，且存成 NEW
  page.fields_dict.search.value = '';
  h.navigate();                                     // 去别页再回来（routed_page 每次显示都重建字段）
  assert.deepEqual(await make(), ['NEW']);
});

test('列表拆掉时一并拆掉原生筛选区（同一页重建列表不多出一颗「筛选」）', async () => {
  const h = harness();
  const { list } = await open(h, { fields: {} });
  const removed = [];
  list.$filter_section = { remove: () => removed.push('filter-section') };
  list.filter_area = { get: () => [], standard_filters_wrapper: { remove: () => removed.push('standard') } };
  list.destroy();
  assert.deepEqual(removed, ['filter-section', 'standard']);
});
