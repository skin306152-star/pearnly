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

// 筛选下拉与条件标签用格子同一套词（11-28：收款状态下拉显示 queued / failed 原码）：页面给 labels → 选项换成官方的
// [{value: 原码, label: 状态词}]、条件标签走同一个词；没给 labels 的字段原样。只换显示，交给服务端的条件不变。
{
  const words = { queued: '录入中', failed: '失败', written: '成功' };
  const label = value => words[value] || '';
  const metaFrappe = { pearnly: {}, ui: { Filter: NativeFilter }, utils: { escape_html: value => String(value) },
    meta: { get_docfield: (doctype, name) => ({ fieldname: name, fieldtype: name === 'doc_date' ? 'Date' : 'Select',
      options: name === 'status' ? 'queued\nwriting\nwritten\nfailed' : 'line\ncc' }) },
    views: { BaseList: class {}, ListView: { prototype: {} } } };
  vm.runInNewContext(source, { frappe: metaFrappe, window: {} });
  metaFrappe.pearnly.escape = value => String(value ?? '');
  const filter = new metaFrappe.ui.Filter({ doctype: 'ERP Collection',
    filter_list: { filter_fields: ['status', 'source', 'doc_date'], filter_labels: { status: label } } });
  const [status, source_, date] = filter.filter_fields;
  assert.deepEqual(JSON.parse(JSON.stringify(status.options)), [{ value: '', label: '' }, { value: 'queued', label: '录入中' },
    { value: 'writing', label: 'writing' }, { value: 'written', label: '成功' }, { value: 'failed', label: '失败' }]);
  assert.equal(status.formatter('failed'), '失败');
  assert.equal(status.formatter(''), '');
  assert.equal(source_.options, 'line\ncc');  // 没给 labels：原样（官方按 __(原码) 显示）
  assert.equal(source_.formatter, undefined);
  assert.equal(date.fieldtype, 'Date');
  // 元数据里的字段没被改（克隆了一份）
  assert.equal(metaFrappe.meta.get_docfield('ERP Collection', 'status').options, 'queued\nwriting\nwritten\nfailed');
  // 同一筛选值，有没有 labels 交给服务端的都一样：值是原码
  const rows = [['ERP Collection', 'status', '=', 'failed'], ['ERP Collection', 'status', 'in', ['queued', 'failed']],
    ['ERP Collection', 'source', '!=', 'cc']];
  const sent = list_ => [JSON.stringify(metaFrappe.pearnly.filter_values(list_)), metaFrappe.pearnly.filter_conditions(list_)];
  assert.deepEqual(sent({ filter_area: { get: () => rows }, filter_labels: { status: label } }),
    sent({ filter_area: { get: () => rows } }));
  assert.deepEqual(JSON.parse(sent({ filter_area: { get: () => rows } })[0]), { status: 'failed' });
  console.log('desk list filter labels: ok');

  // 每个加了 labels 的列表：DocType 里的每一档，筛选「等于 / 在…之中」交给服务端的都是同一个原码，与不加 labels 时相同
  const pages = [
    ['apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/collections.js', 'erp_collection', ['status', 'source']],
    ['apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/sales.js', 'erp_sales_document', ['doc_kind', 'payment_status', 'source']],
    ['apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/purchase.js', 'purchase_document', ['doc_kind', 'payment_status', 'status']],
    ['apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/pos-receipts.js', 'erp_sale', ['sale_type', 'status']],
  ];
  for (const [page, doctype, fields] of pages) {
    const js = fs.readFileSync(page, 'utf8');
    const [json] = require('node:child_process').execSync(`git ls-files 'apps/pearnly_erp_portal/**/doctype/${doctype}/${doctype}.json'`)
      .toString().trim().split('\n');
    const meta = JSON.parse(fs.readFileSync(json, 'utf8'));
    for (const fieldname of fields) {
      assert.match(js, new RegExp(`fieldname: "${fieldname}"[^}]*fieldtype: "Select", labels: `), `${page} ${fieldname} declares labels`);
      const options = meta.fields.find(df => df.fieldname === fieldname).options.split('\n').filter(Boolean);
      for (const value of options) {
        for (const operator of ['=', 'in']) {
          const row = [[meta.name, fieldname, operator, operator === 'in' ? [value] : value]];
          const plain = sent({ filter_area: { get: () => row } });
          assert.deepEqual(sent({ filter_area: { get: () => row }, filter_labels: { [fieldname]: () => 'X' } }), plain);
          assert.ok((plain[0] + (plain[1] || '')).includes(JSON.stringify(value)), `${fieldname}=${value} goes as the code`);
        }
      }
    }
  }
  console.log('desk list filter labels per page: ok');
}

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
