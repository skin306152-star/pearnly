// 手机键盘不盖住正在填的格（public/desk/keyboard.js）：可见区（visualViewport）变小时，聚焦的格被滚进
// 「可见区减去贴顶 / 贴底条」里；整页、弹窗里的滚动容器、贴底操作条、安卓整页变矮四种情况。
// 带建议列表的格（Link 选择框）摆到可见区上部三分之一、下面留给列表。
// 不该动的：电脑（非触屏）一行不执行、没聚焦文字格时可见区变化不动、勾选框 / 只读 / 日期格不动、人自己滚动不拉回、键盘收起后弹窗变量清掉。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync('apps/pearnly_core/pearnly_core/public/desk/keyboard.js', 'utf8');

// 一个只够这份脚本用的假页面：元素有父子、样式、在页面里的位置；整页滚动与容器滚动都改它们在屏幕上的位置。
function page({ layout = 800, meta = 'width=device-width, initial-scale=1', coarse = true } = {}) {
  const listeners = {}, viewListeners = {}, timers = [];
  const state = { scrollY: 0, scrolledBy: [] };
  const element = (props) => {
    const el = {
      tagName: 'DIV', parentElement: null, style: { position: 'static', overflowY: 'visible' }, readOnly: false, disabled: false,
      isContentEditable: false, isConnected: true, scrollTop: 0, scrollHeight: 0, clientHeight: 0,
      top: 0, height: 40,  // 在父元素内容里的位置（父是整页时就是页面坐标）
      ...props,
    };
    el.getAttribute = (name) => (el.attrs || {})[name] ?? null;
    el.closest = (selector) => { for (let n = el; n; n = n.parentElement) if ((n.className || '').split(' ').includes(selector.slice(1))) return n; return null; };
    el.contains = (other) => { for (let n = other; n; n = n.parentElement) if (n === el) return true; return false; };
    el.getBoundingClientRect = () => {
      let top = el.top;
      let node = el;
      for (; node.parentElement && node.parentElement !== body; node = node.parentElement) {
        top += node.parentElement.top - node.parentElement.scrollTop;
      }
      if (node.style.position !== 'fixed') top -= state.scrollY;
      return { top, bottom: top + el.height, height: el.height, left: 0, width: 360 };
    };
    return el;
  };
  const body = element({ tagName: 'BODY' });
  const root = {
    clientHeight: layout, classList: { set: new Set(), toggle(name, on) { on ? this.set.add(name) : this.set.delete(name); } },
    style: { vars: {}, setProperty(name, value) { this.vars[name] = value; }, removeProperty(name) { delete this.vars[name]; } },
  };
  const covers = [];  // 贴顶 / 贴底条：elementFromPoint 命中它们
  const viewport = { name: 'viewport', content: meta };
  const document = {
    documentElement: root, body, activeElement: body,
    querySelector: (selector) => (selector === 'meta[name="viewport"]' ? viewport : null),
    addEventListener: (name, fn) => { listeners[name] = fn; },
    elementFromPoint: (x, y) => covers.find((el) => { const r = el.getBoundingClientRect(); return y >= r.top && y < r.bottom; }) || body,
  };
  const view = {
    height: layout, width: 360, offsetTop: 0, offsetLeft: 0, scale: 1,
    addEventListener: (name, fn) => { viewListeners[name] = fn; },
  };
  const window = {
    visualViewport: view, getComputedStyle: (el) => el.style, matchMedia: (query) => ({ matches: query === '(pointer: coarse)' && coarse }),
    setTimeout: (fn) => { timers.push(fn); return timers.length; }, clearTimeout: () => {},
    scrollBy: (x, y) => { state.scrollY += y; state.scrolledBy.push(y); },
  };
  vm.runInNewContext(source, { window, document });
  const flush = () => { while (timers.length) timers.shift()(); };
  return {
    body, root, view, viewport, state, covers, element, flush, listeners, viewListeners,
    focus(el) { document.activeElement = el; listeners.focusin({ target: el }); flush(); },
    blur() { document.activeElement = body; listeners.focusout({}); flush(); },
    keyboard(height, top = 0) { view.height = height; view.offsetTop = top; viewListeners.resize(); flush(); },
    scrollView() { viewListeners.scroll(); flush(); },
  };
}
const input = (p, props) => p.element({ tagName: 'INPUT', type: 'text', parentElement: p.body, ...props });
const inside = (rect, top, bottom) => rect.top >= top && rect.bottom <= bottom;

test('a computer (no coarse pointer) runs nothing: no meta change, no listeners', () => {
  const p = page({ coarse: false });
  assert.equal(p.viewport.content, 'width=device-width, initial-scale=1');
  assert.deepEqual(Object.keys(p.listeners), []);
  assert.deepEqual(Object.keys(p.viewListeners), []);
});

test('viewport meta gets interactive-widget once', () => {
  assert.equal(page().viewport.content, 'width=device-width, initial-scale=1, interactive-widget=resizes-content');
  const kept = page({ meta: 'width=device-width, interactive-widget=resizes-content' });
  assert.equal(kept.viewport.content, 'width=device-width, interactive-widget=resizes-content');
});

