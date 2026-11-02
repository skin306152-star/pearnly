// 字段说明收进标题旁的说明图标（用户 2026-11-02，ERP / Cowork）：`field-help.js` 扩展框架 `ControlInput.set_description`，
// 说明放进标题旁的 `.help` 插槽（框架 info 图标 + Bootstrap tooltip，aria-label 是说明原文），字段下方不再写；
// 登记的例外 `description_inline` 照原生留在下方；没声明的产品（DMS）不动。走真实 field-help.js，假的官方控件与 jQuery，不连站点。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname,
  '../apps/pearnly_core/pearnly_core/public/desk/field-help.js'), 'utf8');

const node = (classes = '') => ({classes: new Set(classes.split(' ').filter(Boolean)), children: [], attrs: {}, data: {}, calls: []});

function load({declared = 'icon', touch = false} = {}) {
  const document = {handlers: [], createElement() {
    return {set innerHTML(html) { this.text = html.replace(/<[^>]+>/g, ' '); }, get textContent() { return this.text; }};
  }};
  const wrap = list => {
    const $list = {length: list.length, 0: list[0], list,
      find(selector) {
        const name = selector.slice(1), out = [];
        const walk = item => item.children.forEach(child => { if (child.classes.has(name)) out.push(child); walk(child); });
        list.forEach(walk);
        return wrap(out);
      },
      first: () => wrap(list.slice(0, 1)),
      children: selector => wrap(list.flatMap(item => item.children.filter(child => child.classes.has(selector.slice(1))))),
      appendTo(target) {
        const parent = $(target).list[0];
        list.forEach(item => { parent.children.push(item); item.parent = parent; });
        return $list;
      },
      remove() { list.forEach(item => { item.parent.children = item.parent.children.filter(child => child !== item); }); return $list; },
      tooltip(arg) { list.forEach(item => typeof arg === 'object' ? (item.tooltip = arg) : item.calls.push(arg)); return $list; },
      on(...args) { if (list[0] === document) document.handlers.push(args); return $list; },
      data(key, value) {
        if (value === undefined) return list[0]?.data[key];
        list.forEach(item => { item.data[key] = value; });
        return $list;
      },
      attr(key, value) {
        list.forEach(item => { if (value === null) delete item.attrs[key]; else item.attrs[key] = value; });
        return $list;
      },
      closest: () => wrap([])};
    return $list;
  };
  const $ = target => {
    if (target?.list) return target;
    if (typeof target === 'string') {
      const made = node(/class="([^"]+)"/.exec(target)?.[1] || '');
      made.html = target;
      return wrap([made]);
    }
    return wrap(target ? [target] : []);
  };
  class ControlInput {
    constructor(df) {
      this.df = df;
      this.native = 0;
      this.emptied = 0;
      this.help = node('help');
      this.help_box = node('help-box');
      this.wrapper = node('frappe-control');
      this.wrapper.children.push(Object.assign(node('clearfix'), {children: [node('control-label'), this.help]}), this.help_box);
      this.$wrapper = $(this.wrapper);
      this.input_area = node('control-input');
    }
    set_description(description) {
      this.native += 1;
      if (description !== undefined) this.df.description = description;
      this.help_box.text = this.df.description || '';
    }
    set_empty_description() { this.emptied += 1; this.help_box.text = ''; }
  }
  const native = ControlInput.prototype.set_description;
  const frappe = {boot: {pearnly_field_help: declared}, router: {on() {}},
    ui: {form: {ControlInput}}, utils: {icon: (name, size) => `<svg class="icon icon-${size}"><use href="#icon-${name}"></use></svg>`}};
  vm.runInNewContext(source, {frappe, $, __: (text) => `译:${text}`, document,
    window: {matchMedia: () => ({matches: touch}), addEventListener() {}}});
  return {ControlInput, native};
}

const icon_of = control => control.help.children.find(child => child.classes.has('pearnly-field-help'));

test('没声明的产品（DMS）不动框架的说明写法', () => {
  const {ControlInput, native} = load({declared: null});
  assert.equal(ControlInput.prototype.set_description, native);
});

