// 左下角头像（`p.user_menu`）进出账号区：进去落账号区第一项、再点回到进去前那一页（含列表筛选）、账号区里切换不丢原页、
// 从账号区点业务菜单原页作废、直接打开账号页再点回首页（用户 11-13，评估 docs/work/2026-11-23-user-menu-return-评估.md）。
// 走真实 product.js，假 frappe 路由与侧栏，不连站点。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname,
  '../apps/pearnly_core/pearnly_core/public/desk/product.js'), 'utf8');

// 极简 jQuery 替身：只够 `p.user_menu` 用的几个方法
function node(extra = {}) {
  const self = {length: 1, attrs: {}, classes: new Set(), dataset: {}, handlers: {}, children: [],
    find: () => self, on(event, fn) { self.handlers[event] = fn; return self; }, removeAttr() { return self; },
    data(key, value) { if (value === undefined) return self.dataset[key]; self.dataset[key] = value; return self; },
    addClass(c) { self.classes.add(c); return self; }, removeClass(c) { self.classes.delete(c); return self; },
    empty() { self.children = []; return self; }, append(items) { self.children.push(...items); return self; },
    ...extra};
  return self;
}

function load({menu = [{route: 'pearnly-company', label: 'Company', before_profile: true}, {route: 'pearnly-team', label: 'Team'}],
  start = '/desk/pearnly-inventory', search = ''} = {}) {
  const location = {pathname: start, search};
  const changes = [], visits = [];
  const route_of = pathname => pathname.replace(/^\/desk\/?/, '').split('/').filter(Boolean);
  const go = (pathname, query = '') => {
    location.pathname = pathname; location.search = query;
    visits.push(pathname + query);
    changes.forEach(fn => fn());
  };
  const frappe = {provide() {}, pearnly: {}, re_route: {}, model: {}, route_options: null,
    boot: {pearnly_pages: ['pearnly-home'], pearnly_user_menu: menu},
    session: {user: 'u@example.com'}, realtime: {on() {}, off() {}}, datetime: {str_to_user: v => v},
    utils: {escape_html: v => v}, breadcrumbs: {set_custom_breadcrumbs() {}, add() {}},
    get_route: () => route_of(location.pathname),
    router: {on: (event, fn) => changes.push(fn)},
    render_template: () => '<div/>',
    ui: {Sidebar: class {}, toolbar: {route_to_user: () => go('/desk/user/u@example.com')}},
    set_route(target) {
      // 同框架：路径 + `route_options` 拼成查询参数，然后清掉
      const options = frappe.route_options;
      frappe.route_options = null;
      const query = options ? '?' + new URLSearchParams(options).toString() : '';
      go(target.startsWith('/') ? target : '/desk/' + target, query);
    }};
  // `Form/User/<本人>`：框架 get_route 的样子
  const plain = frappe.get_route;
  frappe.get_route = () => location.pathname.startsWith('/desk/user/')
    ? ['Form', 'User', 'u@example.com'] : plain();
  const button = node(), items = node(), cards = node();
  items.find = selector => (selector === '.item-anchor'
    ? {each: fn => items.children.forEach((child, index) => fn(index, child))} : items);
  const sidebar = {$sidebar: {find: selector => (selector === '.sidebar-user-button' ? button : cards)},
    $items_container: items, made: 0, make_sidebar() { sidebar.made += 1; }};
  const $ = x => (x && x.handlers ? x : node());
  vm.runInNewContext(source, {frappe, window: {addEventListener() {}, location}, location,
    document: {body: {classList: {add() {}}}, addEventListener() {}}, $, __: k => k, locals: {},
    URLSearchParams, setTimeout, clearTimeout,
    localStorage: {getItem() { return null; }, setItem() {}, length: 0, key() {}}});
  frappe.pearnly.user_menu(sidebar);
  const click = () => button.handlers.click({preventDefault() {}});
  const pick = index => items.children[index].handlers.click({preventDefault() {}});
  return {location, visits, click, pick, sidebar, go, frappe};
}

test('点左下角进账号区落第一项（ERP 是公司），再点回到进去前那一页、带着筛选', () => {
  const t = load({search: '?warehouse=WH-1&status=low'});
  t.click();
  assert.equal(t.location.pathname, '/desk/pearnly-company');
  assert.equal(t.sidebar.pearnly_user_menu.open, true);
  t.click();
  assert.equal(t.location.pathname, '/desk/pearnly-inventory');
  assert.deepEqual(Object.fromEntries(new URLSearchParams(t.location.search)), {warehouse: 'WH-1', status: 'low'});
  assert.equal(t.sidebar.pearnly_user_menu.open, false);
  assert.ok(t.sidebar.made >= 1, '业务菜单要画回来');
});

test('没有公司的产品（Cowork / DMS）落第一项：个人信息', () => {
  const t = load({menu: [{route: 'pearnly-team', label: 'Team'}]});
  t.click();
  assert.equal(t.location.pathname, '/desk/user/u@example.com');
  t.click();
  assert.equal(t.location.pathname, '/desk/pearnly-inventory');
});

test('账号区里切换公司 / 个人信息 / 团队管理，原页不丢', () => {
  const t = load({search: '?q=abc'});
  t.click();
  t.pick(1);  // 个人信息
  assert.equal(t.location.pathname, '/desk/user/u@example.com');
  t.pick(2);  // 团队管理
  assert.equal(t.location.pathname, '/desk/pearnly-team');
  assert.equal(t.sidebar.pearnly_user_menu.open, true);
  t.click();
  assert.equal(t.location.pathname + t.location.search, '/desk/pearnly-inventory?q=abc');
});

test('从账号区点业务菜单：照常进那一页，原页作废，再点左下角从这一页重新记', () => {
  const t = load();
  t.click();
  t.go('/desk/pearnly-sales');  // 业务菜单 / 浏览器后退一样走路由
  assert.equal(t.sidebar.pearnly_user_menu.open, false);
  assert.equal(t.sidebar.pearnly_user_menu.back, null);
  t.click();
  t.click();
  assert.equal(t.location.pathname, '/desk/pearnly-sales');
});

test('直接打开账号页（没有进去前那一页）再点左下角：回首页', () => {
  const t = load({start: '/desk/pearnly-team'});
  assert.equal(t.sidebar.pearnly_user_menu.open, true);  // 一打开就在账号区
  t.click();
  assert.equal(t.location.pathname, '/desk');
  assert.equal(t.sidebar.pearnly_user_menu.open, false);
});
