// 多级面包屑的窄屏下拉 `p.breadcrumbs`：首页 + 各级父页，同一个去处只列一次（首页的二级页「订阅与充值」原来列两个
// 「首页」，用户 2026-10-08）。走真实 product.js，假 frappe.breadcrumbs，不连站点。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname,
  '../apps/pearnly_core/pearnly_core/public/desk/product.js'), 'utf8');

function load(pages = ['pearnly-home']) {
  const added = [], routes = [];
  const frappe = {provide() {}, pearnly: {}, boot: {pearnly_pages: pages}, re_route: {}, model: {},
    realtime: {on() {}, off() {}}, ui: {Sidebar: class {}}, datetime: {str_to_user: v => v},
    breadcrumbs: {set_custom_breadcrumbs() {}, add: crumb => added.push(crumb)}, utils: {escape_html: v => v},
    set_route: route => routes.push(route), get_route_str: () => 'pearnly-intake/new'};
  vm.runInNewContext(source, {frappe, window: {addEventListener() {}}, document: {body: {classList: {add() {}}},
    addEventListener() {}}, $: () => ({on() {}}), __: k => (k === 'nav.home' ? '首页' : k), locals: {},
    setTimeout, clearTimeout, localStorage: {getItem() { return null; }, setItem() {}, length: 0, key() {}}});
  return {p: frappe.pearnly, added, routes};
}

test('首页的二级页：下拉只有一个「首页」', () => {
  const {p, added, routes} = load();
  p.breadcrumbs({label: '首页', route: p.subroute('pearnly-home'), trail: [{label: '订阅与充值'}]});
  const menu = added[0].menu_items;
  assert.deepEqual(Array.from(menu, item => item.label), ['首页']);
  menu[0].onClick();
  assert.deepEqual(routes, ['pearnly-home']);
});

test('其它页的二级页：首页、父页、更上一级父页各一项，点了去各自的地方', () => {
  const {p, added, routes} = load();
  p.breadcrumbs({label: '录入工作台', route: p.subroute('pearnly-intake'),
    trail: [{label: '自动录入规则', route: '/desk/pearnly-intake/folders'}, {label: '运行记录'}]});
  const menu = added[0].menu_items;
  assert.deepEqual(Array.from(menu, item => item.label), ['首页', '录入工作台', '自动录入规则']);
  menu.forEach(item => item.onClick());
  assert.deepEqual(routes, ['pearnly-home', '/desk/pearnly-intake', '/desk/pearnly-intake/folders']);
});

test('没有首页页面的产品不加首页；一级页没有下拉', () => {
  const {p, added} = load([]);
  p.breadcrumbs({label: '录入工作台', route: p.subroute('pearnly-intake'), trail: [{label: '上传'}]});
  assert.deepEqual(Array.from(added[0].menu_items, item => item.label), ['录入工作台']);
  p.breadcrumbs({label: '录入工作台', route: p.subroute('pearnly-intake')});
  assert.equal(added[1].menu_items.length, 0);
});