test('带标题的格：说明进标题旁的图标，字段下方不写', () => {
  const {ControlInput} = load();
  const control = new ControlInput({fieldtype: 'Data', label: 'Base Unit', description: 'Stock is <b>counted</b> here', parent: 'ERP Product'});
  control.set_description();
  assert.equal(control.native, 0);
  assert.equal(control.help_box.text, '');
  const icon = icon_of(control);
  assert.ok(icon, '标题旁有图标');
  assert.match(icon.html, /#icon-info/);
  assert.equal(icon.data['pearnly-help'], '译:Stock is <b>counted</b> here', '照框架原句翻译');
  assert.equal(icon.attrs['aria-label'], '译:Stock is counted here', '读屏读纯文字');
  assert.match(icon.html, /role="button" tabindex="0"/);
  assert.equal(icon.tooltip.trigger, 'hover focus');
  assert.equal(icon.tooltip.container, 'body');
  control.set_description();
  assert.equal(control.help.children.length, 1, '刷新不重复加图标');
});

test('触屏上点开、再点收起（不靠悬停）', () => {
  const {ControlInput} = load({touch: true});
  const control = new ControlInput({fieldtype: 'Select', label: 'Terminal', description: 'Counter'});
  control.set_description();
  assert.equal(icon_of(control).tooltip.trigger, 'manual');
});

test('说明清空时图标一起拿掉；运行时改说明换图标里的字', () => {
  const {ControlInput} = load();
  const control = new ControlInput({fieldtype: 'Data', label: 'Doc No', description: 'Auto'});
  control.set_description();
  control.set_description('Changed');
  assert.equal(icon_of(control).data['pearnly-help'], '译:Changed');
  control.set_description('');
  assert.equal(icon_of(control), undefined);
  assert.deepEqual(control.help.children, []);
});

test('登记的例外 description_inline 照原生留在下方，并带标记', () => {
  const {ControlInput} = load();
  const control = new ControlInput({fieldtype: 'Currency', label: 'Received Amount', description: 'Grand Total 100', description_inline: 1});
  control.set_description();
  assert.equal(control.native, 1);
  assert.equal(control.help_box.text, 'Grand Total 100');
  assert.equal(control.wrapper.attrs['data-pearnly-description'], 'inline');
  assert.equal(icon_of(control), undefined);
});

test('同一格先是说明、后是实时数（定时任务的文件夹）：图标与下方来回换', () => {
  const {ControlInput} = load();
  const control = new ControlInput({fieldtype: 'Select', label: 'Folder', description: 'One task per folder'});
  control.set_description();
  assert.ok(icon_of(control));
  control.df.description_inline = 1;
  control.set_description('3 waiting');
  assert.equal(icon_of(control), undefined);
  assert.equal(control.help_box.text, '3 waiting');
  control.df.description_inline = 0;
  control.set_description('One task per folder');
  assert.ok(icon_of(control));
  assert.equal(control.help_box.text, '');
  assert.equal(control.wrapper.attrs['data-pearnly-description'], undefined);
});

test('按钮的标题区被框架藏起来：图标放在按钮右边', () => {
  const {ControlInput} = load();
  const control = new ControlInput({fieldtype: 'Button', label: 'Download', description: 'Sign in after installing'});
  control.set_description();
  const slot = control.input_area.children.find(child => child.classes.has('pearnly-field-help-slot'));
  assert.ok(slot.children[0].classes.has('pearnly-field-help'));
  assert.deepEqual(control.help.children, []);
});

test('没有标题、只有输入框的格没地方放图标：照原生', () => {
  const {ControlInput} = load();
  const plain = new ControlInput({fieldtype: 'Data', description: 'Hint'});
  plain.set_description();
  assert.equal(plain.native, 1);
  const bare = new ControlInput({fieldtype: 'Data', label: 'Code', description: 'Hint'});
  bare.only_input = true;
  bare.set_description();
  assert.equal(bare.native, 1);
});
