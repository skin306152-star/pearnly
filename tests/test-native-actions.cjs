// 产品页页头入口与全局快捷键白名单（core `desk/actions.js`，声明在各产品 `pearnly_navigation` 的 actions / shortcuts）：
// 自己的页面照常加并打标记；框架原生视图只放行声明过的，没声明的（个人页的「权限」「创建用户电子邮件」「密码」……）根本不加；
// 全局快捷键只留声明过的和挂在我们自己页面上的。10-26 testcowork 个人页露出原生按钮后加的。
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
const handlers = { 'ctrl+s': ['save'], 'ctrl+g': ['global search'], 'shift+t': ['console'] };
const keys = {
  standard_shortcuts: [{ shortcut: 'ctrl+s' }, { shortcut: 'ctrl+g' }, { shortcut: 'shift+t' }],
  off(key) { delete handlers[key]; },
  add_shortcut(options) { (handlers[options.shortcut] = handlers[options.shortcut] || []).push(options.description); return 'added'; },
};
const frappe = { boot: { pearnly_product: 'cowork', pearnly_page_actions: { 'Form/User': ['Save'], '*': ['selection'] },
  pearnly_shortcuts: ['ctrl+s'] }, ui: { Page, keys }, get_route: () => route };
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
assert.equal(report.add_menu_item('selection').attr('data-pearnly-declared'), '1', 'Entries declared under "*" are allowed on every page');

// 全局快捷键：加载时已注册的没声明的注销（全局搜索、控制台），Ctrl+S 留着；之后注册的同样过白名单
assert.deepEqual(Object.keys(handlers).sort(), ['ctrl+s']);
assert.deepEqual(keys.standard_shortcuts.map(item => item.shortcut), ['ctrl+s']);
assert.equal(keys.add_shortcut({ shortcut: 'shift+ctrl+g', description: 'theme' }), undefined, 'Theme switch is not registered');
assert.equal(keys.add_shortcut({ shortcut: 'ctrl+i', description: 'POS item', page: own }), 'added', 'Shortcuts on our own pages stay');
assert.equal(keys.add_shortcut({ shortcut: 'ctrl+p', description: 'print', page: profile }), undefined, 'Native form shortcuts are dropped');
console.log('PASS product page heads only show declared entries; undeclared native buttons and global shortcuts are never added');
