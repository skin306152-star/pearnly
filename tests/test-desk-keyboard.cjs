// 手机键盘不盖住正在填的格（public/desk/keyboard.js）：可见区（visualViewport）变小时，聚焦的格被滚进
// 「可见区减去贴顶 / 贴底条」里；整页、弹窗里的滚动容器、贴底操作条、安卓整页变矮四种情况。
// 带建议列表的格（Link 选择框）摆到可见区上部三分之一、下面留给列表。
// 不该动的：电脑（非触屏）一行不执行、没聚焦文字格时可见区变化不动、勾选框 / 只读 / 日期格不动、人自己滚动不拉回、键盘收起后弹窗变量清掉。
// iOS App 内嵌网页（LINE 等内置浏览器）：元素坐标原点跟着可见区走、offsetTop 仍报几百，可见区按探针实际位置算。
// 弹窗里的日期格：摆它时不让 Frappe 弹窗的滚动把它失焦。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync('apps/pearnly_core/pearnly_core/public/desk/keyboard.js', 'utf8');

// 一个只够这份脚本用的假页面：元素有父子、样式、在页面里的位置；整页滚动与容器滚动都改它们在屏幕上的位置。
function page({ layout = 800, meta = 'width=device-width, initial-scale=1', coarse = true } = {}) {
  const listeners = {}, viewListeners = {}, timers = [];
  // origin：元素坐标原点比布局视口低多少（Safari / 安卓为 0；iOS App 内嵌网页是可见区顶端，等于 offsetTop）
  const state = { scrollY: 0, scrolledBy: [], origin: 0, frames: [] };
  const element = (props) => {
    const el = {
      tagName: 'DIV', parentElement: null, style: { position: 'static', overflowY: 'visible' }, readOnly: false, disabled: false,
      isContentEditable: false, isConnected: true, scrollTop: 0, scrollHeight: 0, clientHeight: 0,
      top: 0, height: 40,  // 在父元素内容里的位置（父是整页时就是页面坐标）
      ...props,
    };
    el.getAttribute = (name) => (el.attrs || {})[name] ?? null;
    el.addEventListener = () => {}; el.removeEventListener = () => {};
    el.setAttribute = (name, value) => { el.attrs = { ...(el.attrs || {}), [name]: value }; };
    el.removeAttribute = (name) => { const { [name]: _, ...rest } = el.attrs || {}; el.attrs = rest; };
    el.closest = (selector) => { for (let n = el; n; n = n.parentElement) if ((n.className || '').split(' ').includes(selector.slice(1))) return n; return null; };
    el.contains = (other) => { for (let n = other; n; n = n.parentElement) if (n === el) return true; return false; };
    el.getBoundingClientRect = () => {
      let top = el.top;
      let node = el;
      for (; node.parentElement && node.parentElement !== body; node = node.parentElement) {
        top += node.parentElement.top - node.parentElement.scrollTop;
      }
      if (node.style.position !== 'fixed') top -= state.scrollY;
      top -= state.origin;
      return { top, bottom: top + el.height, height: el.height, left: 0, width: 360 };
    };
    return el;
  };
  const body = element({ tagName: 'BODY' });
  const root = {
    clientHeight: layout, clientWidth: 360, classList: { set: new Set(), toggle(name, on) { on ? this.set.add(name) : this.set.delete(name); } },
    style: { vars: {}, setProperty(name, value) { this.vars[name] = value; }, removeProperty(name) { delete this.vars[name]; } },
  };
  const covers = [];  // 贴顶 / 贴底条：elementFromPoint 命中它们
  const viewport = { name: 'viewport', content: meta };
  // 探针：fixed，top / height 取 --pearnly-visible-top / height（与 product.css 的弹窗外框同一组变量）
  const probes = [];
  body.appendChild = (el) => { el.parentElement = body; probes.push(el); };
  const document = {
    documentElement: root, body, activeElement: body,
    createElement: () => {
      const el = element({ style: { position: 'fixed', overflowY: 'visible' } });
      el.remove = () => { probes.splice(probes.indexOf(el), 1); el.parentElement = null; };
      el.getBoundingClientRect = () => {
        const top = parseFloat(root.style.vars['--pearnly-visible-top'] ?? '0') - state.origin;
        const height = parseFloat(root.style.vars['--pearnly-visible-height'] ?? String(layout));
        return { top, bottom: top + height, height, left: 0, width: 1 };
      };
      return el;
    },
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
    requestAnimationFrame: (fn) => { state.frames.push(fn); },
  };
  vm.runInNewContext(source, { window, document });
  const flush = () => { while (timers.length || state.frames.length) (timers.shift() || state.frames.shift())(); };
  return {
    body, root, view, viewport, state, covers, element, flush, listeners, viewListeners, probes,
    focus(el) { document.activeElement = el; listeners.focusin({ target: el }); flush(); },
    press(el) { document.activeElement = el; listeners.focusin({ target: el }); },  // 聚焦后不等定时器（浏览器紧接着自己滚）
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
  assert.equal(p.probes.length, 0, 'the probe goes with the keyboard');
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
  const dated = page();
  const date = input(dated, { top: 250, attrs: { 'data-fieldtype': 'Date' } });  // Frappe 日期控件：日历弹层在下面
  dated.focus(date);
  dated.keyboard(354);
  assert.ok(date.getBoundingClientRect().bottom <= 354 / 3, JSON.stringify(date.getBoundingClientRect()));
});

