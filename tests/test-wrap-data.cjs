// 表单 / 弹窗布局里一行字的文字框写不下时换行、框往下长（用户 2026-10-07，全产品）：`product.js` 只把
// 布局里带标签、没有选项的 `Data` 换成会长高的多行框；工具栏、表格、单独嵌入的输入框、带选项的、管理后台照旧。
// 走真实 product.js，假的官方控件与 jQuery，不连站点。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname,
  '../apps/pearnly_core/pearnly_core/public/desk/product.js'), 'utf8');

function fake_node(tag) {
  const handlers = {};
  const node = {tagName: tag.toUpperCase(), value: '', attrs: {}, classes: new Set(), style: {}, offsetWidth: 0,
    selectionStart: 0, selectionEnd: 0, handlers,
    setRangeText(text, start, end) { this.value = this.value.slice(0, start) + text + this.value.slice(end); },
    dispatchEvent(event) { (handlers[event.type] || []).forEach(fn => fn(event)); }};
  const $node = {node, 0: node,
    addClass(name) { name.split(' ').forEach(c => node.classes.add(c)); return $node; },
    attr(name, value) { if (value !== undefined) node.attrs[name] = value; return $node; },
    removeAttr(name) { delete node.attrs[name]; return $node; },
    on(event, fn) { (handlers[event] ||= []).push(fn); return $node; },
    css() { return $node; }, get: () => node};
  return $node;
}

function load({product = true} = {}) {
  class NativeData {
    static html_element = 'input';
    constructor(opts) { Object.assign(this, opts); }
    make_input() { this.$input = fake_node(this.constructor.html_element); this.input = this.$input.node; }
    set_formatted_input(value) { this.input.value = value ?? ''; }
  }
  class NativeSmallText extends NativeData { static html_element = 'textarea'; }
  const frappe = {provide() {}, pearnly: {}, boot: {}, re_route: {}, model: {},
    realtime: {on() {}, off() {}}, ui: {Sidebar: class {}, form: {ControlData: NativeData, ControlSmallText: NativeSmallText}},
    breadcrumbs: {set_custom_breadcrumbs() {}}, datetime: {str_to_user: v => v},
    utils: {get_clipboard_data: event => event.clipboard}};
  const $ = target => ({on() {}, closest: selector =>
    ({length: target?.in_grid && /grid/.test(selector) ? 1 : 0})});
  vm.runInNewContext(source, {frappe, window: {addEventListener() {}}, $, __: k => k, locals: {}, Event: class {
    constructor(type) { this.type = type; } }, setTimeout, clearTimeout,
  document: {hidden: false, body: {classList: {add() {}}}, addEventListener() {}},
  localStorage: {getItem() { return null; }, setItem() {}, length: 0, key() {}}});
  // 产品外壳的初始化不在这里测：加载后再标成产品界面（控件是建的时候才看这一位）。
  frappe.boot.pearnly_product = product ? 'cowork' : null;
  return {form: frappe.ui.form, NativeData};
}

const made = (form, opts) => {
  const control = new form.ControlData({layout: {}, parent: {}, ...opts, df: {fieldtype: 'Data', label: 'Name', ...opts.df}});
  control.make_input();
  return control;
};

test('布局里带标签的普通文字字段换成会长高的多行框，仍一行字', () => {
  const {form} = load();
  const control = made(form, {});
  assert.equal(control.input.tagName, 'TEXTAREA');
  assert.ok(control.input.classes.has('pearnly-wrap-data'));
  assert.equal(control.input.attrs.rows, 1);
  control.set_formatted_input('บริษัท สมาร์ท ปริ้นท์ แฟบริค จำกัด');
  assert.equal(control.input.value, 'บริษัท สมาร์ท ปริ้นท์ แฟบริค จำกัด');
});

test('回车不换行（输入法确认字的回车照常），粘贴的换行变成空格', () => {
  const {form} = load();
  const control = made(form, {});
  const press = event => { let prevented = false;
    control.input.handlers.keydown.forEach(fn => fn({...event, preventDefault: () => { prevented = true; }}));
    return prevented; };
  assert.equal(press({key: 'Enter'}), true);
  assert.equal(press({key: 'Enter', isComposing: true}), false);
  assert.equal(press({key: 'a'}), false);
  let inputs = 0;
  control.input.handlers.input = [() => inputs++];
  control.input.handlers.paste.forEach(fn => fn({clipboard: 'CAKE : Red velvet\r\n ตัด 12', preventDefault() {}}));
  assert.equal(control.input.value, 'CAKE : Red velvet ตัด 12');
  assert.equal(inputs, 1);
});

test('工具栏、表格、单独嵌入、带选项、没标签、管理后台的照旧是单行框', () => {
  const {form} = load();
  for (const opts of [{only_input: true}, {layout: undefined}, {df: {input_class: 'input-xs'}},
    {df: {options: 'Email'}}, {df: {label: ''}}, {parent: {in_grid: true}}]) {
    assert.equal(made(form, opts).input.tagName, 'INPUT', JSON.stringify(opts));
  }
  assert.equal(made(load({product: false}).form, {}).input.tagName, 'INPUT');
});

test('别的控件继承官方 Data 时不受影响', () => {
  const {form} = load();
  class Phoneish extends form.ControlData {}
  const control = new Phoneish({layout: {}, parent: {}, df: {fieldtype: 'Data', label: 'Phone'}});
  control.make_input();
  assert.equal(control.input.tagName, 'INPUT');
});
