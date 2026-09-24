// Exercise the shipped selector with controlled native input events and delayed RPCs.
// No browser, camera, Frappe Site, master record or external OCR writes are needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname,
  '../apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/entry-choice.js'), 'utf8');
const contexts = [
  {kind: 'supplier', context: 'purchase', reference: 'supplier', title: 'supplier_name',
    field: 'supplier_name', details: {supplier_tax_id: '0125549007435', supplier_address: 'OCR address',
      supplier_branch_type: 'branch', supplier_branch_no: '0001'}},
  {kind: 'customer', context: 'sales', reference: 'customer', title: 'buyer_name',
    field: 'customer_name', details: {party_type: 'company', address: 'OCR address'}},
  ...['purchase', 'sales'].map(context => ({kind: 'product', context, reference: 'product',
    title: 'description', field: 'product_name', details: {code: 'OCR-001', barcode: '8850000000001'}})),
];
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
};

function fixture(config) {
  class Element {
    constructor() { this[0] = {isConnected: true}; this.handlers = new Map(); this.value = ''; }
    val(value) { if (arguments.length) { this.value = value; return this; } return this.value; }
    on(names, callback) {
      for (const name of names.split(' ')) {
        const event = name.split('.')[0];
        this.handlers.set(event, [...(this.handlers.get(event) || []), callback]);
      }
      return this;
    }
    trigger(name, event = {}) { for (const handler of this.handlers.get(name) || []) handler(event); return this; }
    attr() { return this; }
    html() { return this; }
    addClass() { return this; }
    append() { return this; }
    appendTo() { return this; }
    find() { return this; }
    toggle() { return this; }
    toggleClass() { return this; }
    is() { return true; }
  }
  const elements = [], listeners = {}, searches = [], scans = [], alerts = [], opens = [], requests = [];
  const data = {[config.reference]: '', [config.title]: '', qty: 3, unit_price: 42, note: 'keep draft'};
  const input = new Element();
  const control = {$input: input, $wrapper: new Element(), input_area: new Element(),
    input: {addEventListener(name, callback) { listeners[name] = callback; }},
    awesomplete: {evaluate() {}, close() {}},
    set_data(rows) { this.options = rows; },
    async set_value(value) { input.val(value); },
  };
  const form = {fields_dict: {entry_choice: control},
    get_value: key => data[key], async set_values(values) { Object.assign(data, values); }};
  let refresh, searchRows = [], scanResponse, clears = 0;
  const p = {
    link_editors: {},
    watch_master_control(_control, _doctype, callback) { refresh = callback; },
    open_link_create(...args) { opens.push(args); },
    scan(session) { scans.push(session); },
    async call(_method, args) {
      requests.push(args);
      assert.equal(args.kind, config.kind);
      assert.equal(args.context, config.context);
      return args.scan ? await scanResponse.promise : searchRows;
    },
  };
  vm.runInNewContext(source, {
    frappe: {pearnly: p, model: {can_create: () => true}, show_alert: row => alerts.push(row),
      utils: {icon: () => '', debounce(fn) {
        let args;
        const wrapped = (...next) => { args = next; };
        searches.push(async () => { if (args) { const current = args; args = undefined; await fn(...current); } });
        return wrapped;
      }}},
    $: value => {
      if (typeof value !== 'string') return value;
      const element = new Element(); elements.push(element); return element;
    },
    __: text => text,
  });
  const choice = p.bind_entry_choice(form, {...config, editable: true,
    on_select: row => form.set_values({...config.details, selected_detail: row.name}),
    on_clear: async () => {
      clears++;
      await form.set_values(Object.fromEntries(Object.keys(config.details).map(key => [key, ''])));
    }});
  const flush = () => choice.sync();
  const row = key => ({name: `${config.kind}-${key}`, [config.field]: `Name ${key}`, code: key, tax_id: key});
  return {
    data, input, control, choice, alerts, opens, requests, row,
    get clears() { return clears; },
    async show(values) { await form.set_values(values); await choice.show(); },
    async search(rows) {
      searchRows = rows; input.trigger('focus');
      for (const run of searches) await run();
    },
    async edit(value) { input.val(value); input.trigger('input'); await flush(); },
    async select(selected) {
      const value = control.options.find(item => item.value.endsWith(` · ${selected.name}`))?.value;
      assert.ok(value, 'Native option exists');
      input.val(value);
      // Native Autocomplete emits change and then selectcomplete for the same choice.
      input.trigger('change'); input.trigger('awesomplete-selectcomplete'); await flush();
    },
    startScan() {
      scanResponse = deferred(); elements[0].trigger('click');
      return {session: scans.at(-1), response: scanResponse};
    },
    refresh: () => refresh(),
    create() {
      const event = {text: {value: 'pearnly_create__link_option'},
        preventDefault() { this.prevented = true; }, stopImmediatePropagation() { this.stopped = true; }};
      listeners['awesomplete-select'](event); return event;
    },
  };
}