test('a field on the page is scrolled above the keyboard, and only then', () => {
  const p = page();
  const field = input(p, { top: 700 });
  p.focus(field);
  assert.deepEqual(p.state.scrolledBy, [], 'no keyboard yet: nothing moves');
  assert.ok(!p.root.classList.set.has('pearnly-keyboard-open'));
  p.keyboard(400);
  assert.ok(inside(field.getBoundingClientRect(), 8, 392), JSON.stringify(field.getBoundingClientRect()));
  assert.ok(p.root.classList.set.has('pearnly-keyboard-open'));
  assert.equal(p.root.style.vars['--pearnly-visible-height'], '400px');
  // 人自己往回滚：可见区滚动不再把它拉回来
  p.state.scrollY -= 300;
  p.scrollView();
  assert.equal(p.state.scrolledBy.length, 1);
  // 键盘收起：类名与变量都清掉，弹窗回到原样
  p.keyboard(800);
  assert.ok(!p.root.classList.set.has('pearnly-keyboard-open'));
  assert.deepEqual(p.root.style.vars, {});
});

test('the viewport shrinking without a focused text field changes nothing', () => {
  const p = page();
  p.keyboard(400);
  p.keyboard(700);
  assert.ok(!p.root.classList.set.has('pearnly-keyboard-open'));
  assert.deepEqual(p.root.style.vars, {});
  assert.deepEqual(p.state.scrolledBy, []);
});

test('a sticky action bar at the bottom of the visible area is not covering the field (Android resizes the page)', () => {
  // 安卓 resizes-content：键盘弹出后整页变矮（布局高度跟着缩），贴底条贴在键盘上方；照样认得出键盘弹起
  const p = page();
  const bar = p.element({ parentElement: p.body, style: { position: 'sticky', overflowY: 'visible' }, height: 60 });
  bar.getBoundingClientRect = () => ({ top: 340, bottom: 400, height: 60 });
  p.covers.push(bar);
  const field = input(p, { top: 345 });
  p.focus(field);
  p.root.clientHeight = 400;
  p.keyboard(400);
  const rect = field.getBoundingClientRect();
  assert.ok(inside(rect, 8, 332), JSON.stringify(rect));
});

test('a field in a fixed dialog scrolls the dialog, not the page', () => {
  const p = page();
  const modal = p.element({ parentElement: p.body, style: { position: 'fixed', overflowY: 'auto' }, top: 0, height: 800,
    scrollHeight: 1200, clientHeight: 800 });
  Object.defineProperty(modal, 'scrollTop', {
    get() { return this._top || 0; },
    set(value) { this._top = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); },
  });
  const body = p.element({ parentElement: modal, top: 0, height: 1200 });
  const field = input(p, { parentElement: body, top: 1100 });
  p.covers.push(modal);  // 命中的是包着这一格的弹窗：不算遮挡
  modal.scrollTop = 400;  // 弹窗已滚到底，最后一格在 700
  p.focus(field);
  modal.clientHeight = 400;  // product.css：键盘弹起时弹窗外框缩成可见区（类名在摆格子之前就切好了）
  p.keyboard(400);
  const rect = field.getBoundingClientRect();
  assert.ok(inside(rect, 8, 392), JSON.stringify(rect));
  assert.deepEqual(p.state.scrolledBy, [], 'the page under a fixed dialog does not move');
  assert.equal(p.root.style.vars['--pearnly-visible-top'], '0px');
});

test('controls without a keyboard, read only and disabled fields are left alone', () => {
  const p = page();
  for (const props of [{ type: 'checkbox' }, { type: 'file' }, { type: 'date' }, { readOnly: true }, { disabled: true }, { tagName: 'SELECT' }]) {
    p.focus(input(p, { top: 700, ...props }));
    p.keyboard(400);
    assert.ok(!p.root.classList.set.has('pearnly-keyboard-open'), JSON.stringify(props));
    p.blur();
    p.keyboard(800);
  }
  assert.deepEqual(p.state.scrolledBy, []);
});

test('turning the phone resets the no-keyboard height', () => {
  const p = page();
  const field = input(p, { top: 300 });
  p.view.width = 800;  // 横屏：更宽、更矮，不是键盘
  p.keyboard(360);
  p.focus(field);
  assert.ok(!p.root.classList.set.has('pearnly-keyboard-open'));
  p.keyboard(160);  // 横屏下再弹键盘
  assert.ok(p.root.classList.set.has('pearnly-keyboard-open'));
});

test('a field with a suggestion list (Link) is placed in the top third so the list fits above the keyboard', () => {
  const p = page();
  const wrap = p.element({ parentElement: p.body, className: 'awesomplete', top: 0, height: 2000 });
  const link = input(p, { parentElement: wrap, top: 250 });
  p.focus(link);
  p.keyboard(354);  // 250..290 已在可见区里，但列表往下展开会被键盘挡住
  const rect = link.getBoundingClientRect();
  assert.ok(rect.top >= 8 && rect.bottom <= 354 / 3, JSON.stringify(rect));
  const combo = page();
  const box = input(combo, { top: 60, attrs: { role: 'combobox' } });
  combo.focus(box);
  combo.keyboard(354);
  assert.deepEqual(combo.state.scrolledBy, [], 'already in the top third: nothing moves');
});
