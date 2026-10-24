// 产品列表的官方筛选（list.js 里的 frappe.ui.Filter 子类）：声明了本页筛选字段的列表，条件下拉只列服务端认的
// 「等于」与「介于」；别的列表、表单里的过滤器仍是官方全部条件。走真实 list.js，官方 Filter 用最小替身。
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
    this.conditions = [['=', 'Equals'], ['!=', 'Not Equals'], ['like', 'Like'], ['in', 'In'], ['>', 'Greater Than'],
      ['Between', 'Between'], ['Timespan', 'Timespan'], ['descendants of', 'Descendants Of']];
  }
  set_conditions_from_config() { this.conditions.push(['fiscal year', 'Fiscal Year']); }
}
const frappe = { pearnly: {}, ui: { Filter: NativeFilter },
  meta: { get_docfield: (doctype, name) => ({ fieldname: name, fieldtype: 'Link' }) },
  views: { BaseList: class {}, ListView: { prototype: {} } } };
vm.runInNewContext(source, { frappe, window: {} });

const product = new frappe.ui.Filter({ doctype: 'ERP Stock Movement', filter_list: { filter_fields: ['warehouse', 'product'] } });
assert.deepEqual(product.conditions.map(([condition]) => condition), ['=', 'Between']);
assert.deepEqual(product.filter_fields.map(field => field.fieldname), ['warehouse', 'product']);

const other = new frappe.ui.Filter({ doctype: 'ToDo', filter_list: {} });
assert.equal(other.conditions.length, 9);
assert.ok(other.conditions.some(([condition]) => condition === 'fiscal year'));
console.log('desk list filter: ok');
