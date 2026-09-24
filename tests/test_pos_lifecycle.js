// Execute the shipped adapter/entry and onScan against controlled Desk boundaries.
// These checks cover lifecycle/state ownership, not native rendering or server settlement.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const desk = path.join(__dirname, '../apps/pearnly_erp_portal/pearnly_erp_portal/public/desk');
const source = name => fs.readFileSync(path.join(desk, name), 'utf8');
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
};
const shift = name => ({shift: {name, terminal: 'T1', shift_seq: 1, opened_at: '2026-09-24'}, expected_cash: 0});

function fixture({search = '', initial = shift('S1'), installShims = false} = {}) {
  const requests = [], cleared = [], callbacks = {key: 0, form: 0, model: 0, doc: 0, detail: 0};
  const document = {listeners: {}, addEventListener(event, callback) {
    (this.listeners[event] ||= new Set()).add(callback);
  }, removeEventListener(event, callback) {this.listeners[event]?.delete(callback);}};
  class Element {
    constructor(node) {this[0] = node; this.events = {}; this.length = 1;}
    on(event, ...args) {(this.events[event] ||= []).push(args.at(-1)); return this;}
    off(event, callback) {this.events[event] = (this.events[event] || []).filter(fn => fn !== callback); return this;}
    trigger(event) {Object.entries(this.events).forEach(([key, handlers]) => {
      if (key.split('.')[0] === event) [...handlers].forEach(fn => fn({}));
    }); return this;}
    find() {return this;}
    html(value) {this.content = value; return this;}
    empty() {this.content = ''; return this;}
  }
  const elements = new Map();
  const $ = node => {if (!elements.has(node)) elements.set(node, new Element(node)); return elements.get(node);};
  $.fn = Element.prototype;
  const wrapper = {}, page = {wrapper, clear_menu() {}, clear_secondary_action() {},
    add_menu_item() {}, set_primary_action() {}, set_secondary_action() {}, set_title(text) {this.title = text;}, set_indicator(text) {this.indicator = text;}};
  const keyForeign = () => {}, formForeign = () => {}, modelForeign = () => {}, documentForeign = () => {};
  $(document).on('keydown', documentForeign);
  const keys = {handlers: {enter: [keyForeign]}, standard_shortcuts: [{shortcut: 'enter', description: 'foreign'}]};
  const form = {handlers: {'ERP Sale': {paid: [formForeign]}}, Form: class {
    constructor() {this.doc = {lines: []};}
    refresh_header() {page.title = 'New ERP Sale'; page.crumb = {label: 'ERP Sale'};}
  }};
  const model = {pearnly_pos_shim: !installShims, pearnly_meta_seeded: true, add_child() {}, events: {'ERP Sale Item': {'*': [modelForeign]}}};
  let current = initial, nextShift, nextTerminals;
  const p = {call(method) {
    requests.push(method);
    if (method.endsWith('api_current_shift')) {const next = nextShift; nextShift = null; return next || Promise.resolve(current);}
    if (method.endsWith('api_list_terminals')) {const next = nextTerminals; nextTerminals = null; return next || Promise.resolve([
      {name: 'T1', company: 'C1', warehouse: 'W1', payment_methods: ['cash', 'promptpay']}
    ]);}
    throw new Error(`Unexpected RPC ${method}`);
  }, texts: async () => (key, ...args) => args.length ? `${key}: ${args.join(", ")}` : key,
    breadcrumbs(value) {page.crumb = value;}, subroute: root => `/desk/${root}`, datetime_text: text => text, money: String, restore_sidebar() {}};
  const context = vm.createContext({console, document, $, frappe: {pearnly: p, ui: {keys, form}, model,
    call: async () => ({message: {}}), db: {get_value: async (doctype, name, field) => {
      requests.push({doctype, name, field}); return {message: {contact_email: 'customer@example.test'}};
    }},
    utils: {escape_html: String}, get_route: () => ["pearnly-cashier"], provide() {}}, __: String, clearTimeout: id => cleared.push(id),
    setTimeout, Date, location: {search}});
  context.window = context;
  vm.runInContext(source('pos/onscan.js'), context);
  const O = context.erpnext = {PointOfSale: {}};
  class Component {}
  class Details extends Component {
    bind_custom_control_change_event() {model.events['ERP Sale Item']['*'].push(() => callbacks.detail++);}
  }
  class NativeController {
    constructor(wrapper) {this.wrapper = $(wrapper); this.page = wrapper.page; this.check_opening_entry();}
    get_new_frm(frm) {frm.refresh_header(); return frm;}
    make_app() {
      this.builds = (this.builds || 0) + 1;
      this.frm = {doc: {lines: [{product: 'retained-cart'}]}};
      this.item_selector = {last_search: 101};
      // Match native keys.off replacement: unrelated callbacks survive in a new array.
      keys.handlers.enter = [...keys.handlers.enter, () => callbacks.key++];
      keys.standard_shortcuts[0] = {shortcut: 'enter', description: 'POS'};
      form.handlers['ERP Sale'].paid.push(() => callbacks.form++);
      model.events['ERP Sale Item']['*'].push(() => callbacks.model++);
      $(document).on('keydown', () => callbacks.doc++);
      context.onScan.attachTo(document, {onScan() {}});
      this.details = new O.PointOfSale.ItemDetails({});
      this.prepare_page_actions();
    }
  }
  Object.assign(O.PointOfSale, {Controller: NativeController, ItemSelector: Component,
    ItemDetails: Details, ItemCart: Component, Payment: Component,
    PastOrderList: Component, PastOrderSummary: Component});
  vm.runInContext(source('pos/pearnly-pos-adapter.js'), context);
  vm.runInContext(source('cashier.js'), context);
  return {p, page, wrapper, document, $, keys, form, model, context, callbacks, cleared, requests,
    foreign: {keyForeign, formForeign, modelForeign, documentForeign},
    setShift(value) {current = value;}, delayShift(value) {nextShift = value;},
    delayTerminals(value) {nextTerminals = value;},
    fire() {keys.handlers.enter.forEach(fn => fn()); form.handlers['ERP Sale'].paid.forEach(fn => fn());
      model.events['ERP Sale Item']['*'].forEach(fn => fn()); $(document).trigger('keydown');}};
}

