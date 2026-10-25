// 产品列表的官方筛选（list.js）：条件下拉列 Frappe 原生的那些（树形、其它 App 配进来的除外，服务端不认）；
// 「等于 / 介于」走 p.filter_values 翻成各页参数，其余运算符走 p.filter_conditions 原样交给服务端。
// 别的列表、表单里的过滤器仍是官方全部条件。走真实 list.js，官方 Filter 用最小替身。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('apps/pearnly_core/pearnly_core/public/desk/list.js', 'utf8');

class NativeFilter {
  constructor(options) {
    Object.assign(this, options);
    this.set_conditions();
    this.set_conditions_from_config();
  }
  set_conditions() {
    this.conditions = [['=', 'Equals'], ['!=', 'Not Equals'], ['like', 'Like'], ['not like', 'Not Like'], ['in', 'In'],
      ['not in', 'Not In'], ['is', 'Is'], ['>', 'Greater Than'], ['<', 'Less Than'], ['>=', 'GE'], ['<=', 'LE'],
      ['Between', 'Between'], ['Timespan', 'Timespan'], ['descendants of', 'Descendants Of']];
  }
  set_conditions_from_config() { this.conditions.push(['fiscal year', 'Fiscal Year']); }
}
const frappe = { pearnly: {}, ui: { Filter: NativeFilter },
  meta: { get_docfield: (doctype, name) => ({ fieldname: name, fieldtype: 'Link' }) },
  views: { BaseList: class {}, ListView: { prototype: {} } } };
vm.runInNewContext(source, { frappe, window: {} });
const p = frappe.pearnly;

const product = new frappe.ui.Filter({ doctype: 'ERP Stock Movement', filter_list: { filter_fields: ['warehouse', 'product'] } });
assert.deepEqual(product.conditions.map(([condition]) => condition),
  ['=', '!=', 'like', 'not like', 'in', 'not in', 'is', '>', '<', '>=', '<=', 'Between', 'Timespan']);
assert.deepEqual(product.filter_fields.map(field => field.fieldname), ['warehouse', 'product']);

const other = new frappe.ui.Filter({ doctype: 'ToDo', filter_list: {} });
assert.equal(other.conditions.length, 15);

const list = { filter_area: { get: () => [
  ['Purchase Document', 'doc_kind', '=', 'purchase'],
  ['Purchase Document', 'doc_date', 'Between', ['2026-10-01', '2026-10-31']],
  ['Purchase Document', 'supplier', '!=', 'SUP-1'],
  ['Purchase Document', 'supplier', 'like', '%ABC%'],
  ['Purchase Document', 'payment_status', 'not in', ['paid']],
  ['Purchase Document', 'qty_on_hand', '>', 5],
] } };
assert.deepEqual(JSON.parse(JSON.stringify(p.filter_values(list))), { doc_kind: 'purchase', doc_date: ['2026-10-01', '2026-10-31'] });
assert.deepEqual(JSON.parse(p.filter_conditions(list, ['qty_on_hand'])), [
  ['supplier', '!=', 'SUP-1'], ['supplier', 'like', '%ABC%'], ['payment_status', 'not in', ['paid']]]);
assert.equal(p.filter_conditions({ filter_area: { get: () => [['X', 'doc_kind', '=', 'a']] } }), undefined);
console.log('desk list filter: ok');

// 列表还在加载 DocType（结果区没建好）时页面就要刷新：并进首次刷新，不取数、不抛错（库存卡页偶发 reading 'parent'，验收 10-25）
{
  let release;
  const loads = [];
  const page = { main: { addClass() {} }, wrapper: { on() {} }, page_form: { find: () => ({ hide() {} }) } };
  const listFrappe = { pearnly: {}, ui: {}, meta: {}, utils: { debounce: fn => fn },
    model: { with_doctype: () => new Promise(resolve => { release = resolve; }) }, get_meta: () => ({ fields: [] }),
    views: { BaseList: class { constructor(options) { Object.assign(this, options); } }, ListView: { prototype: {} } } };
  vm.runInNewContext(source, { frappe: listFrappe, window: {}, $: () => ({ on() {} }), __: value => value });
  const list = listFrappe.pearnly.list({ page, doctype: 'ERP Stock Movement', columns: [{ fieldname: 'product_name' }],
    load: async () => { loads.push(1); return { items: [], total: 0 }; } });
  let state = 'pending';
  list.reload().then(() => { state = 'resolved'; }, error => { state = 'rejected: ' + error.message; });
  setImmediate(() => {
    assert.equal(state, 'pending', 'waits for the first refresh instead of failing');
    assert.equal(loads.length, 0, 'no load before the result area exists');
    assert.equal(list.$result, undefined);
    console.log('desk list refresh before setup: ok');
  });
}