for (const config of contexts) {
  const name = `${config.context}/${config.kind}`;
  test(`${name}: native choice and unique scan retain real IDs`, async () => {
    const f = fixture(config), a = f.row('A'), b = f.row('B');
    await f.search([a, b]); await f.select(b);
    assert.equal(f.data[config.reference], b.name);
    assert.equal(f.data[config.title], b[config.field]);
    const {session, response} = f.startScan();
    const result = session.on_result('A');
    // Focusing the original field can refresh suggestions without changing the choice.
    await f.search([a, b]); response.resolve([a]); await result;
    assert.equal(f.data[config.reference], a.name);
    assert.equal(f.data[config.title], a[config.field]);
    assert.equal(f.data.qty, 3); assert.equal(f.data.unit_price, 42);
    assert.equal(f.clears, 0);
  });

  for (const newer of ['typing', 'selection', 'OCR refill', 'removed field', 'master refresh']) {
    test(`${name}: pending scan cannot overwrite ${newer}`, async () => {
      const f = fixture(config), a = f.row('A'), b = f.row('B');
      await f.search([a, b]);
      const {session, response} = f.startScan();
      const result = session.on_result('A');
      if (newer === 'typing') await f.edit('New text');
      if (newer === 'selection') await f.select(b);
      if (newer === 'OCR refill') await f.show({[config.reference]: '', [config.title]: 'OCR name', ...config.details});
      if (newer === 'removed field') f.control.$wrapper[0].isConnected = false;
      if (newer === 'master refresh') f.refresh();
      const before = {...f.data}, shown = f.input.val();
      response.resolve([a]); await result;
      assert.deepEqual(f.data, before); assert.equal(f.input.val(), shown);
    });
  }

  test(`${name}: later scan wins even when responses arrive out of order`, async () => {
    const f = fixture(config), a = f.row('A'), b = f.row('B');
    const first = f.startScan(), old = first.session.on_result('A');
    const second = f.startScan(), latest = second.session.on_result('B');
    second.response.resolve([b]); await latest;
    first.response.resolve([a]); await old;
    assert.equal(f.data[config.reference], b.name);
    assert.equal(f.data[config.title], b[config.field]);
  });

  test(`${name}: opening then cancelling the scanner does not discard a pending selection`, async () => {
    const f = fixture(config), a = f.row('A');
    await f.search([a]);
    const selection = f.select(a);
    f.startScan(); // No decoded result: equivalent to closing the camera dialog.
    await selection;
    assert.equal(f.data[config.reference], a.name);
    assert.equal(f.data[config.title], a[config.field]);
  });

  test(`${name}: an OCR refill also invalidates a camera result not yet decoded`, async () => {
    const f = fixture(config), scan = f.startScan();
    await f.show({[config.title]: 'OCR name', ...config.details});
    const result = scan.session.on_result('A');
    scan.response.resolve([f.row('A')]); await result;
    assert.equal(f.requests.length, 0);
    assert.equal(f.data[config.title], 'OCR name');
  });

  test(`${name}: correcting unlinked OCR text preserves details; empty text clears them`, async () => {
    const f = fixture(config);
    await f.show({[config.title]: 'OCR typoo', ...config.details});
    await f.edit('OCR typo');
    assert.equal(f.data[config.reference], ''); assert.equal(f.clears, 0);
    for (const [key, value] of Object.entries(config.details)) assert.equal(f.data[key], value);
    await f.edit('');
    assert.equal(f.clears, 1);
    for (const key of Object.keys(config.details)) assert.equal(f.data[key], '');
  });

  test(`${name}: editing a linked name discards its old ID and related details`, async () => {
    const f = fixture(config), a = f.row('A');
    await f.search([a]); await f.select(a); await f.edit('Different master');
    assert.equal(f.data[config.reference], ''); assert.equal(f.clears, 1);
    for (const key of Object.keys(config.details)) assert.equal(f.data[key], '');
    assert.equal(f.data.note, 'keep draft');
  });

  test(`${name}: missing or ambiguous scans remain text without a fabricated ID`, async () => {
    for (const count of [0, 2]) {
      const f = fixture(config), scan = f.startScan();
      const result = scan.session.on_result('unknown');
      scan.response.resolve(count ? [f.row('A'), f.row('B')] : []); await result;
      assert.equal(f.data[config.reference], '');
      assert.equal(f.data[config.title], 'unknown');
      assert.equal(f.alerts.length, count ? 0 : 1);
    }
  });

  test(`${name}: cancelled inline creation and saved-master refresh preserve the draft`, async () => {
    const f = fixture(config), a = f.row('A');
    await f.search([a]); await f.select(a);
    const before = {...f.data}, shown = f.input.val();
    const event = f.create();
    assert.ok(event.prevented && event.stopped);
    assert.equal(f.opens.length, 1);
    // Cancelling the editor does not call refresh or mutate the initiating field.
    assert.deepEqual(f.data, before); assert.equal(f.input.val(), shown);
    // Successful master maintenance only invalidates suggestions, then they reload.
    f.refresh();
    assert.deepEqual(f.data, before); assert.equal(f.input.val(), shown);
    assert.equal(f.control.options.length, 1, 'Only the create action remains before the fresh lookup');
    const b = f.row('B'); await f.search([a, b]); await f.select(b);
    assert.equal(f.data[config.reference], b.name);
    assert.equal(f.data.note, 'keep draft');
  });
}