test('ordinary and legacy bookmarks install official adapter once and retain a cart on same-shift reentry', async () => {
  for (const search of ['', '?pearnly_pos_next=1']) {
    const f = fixture({search});
    await f.p.cashier(f.page);
    const controller = f.wrapper.pos, installed = f.context.erpnext.PointOfSale.ItemCart;
    assert.equal(controller.builds, 1);
    assert.equal(f.page.title, 'nav.cashier');
    assert.equal(f.page.crumb.route, '/desk/pearnly-cashier');
    const cart = controller.frm;
    await f.p.cashier(f.page);
    assert.equal(f.context.erpnext.PointOfSale.ItemCart, installed);
    assert.equal(controller.builds, 1);
    f.$(f.wrapper).trigger('hide');
    assert.equal(f.document.scannerDetectionData, undefined);
    f.fire();
    assert.deepEqual(f.callbacks, {key: 0, form: 0, model: 0, doc: 0, detail: 0});
    f.wrapper.on_page_show();
    await controller.pearnly_ready;
    assert.equal(controller.frm, cart);
    assert.equal(controller.builds, 1);
    assert.equal(f.document.listeners.keydown.size, 1);
    f.fire();
    assert.deepEqual(f.callbacks, {key: 1, form: 1, model: 1, doc: 1, detail: 0});
  }
});

test('shift close/reopen clears owned scanner timers/listeners and preserves other Desk listeners', async () => {
  const f = fixture(); await f.p.cashier(f.page);
  const c = f.wrapper.pos;
  const data = f.document.scannerDetectionData;
  data.vars.testTimer = 123; data.vars.longPressTimer = 456;
  f.setShift(null); await c.after_shift_change();
  assert.equal(f.document.scannerDetectionData, undefined);
  assert.ok(f.cleared.includes(123)); assert.ok(f.cleared.includes(456));
  assert.deepEqual(f.keys.handlers.enter, [f.foreign.keyForeign]);
  assert.deepEqual(f.form.handlers['ERP Sale'].paid, [f.foreign.formForeign]);
  assert.deepEqual(f.model.events['ERP Sale Item']['*'], [f.foreign.modelForeign]);
  assert.deepEqual(f.$(f.document).events.keydown, [f.foreign.documentForeign]);
  assert.equal(f.keys.standard_shortcuts[0].description, 'foreign');
  assert.equal(f.page.indicator, 'trade.noShift');
  f.setShift(shift('S2')); await c.after_shift_change();
  assert.equal(c.builds, 2);
  assert.equal(f.document.listeners.keydown.size, 1);
  f.fire();
  assert.deepEqual(f.callbacks, {key: 1, form: 1, model: 1, doc: 1, detail: 0});
});

test('late shift and terminal requests cannot rebuild a hidden or superseded POS', async () => {
  const f = fixture(), lateShift = deferred(); f.delayShift(lateShift.promise);
  const ready = f.p.cashier(f.page);
  f.$(f.wrapper).trigger('hide'); lateShift.resolve(shift('stale'));
  await ready;
  assert.equal(f.wrapper.pos.builds, undefined);
  f.wrapper.on_page_show(); await f.wrapper.pos.pearnly_ready;
  assert.equal(f.wrapper.pos.builds, 1);
  const lateTerminals = deferred(); f.delayTerminals(lateTerminals.promise);
  f.setShift(shift('S2'));
  const refresh = f.wrapper.pos.after_shift_change();
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  f.$(f.wrapper).trigger('hide');
  lateTerminals.resolve([{name: 'T1'}]); await refresh;
  assert.equal(f.wrapper.pos.builds, 1);
  assert.equal(f.document.scannerDetectionData, undefined);
});

