// 选择框里的「新建 / 高级搜索」接到我们自己的页面：走真实 link.js，用假的 frappe 与 awesomplete。
// 断言三件事：官方那两项被换成我们的（值是 `pearnly_create__link_option`、动作进我们的页）、
// 没登记过的 doctype 一切照官方、以及仓库/收银台按当前公司联动的默认过滤。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(
  'apps/pearnly_core/pearnly_core/public/desk/link.js', 'utf8');

const LINKS = {'ERP Warehouse': 'pearnly-warehouses', 'ERP Supplier': 'pearnly-suppliers'};

// **忠实**的 awesomplete 桩：`list` 是**只写**属性（真库里 `set list(list) { this._list = list }`，
// 没有 getter）。先前就是读 `.list` 拿到 `undefined` 才整段失效 —— 桩必须保留这个形状，
// 否则这个坑下次还是测不出来。
function awesomeplete() {
  return {
    ul: { children: [] },
    set list(value) { this._list = value; },
    get current() { return this._list; },
  };
}

function boot({ links = LINKS, can_create = true, company = 'CO-00552' } = {}) {
  const routes = [];
  // 造出来的按钮：`attach_scan_btn` 只用到 `on`（点击时给个假事件）与被 append 进宿主。
  const made = [];
  const $ = html => {
    const node = {html, handlers: {},
      on(event, handler) { this.handlers[event] = handler; return this; },
      click() { this.handlers.click?.({preventDefault() {}, stopPropagation() {}}); },
    };
    made.push(node);
    return node;
  };
  class NativeLink {
    constructor(df) { this.df = df || {}; }
    get_options() { return this.df.options; }
    get_input_value() { return this.input_value || ""; }
    toggle_href() {}
    make_input() {}
    show_link_and_clear_buttons() {}
    new_doc() { this.native_new_doc = true; return 'native'; }
    open_advanced_search() { this.native_advanced = true; return 'native'; }
  }
  const frappe = {
    boot: { pearnly_links: links, pearnly_company: company },
    ui: { form: { ControlLink: NativeLink } },
    utils: { icon: () => '<svg class="icon"></svg>' },
    model: { can_create: () => can_create },
    set_route: (...parts) => routes.push(parts.join('/')),
  };
  frappe.pearnly = {};
  // 框架的 `__()` 会做 `{0}` 插值（真实实现如此），桩要跟着做，否则断言看到的是模板。
  const translate = (text, args) => String(text)
    .replace(/\{(\d+)\}/g, (whole, index) => (args && args[index] !== undefined ? args[index] : whole));
  vm.runInNewContext(source, { frappe, window: {}, __: translate, console, $ });
  return { ControlLink: frappe.ui.form.ControlLink, p: frappe.pearnly, routes, made };
}

function link_with(booted, df, values) {
  const control = new booted.ControlLink(df);
  control.awesomplete = awesomeplete();
  control.awesomplete._list = values;
  return control;
}

const native_two = () => ([
  {value: 'WH-00003', label: 'WH-00003'},
  {value: 'create_new__link_option', label: 'Create a new ERP Warehouse', action() {}},
  {value: 'advanced_search__link_option', label: 'Advanced Search', action() {}},
]);

const values_of = control => control.awesomplete._list.map(item => item.value);
// 共享层在 vm 那个 realm 里造对象：直接 deepEqual 会比原型，先摊平成本地对象。
const plain = value => JSON.parse(JSON.stringify(value));

// 箭头那颗按钮（官方的 "Open Link"）：只有 attr/off/on，点一下就把事件派给挂上的处理函数。
function fakeLinkButton() {
  return {
    href: null, handlers: {},
    hidden: false,
    attr(key, value) { if (key === 'href') this.href = value; return this; },
    off() { this.handlers = {}; return this; },
    on(event, handler) { this.handlers[event] = handler; return this; },
    hide() { this.hidden = true; return this; },
    click() {
      let prevented = false;
      this.handlers['click.pearnly']?.({preventDefault: () => { prevented = true; }});
      return prevented;
    },
  };
}

