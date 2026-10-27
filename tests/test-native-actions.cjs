// 产品站点的路由、页头入口与全局快捷键白名单（core `desk/actions.js`，声明在各产品 `pearnly_navigation` 的 routes / actions / shortcuts）：
// 我们自己的页面、本人个人页、声明过的框架视图照常渲染，其余框架路由不画、换成首页（10-27 验收 17 岗位漏出清单后加）；
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
const rendered = [];
const routed = [];
const parsed = [];
const router = { current_route: [], sub_path: '', render() { rendered.push(this.current_route.join('/')); },
  route() { parsed.push(this.sub_path); }, get_sub_path() { return this.sub_path; },
  slug: name => name.toLowerCase().replace(/ /g, '-') };
const frappe = { boot: { pearnly_product: 'cowork', pearnly_page_actions: { 'Form/User': ['Save'], '*': ['selection'] },
  setup_complete: 1, pearnly_shortcuts: ['ctrl+s'], pearnly_routes: ['query-report/Stock Variance'], pearnly_landing_url: '/desk/pearnly-intake',
    pearnly_product_pages: ['pearnly-sales', 'pearnly-intake'] },
  session: { user: 'a@b.co' }, route_flags: {}, router, set_route: target => routed.push([target, frappe.route_flags.replace_route]),
  ui: { Page, keys }, get_route: () => route };
const window = { location: { pathname: '/desk/contact' } };
vm.runInNewContext(fs.readFileSync('apps/pearnly_core/pearnly_core/public/desk/actions.js', 'utf8'),
  { frappe, $, window, __: text => ({ Save: 'บันทึก' })[text] || text });

// 路由第一道：原始地址第一段不放行的连解析都不做（解析会先拉 DocType 定义，没权限的在那里就抛错、走不到渲染）
const enter = path => { router.sub_path = path; frappe.route_flags = {}; router.route(); };
for (const path of ['pearnly-sales/SD-1', 'user/a@b.co', 'query-report/Stock Variance', '']) enter(path);
assert.deepEqual(parsed, ['pearnly-sales/SD-1', 'user/a@b.co', 'query-report/Stock Variance', ''], 'Allowed heads go on to parsing');
for (const path of ['address', 'list-filter/x', 'contact', 'home', 'cowork-push-record', 'app/todo', 'pearnly-ocr-work', 'pearnly-cashier']) enter(path);
assert.equal(parsed.length, 4, 'Other heads never reach the parser, framework DocTypes named pearnly-* and undeclared shared pages included');
assert.deepEqual(routed.at(-1), ['pearnly-intake', true]);
routed.length = 0;

// 第二道：解析成标准路由后渲染前按前两段精确判，不放行的换成首页（替换历史）
const visit = (...current) => { router.current_route = current; frappe.route_flags = {}; router.render(); };
visit('pearnly-sales', 'SD-1');
visit('Form', 'User', 'a@b.co');
visit('query-report', 'Stock Variance');
visit('');
assert.deepEqual(rendered, ['pearnly-sales/SD-1', 'Form/User/a@b.co', 'query-report/Stock Variance', ''],
  'Our pages, the own profile, declared framework views and home render');
for (const blocked of [['List', 'Contact', 'List'], ['Form', 'User', 'other@b.co'], ['List', 'User', 'List'], ['Workspaces', 'Home'],
  ['List', 'Cowork Push Record', 'List'], ['query-report', 'General Ledger'], ['dashboard-view', 'X'], ['Form', 'Module Def', 'Core'],
  ['List', 'Pearnly OCR Work', 'List'], ['pearnly-cashier']])
  visit(...blocked);
assert.equal(rendered.length, 4, 'Undeclared framework routes never render');
assert.deepEqual(routed.at(-1), ['pearnly-intake', true], 'They are replaced by the product landing page');
assert.equal(routed.length, 10);
frappe.boot.pearnly_profile_allowed = false;
visit('Form', 'User', 'a@b.co');
assert.equal(rendered.length, 4, 'The own profile closes when the product does not grant it');
window.location.pathname = '/desk/pearnly-intake';
visit('List', 'Contact', 'List');
assert.equal(rendered.length, 5, 'Already on the landing page: render instead of looping');
window.location.pathname = '/desk/contact';
frappe.session.user = 'Administrator';
visit('List', 'Contact', 'List');
assert.equal(rendered.length, 6, 'Administrator keeps the framework desk');
frappe.session.user = 'a@b.co';
// 初始设置没完成：框架要跳设置向导，外壳整个放行（不然两边来回跳到栈溢出）
frappe.boot.setup_complete = 0;
const before = [parsed.length, rendered.length, routed.length];
enter('setup-wizard/0');
visit('setup-wizard', '0');
assert.deepEqual([parsed.length, rendered.length, routed.length], [before[0] + 1, before[1] + 1, before[2]],
  'With setup unfinished the setup wizard is parsed and rendered, nothing is replaced');
frappe.boot.setup_complete = 1;
enter('setup-wizard/0');
assert.equal(parsed.length, before[0] + 1, 'Once setup is complete the wizard route is replaced again');

const profile = new Page('Form/User/a@b.co', undefined);
const save = profile.set_primary_action('บันทึก');
assert.equal(save.attr('data-pearnly-declared'), '1', 'Declared Save (translated) is added and marked');
assert.equal(save.attr('data-pearnly-action'), 'Save', 'The matched declaration code is stamped for the page snapshots');
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
assert.equal(button.attr('data-pearnly-action'), undefined, 'An own button whose label matches no code keeps only the declared mark');
assert.equal(report.add_menu_item('selection').attr('data-pearnly-declared'), '1', 'Entries declared under "*" are allowed on every page');

// 全局快捷键：加载时已注册的没声明的注销（全局搜索、控制台），Ctrl+S 留着；之后注册的同样过白名单
assert.deepEqual(Object.keys(handlers).sort(), ['ctrl+s']);
assert.deepEqual(keys.standard_shortcuts.map(item => item.shortcut), ['ctrl+s']);
assert.equal(keys.add_shortcut({ shortcut: 'shift+ctrl+g', description: 'theme' }), undefined, 'Theme switch is not registered');
assert.equal(keys.add_shortcut({ shortcut: 'ctrl+i', description: 'POS item', page: own }), 'added', 'Shortcuts on our own pages stay');
assert.equal(keys.add_shortcut({ shortcut: 'ctrl+p', description: 'print', page: profile }), undefined, 'Native form shortcuts are dropped');
console.log('PASS product sites only open declared routes; page heads only show declared entries; undeclared native buttons and global shortcuts are never added');
