// 产品页页头按钮白名单（core `desk/actions.js`）：自己的页面照常加并打标记；框架原生视图只放行声明过的，
// 没声明的（个人页的「权限」「创建用户电子邮件」「密码」……）根本不加。10-26 testcowork 个人页露出原生按钮后加的。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// 极简 jQuery：只要 actions.js 用到的 closest / attr / add / find
function $(input) {
  const items = Array.isArray(input) ? input : (input ? [input] : []);
  const api = {
    jquery: true, length: items.length, items,
    closest(selector) { return $(items.map(item => item.container).filter(Boolean)); },
    attr(name, value) {
      if (value === undefined) return items[0] && items[0].attrs ? items[0].attrs[name] : undefined;
      items.forEach(item => { item.attrs = item.attrs || {}; item.attrs[name] = value; }); return api;
    },
    add(other) { return $(items.concat(other.items)); },
    find() { return $(items.flatMap(item => item.children || [])); },
  };
  return api;
}
const calls = [];
class Page {
  constructor(route, owned) { this.wrapper = { container: { attrs: { 'data-page-route': route } } }; this.pearnly_owned = owned; }
  add_inner_button(label, action, group) { const el = { label, group, children: [{ tag: 'button' }] }; calls.push(['inner', label, group]); return $(el); }
  add_menu_item(label) { calls.push(['menu', label]); return $({ label }); }
  set_primary_action(label) { calls.push(['primary', label]); return $({ label }); }
  add_action_icon(icon) { calls.push(['icon', icon]); return $({ icon }); }
}
let route = ['Form', 'User', 'a@b.co'];
const frappe = { boot: { pearnly_product: 'cowork', pearnly_native_actions: { 'Form/User': ['Save'] } }, ui: { Page },
  get_route: () => route };
vm.runInNewContext(fs.readFileSync('apps/pearnly_core/pearnly_core/public/desk/actions.js', 'utf8'),
  { frappe, $, __: text => ({ Save: 'บันทึก' })[text] || text });

const profile = new Page('Form/User/a@b.co', undefined);
assert.equal(profile.set_primary_action('บันทึก').attr('data-pearnly-declared'), '1', 'Declared Save (translated) is added and marked');
assert.equal(profile.add_inner_button('Set User Permissions', () => {}, 'Permissions').length, 0, 'Native Permissions group is not added');
assert.equal(profile.add_inner_button('Create User Email', () => {}).length, 0);
assert.equal(profile.add_inner_button('Reset Password', () => {}, 'Password').length, 0);
assert.equal(profile.add_menu_item('Duplicate').length, 0);
assert.equal(profile.add_action_icon('printer').length, 0);
assert.deepEqual(calls, [['primary', 'บันทึก']], 'Undeclared native buttons never reach the native method');

route = ['query-report', 'Anything'];
const report = new Page('query-report/Anything', undefined);
assert.equal(report.add_menu_item('Export').length, 0, 'A view with no declaration gets nothing');

route = ['pearnly-sales'];
const own = new Page('pearnly-sales', true);
const button = own.add_inner_button('Whatever we add', () => {});
assert.equal(button.attr('data-pearnly-declared'), '1', 'Our own pages keep their buttons, marked for the smoke check');
assert.equal(button.find().attr('data-pearnly-declared'), '1');
console.log('PASS product page heads only show declared buttons; native profile buttons are never added');