test('repeated item details edits replace their model listener and are inert while hidden', async () => {
  const f = fixture(); await f.p.cashier(f.page);
  const detail = f.wrapper.pos.details;
  for (let i = 0; i < 8; i++) detail.bind_custom_control_change_event();
  assert.equal(f.model.events['ERP Sale Item']['*'].length, 3);
  f.fire(); assert.equal(f.callbacks.detail, 1);
  f.$(f.wrapper).trigger('hide'); f.fire(); assert.equal(f.callbacks.detail, 1);
  await f.wrapper.pos.resume();
  f.setShift(null); await f.wrapper.pos.after_shift_change();
  assert.deepEqual(f.model.events['ERP Sale Item']['*'], [f.foreign.modelForeign]);
});

test('an unrelated scanner is not detached by this page', async () => {
  const f = fixture({initial: null});
  const foreign = {vars: {}, options: {}};
  f.document.scannerDetectionData = foreign;
  await f.p.cashier(f.page); f.$(f.wrapper).trigger('hide');
  assert.equal(f.document.scannerDetectionData, foreign);
});

test('hooks load versioned upstream assets once before adapter and official CSS before extensions', () => {
  const hooks = fs.readFileSync(path.join(desk, '../../hooks.py'), 'utf8');
  const js = ['cashier.js', 'pos/onscan.js', 'pos/official-pos_number_pad.js', 'pos/official-pos_item_selector.js',
    'pos/official-pos_item_details.js', 'pos/official-pos_item_cart.js', 'pos/official-pos_payment.js',
    'pos/official-pos_past_order_list.js', 'pos/official-pos_past_order_summary.js', 'pos/official-pos_controller.js',
    'pos/pearnly-pos-shift.js', 'pos/pearnly-pos-receipt.js', 'pos/pearnly-pos-adapter.js'];
  let last = -1;
  for (const file of js) {
    const asset = `versioned_asset("pearnly_erp_portal", "desk/${file}")`;
    assert.equal(hooks.split(asset).length - 1, 1, file);
    const index = hooks.indexOf(asset); assert.ok(index > last, file); last = index;
  }
  assert.ok(hooks.indexOf('"desk/pos/official-pos.css"') < hooks.indexOf('"desk/cashier.css"'));
  assert.equal(source('cashier.js').includes('window.location'), false);
  assert.equal(source('pos/pearnly-pos-adapter.js').includes('O.pearnly_installed = false'), false);
});


test('post-transaction shift refresh updates cash without replacing the cart', async () => {
  const f = fixture(); await f.p.cashier(f.page);
  const controller = f.wrapper.pos, cart = controller.frm;
  f.setShift({...shift('S1'), expected_cash: 30});
  await controller.refresh_shift_summary();
  assert.equal(controller.pearnly_shift.expected_cash, 30);
  assert.match(f.page.indicator, /30$/);
  assert.equal(controller.frm, cart);
  const late = deferred(); f.delayShift(late.promise);
  const refresh = controller.refresh_shift_summary();
  f.$(f.wrapper).trigger('hide'); late.resolve({...shift('S1'), expected_cash: 100});
  await refresh;
  assert.equal(controller.pearnly_shift.expected_cash, 30);
});


test('customer email mapping unwraps the native get_value object and avoids a walk-in lookup', async () => {
  const f = fixture({installShims: true});
  await f.p.cashier(f.page);
  const get = f.context.frappe.db.get_value;
  const named = await get('Customer', 'CUS-1', 'email_id');
  assert.equal(named.message.email_id, 'customer@example.test');
  assert.deepEqual(f.requests.find(row => row?.doctype), {doctype: 'ERP Customer', name: 'CUS-1', field: 'contact_email'});
  const before = f.requests.length;
  assert.equal((await get('Customer', '', 'email_id')).message.email_id, '');
  assert.equal(f.requests.length, before);
});

test('client form header refresh restores native cashier identity only on its own route', async () => {
  const f = fixture(); await f.p.cashier(f.page);
  const controller = f.wrapper.pos;
  const form = controller.get_new_frm(undefined, 'ERP Sale');
  assert.equal(f.page.title, 'nav.cashier');
  assert.equal(f.page.crumb.label, 'nav.cashier');
  form.refresh_header();
  assert.equal(f.page.title, 'nav.cashier');
  f.context.frappe.get_route = () => ['pearnly-inventory'];
  f.page.title = 'Inventory';
  form.refresh_header();
  assert.equal(f.page.title, 'Inventory', 'A late client form refresh must not overwrite a different Desk page');
});