(async () => {
  // 1) 声明读得回来；没登记的 doctype 没有目标
  const b = boot();
  assert.equal(b.p.link_target('ERP Warehouse'), 'pearnly-warehouses');
  assert.equal(b.p.link_target('ERP Purchase Category'), '');

  // 2) 官方那两项被换成我们的那一条（读的是 `_list`，写走 `list`）
  const warehouse = link_with(b, {options: 'ERP Warehouse', fieldname: 'warehouse'}, native_two());
  warehouse.pearnly_link_options('ERP Warehouse');
  assert.deepEqual(values_of(warehouse), ['WH-00003', 'pearnly_create__link_option']);
  const ours = warehouse.awesomplete._list.at(-1);
  assert.equal(ours.label, 'Create a new ERP Warehouse', '文案与图标照官方那一条写');
  assert.ok(ours.html.includes('pearnly-link-option'), '挂上我们的类，皮肤按它上色');

  // 3) 走我们的页面：动作点下去 = 跳我们那一页，绝不落到官方表单
  ours.action();
  assert.deepEqual(b.routes, ['pearnly-warehouses']);
  assert.deepEqual(plain(b.p.pending_create), {route: 'pearnly-warehouses', doctype: 'ERP Warehouse',
    at: b.p.pending_create.at});

  // 4) 官方那两项的兜底：入口要是还在（字段没设 `only_select`），动作也落在我们页面上
  const native_call = link_with(b, {options: 'ERP Warehouse'}, []);
  assert.equal(native_call.new_doc(), false);
  assert.ok(!native_call.native_new_doc, '不执行父类的官方新建');
  assert.equal(native_call.open_advanced_search(), false);
  assert.ok(!native_call.native_advanced, '不打开官方选择器');
  assert.deepEqual(b.routes.slice(1), ['pearnly-warehouses', 'pearnly-warehouses']);

  // 5) 没登记的 doctype：一个字都不改（没目标就原样交给官方）
  const unmapped = link_with(b, {options: 'ERP Purchase Category'}, native_two());
  unmapped.pearnly_link_options('ERP Purchase Category');
  assert.deepEqual(values_of(unmapped), native_two().map(item => item.value));
  assert.equal(unmapped.new_doc(), 'native');
  assert.ok(unmapped.native_new_doc);

  // 6) 没有新建权限就不给入口（官方那两项也一并去掉，用户不会被带到别处）
  const no_perm = link_with(boot({can_create: false}), {options: 'ERP Supplier'}, native_two());
  no_perm.pearnly_link_options('ERP Supplier');
  assert.deepEqual(values_of(no_perm), ['WH-00003']);

  // 7) 明确不要入口的格子（收银台的客户搜索框）：连官方那两项都去掉
  const opted_out = link_with(b, {options: 'ERP Supplier', pearnly_no_create: 1}, native_two());
  opted_out.pearnly_link_options('ERP Supplier');
  assert.deepEqual(values_of(opted_out), ['WH-00003']);

  // 8) 仓库 / 收银台按当前公司联动；调用方自己写的过滤不覆盖；别的 doctype 不管
  const auto = new b.ControlLink({options: 'ERP Warehouse', fieldname: 'warehouse'});
  auto.make_input();
  assert.deepEqual(plain(auto.df.get_query), {filters: {company: 'CO-00552'}});
  const terminal = new b.ControlLink({options: 'ERP POS Terminal'});
  terminal.make_input();
  assert.deepEqual(plain(terminal.df.get_query), {filters: {company: 'CO-00552'}});
  const own_query = {filters: {is_active: 1}};
  const explicit = new b.ControlLink({options: 'ERP Warehouse', get_query: own_query});
  explicit.make_input();
  assert.equal(explicit.df.get_query, own_query, '调用方写了就用它的');
  const other = new b.ControlLink({options: 'ERP Product'});
  other.make_input();
  assert.equal(other.df.get_query, undefined, '这条联动只管仓库/收银台');

  // 9) 目标页接手：对得上才开自己的新建弹窗，取走即清；对不上/过期都不开
  const opened = [];
  b.p.pending_create = {route: 'pearnly-warehouses', doctype: 'ERP Warehouse', at: Date.now()};
  assert.equal(await b.p.take_create('pearnly-suppliers', 'ERP Supplier', () => opened.push('x')), false);
  assert.equal(await b.p.take_create('pearnly-warehouses', 'ERP Warehouse', () => opened.push('y')), true);
  assert.deepEqual(opened, ['y']);
  assert.equal(b.p.pending_create, null, '取走即清');
  assert.equal(await b.p.take_create('pearnly-warehouses', 'ERP Warehouse', () => opened.push('z')), false);
  b.p.pending_create = {route: 'pearnly-warehouses', doctype: 'ERP Warehouse', at: 0};
  assert.equal(await b.p.take_create('pearnly-warehouses', 'ERP Warehouse', () => opened.push('o')), false);
  assert.deepEqual(opened, ['y'], '过期的那次不弹');

  // 10) 箭头（Open Link）：官方写的是官方表单地址，我们改成自己那一页 + 打开这条记录
  const arrow_env = boot();
  const arrow = new arrow_env.ControlLink({options: 'ERP Warehouse'});
  arrow.$input = {closest: () => null};
  arrow.$link_open = fakeLinkButton();
  arrow.input_value = 'WH-00118';
  arrow.show_link_and_clear_buttons();
  assert.equal(arrow.$link_open.href, '/desk/pearnly-warehouses', '箭头指向我们那一页，不是官方表单');
  assert.ok(arrow.$link_open.click(), '拦掉官方的默认跳转');
  assert.deepEqual(arrow_env.routes, ['pearnly-warehouses']);
  assert.deepEqual(plain(arrow_env.p.pending_open),
    {route: 'pearnly-warehouses', doctype: 'ERP Warehouse', name: 'WH-00118',
      at: arrow_env.p.pending_open.at});

  // 目标页接手：对得上才开这条记录（取走即清），对不上不开
  const opened_records = [];
  assert.equal(await arrow_env.p.take_open('pearnly-suppliers', 'ERP Supplier',
    name => opened_records.push(name)), false);
  assert.equal(await arrow_env.p.take_open('pearnly-warehouses', 'ERP Warehouse',
    name => opened_records.push(name)), true);
  assert.deepEqual(opened_records, ['WH-00118']);
  assert.equal(arrow_env.p.pending_open, null);

  // 没登记的 doctype：箭头照官方（我们一个字都不写）
  const plain_arrow = new arrow_env.ControlLink({options: 'ERP Purchase Category'});
  plain_arrow.$input = {closest: () => null};
  plain_arrow.$link_open = fakeLinkButton();
  plain_arrow.input_value = 'CAT-1';
  plain_arrow.show_link_and_clear_buttons();
  assert.equal(plain_arrow.$link_open.href, null);

  // 明确"不要跳走"的格子：连官方那颗箭头也藏掉（收银台的客户搜索框，跳走会丢单）
  const stayed = new arrow_env.ControlLink({options: 'ERP Customer', pearnly_no_create: 1});
  stayed.$input = {closest: () => null};
  stayed.$link_open = fakeLinkButton();
  stayed.input_value = 'CUS-00483';
  stayed.show_link_and_clear_buttons();
  assert.equal(stayed.$link_open.hidden, true, '箭头藏起来');
  assert.equal(stayed.$link_open.href, null);
  assert.deepEqual(arrow_env.routes, ['pearnly-warehouses'], '没有再跳一次');

  // 11) 跳走前把发起跳转的那张弹窗收起来（框架的弹窗不跟路由消失）
  const hidden = [];
  const modal = {length: 1, modal(action) { hidden.push(action); }};
  const from = {closest: selector => (selector === '.modal' ? modal : null)};
  arrow_env.p.dismiss_modal_of(from);
  assert.deepEqual(hidden, ['hide']);
  arrow_env.p.dismiss_modal_of({closest: () => null});   // 不在弹窗里：什么都不做
  assert.deepEqual(hidden, ['hide']);

  // 12) 扫码按钮：只有我们声明要扫的那类选择框有；按下走产品给的扫码动作；不重复挂；
  //     筛选弹层里、只读的格子里都不放。
  const scan_env = boot();
  const scanned_into = [];
  scan_env.p.scan_doctypes = ['ERP Product'];
  scan_env.p.scan_link = async control => { scanned_into.push(control.df.fieldname); };
  // 每个格子一个自家宿主（表单里是 `.control-input`，网格单元里退到输入框的父元素）。
  const host = () => ({length: 1, classes: [], children: [],
    addClass(name) { this.classes.push(name); return this; },
    append(child) { this.children.push(child); return this; }});
  const place = (control, {filter = false, holder = host()} = {}) => {
    control.$input = {closest: selector =>
      (selector === '.filter-area' ? {length: filter ? 1 : 0} : holder)};
    return holder;
  };

  const product = new scan_env.ControlLink({options: 'ERP Product', fieldname: 'product'});
  const product_host = place(product);
  product.make_input();
  assert.deepEqual(product_host.classes, ['pearnly-link-scan-input'], '宿主拿到皮肤的类');
  assert.equal(product_host.children.length, 1, '按钮挂上了');
  assert.ok(product_host.children[0].html.includes('pearnly-link-scan'), '用皮肤里那颗按钮');
  product_host.children[0].click();
  assert.deepEqual(scanned_into, ['product'], '按下就走产品给的扫码动作');
  product.make_input();
  assert.equal(product_host.children.length, 1, '再进来一次不会挂第二颗');

  const not_ours = new scan_env.ControlLink({options: 'ERP Warehouse', fieldname: 'warehouse'});
  const other_host = place(not_ours);
  not_ours.make_input();
  assert.equal(other_host.children.length, 0, '没声明要扫的那类，一个像素都不动');

  const no_product = boot();
  no_product.p.scan_doctypes = null;               // 产品没声明：共用层什么都不做
  const bare = new no_product.ControlLink({options: 'ERP Product', fieldname: 'product'});
  const bare_host = place(bare);
  bare.make_input();
  assert.equal(bare_host.children.length, 0);

  const in_filter = new scan_env.ControlLink({options: 'ERP Product', fieldname: 'product'});
  const filter_host = place(in_filter, {filter: true});
  in_filter.make_input();
  assert.equal(filter_host.children.length, 0, '筛选弹层里那颗不是"选物料"');

  const readonly = new scan_env.ControlLink({options: 'ERP Product', fieldname: 'product', read_only: 1});
  const readonly_host = place(readonly);
  readonly.make_input();
  assert.equal(readonly_host.children.length, 0, '只读的格子（看一张已提交的单）不留扫码');

  console.log('test-link-options: 全部通过');
})();