test('a sticky dialog header that only sticks after the scroll does not end up over the field', () => {
  // 开班弹窗真机：收银台 Link 在第一格，摆到可见区顶端后被滚动才贴顶的标题栏盖住（11-01）
  const p = page();
  const modal = p.element({ parentElement: p.body, style: { position: 'fixed', overflowY: 'auto' }, top: 0, height: 800,
    scrollHeight: 1200, clientHeight: 354 });
  Object.defineProperty(modal, 'scrollTop', {
    get() { return this._top || 0; },
    set(value) { this._top = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); },
  });
  const content = p.element({ parentElement: modal, top: 8, height: 1180 });
  const header = p.element({ parentElement: content, style: { position: 'sticky', overflowY: 'visible' }, top: 0, height: 47 });
  header.getBoundingClientRect = () => {  // 没滚时在 8–55，滚过 8px 后贴在 0–47
    const top = Math.max(0, 8 - modal.scrollTop);
    return { top, bottom: top + 47, height: 47 };
  };
  const link = input(p, { parentElement: content, top: 101, attrs: { role: 'combobox' } });
  p.covers.push(header, modal);
  p.focus(link);
  p.keyboard(354);
  const rect = link.getBoundingClientRect();
  assert.ok(rect.top >= header.getBoundingClientRect().bottom, JSON.stringify({ rect, header: header.getBoundingClientRect() }));
});

// 可滚动的固定弹窗：scrollTop 夹在 0 到可滚范围之间；键盘弹起时外框按 --pearnly-visible-top / height 摆（product.css）
function dialog(p, { height = 1200 } = {}) {
  const modal = p.element({ parentElement: p.body, className: 'modal show', style: { position: 'fixed', overflowY: 'auto' }, top: 0,
    height: 800, scrollHeight: height, clientHeight: 800 });
  Object.defineProperty(modal, 'scrollTop', {
    get() { return this._top || 0; },
    set(value) { this._top = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); (this.onscroll || (() => {}))(); },
  });
  const follow = () => {
    modal.top = parseFloat(p.root.style.vars['--pearnly-visible-top'] ?? '0');
    modal.clientHeight = modal.height = parseFloat(p.root.style.vars['--pearnly-visible-height'] ?? '800');
  };
  p.covers.push(modal);
  return { modal, follow };
}

test('iOS in-app browser: the visible area is where the dialog actually lands, not offsetTop (LINE transfer remark, 11-01)', () => {
  // iPhone LINE：键盘弹起后 offsetTop 363、可见区高 351，但元素坐标原点就是可见区顶端（弹窗 CSS top 363px，矩形却是 0–351）。
  // 旧算法把可见区当成 363–714，备注在 568–608 被当成看得见，一行没滚
  const p = page({ layout: 714 });
  const { modal, follow } = dialog(p);
  const content = p.element({ parentElement: modal, top: 0, height: 1200 });
  const remark = p.element({ tagName: 'TEXTAREA', parentElement: content, top: 1100, height: 40 });
  modal.scrollTop = 532;  // 点之前弹窗滚到下部，备注在 568–608
  p.focus(remark);
  p.view.height = 351; p.view.offsetTop = 363; p.state.origin = 363;
  p.viewListeners.resize();
  follow();
  p.flush();
  const rect = remark.getBoundingClientRect();
  assert.ok(inside(rect, 8, 343), JSON.stringify({ rect, scrollTop: modal.scrollTop }));
  assert.equal(p.probes.length, 1);
});

test('a date field low in a dialog keeps its focus: the scroll that places it does not look like a Date field to Frappe', () => {
  const p = page();
  const { modal, follow } = dialog(p);
  const content = p.element({ parentElement: modal, top: 0, height: 1200 });
  const date = input(p, { parentElement: content, top: 700, attrs: { 'data-fieldtype': 'Date' } });
  const seen = [];
  modal.onscroll = () => seen.push(date.getAttribute('data-fieldtype'));  // Frappe：弹窗滚动时焦点在 Date 格就 blur
  p.press(date);
  modal.scrollTop = 300;  // 安卓 Chrome：键盘弹出、页面变矮时自己先把弹窗滚一下露出这一格，在共用模块动手之前
  p.view.height = 354;
  p.viewListeners.resize();
  follow();
  p.flush();
  assert.ok(seen.length > 1, 'the dialog was scrolled by the browser and by the module');
  assert.deepEqual([...new Set(seen)], [null], 'no scroll happened while the field still said Date');
  assert.equal(date.getAttribute('data-fieldtype'), 'Date', 'the type is back after the scroll');
  assert.ok(date.getBoundingClientRect().bottom <= 354 / 3 + 1, JSON.stringify(date.getBoundingClientRect()));
});
