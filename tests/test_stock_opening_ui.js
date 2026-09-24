// Run the shipped opening-inventory page handlers with controlled native controls/RPCs.
// No Site, browser, file upload, stock write or network call is performed. This tests
// state transitions and payloads, not Frappe rendering or backend stock validation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const desk = path.join(__dirname, '../apps/pearnly_erp_portal/pearnly_erp_portal/public/desk');
const openingSource = fs.readFileSync(path.join(desk, 'stock-opening.js'), 'utf8');
const inventorySource = fs.readFileSync(path.join(desk, 'inventory.js'), 'utf8');
const paginationURL = '/assets/frappe/js/frappe/form/grid_pagination.js';
// Only replace the browser module-loading boundary; all shipped page handlers run unchanged.
const openingRunnable = openingSource.replace(`import("${paginationURL}")`, `__import__("${paginationURL}")`);
assert.notEqual(openingRunnable, openingSource, 'The page loads the installed native pagination module');
// Optional adapter check against the locked runtime source, without vendoring a copy.
const nativePaginationSource = process.env.PEARNLY_NATIVE_GRID_PAGINATION_SOURCE
  ? fs.readFileSync(process.env.PEARNLY_NATIVE_GRID_PAGINATION_SOURCE, 'utf8') : null;
const plain = value => JSON.parse(JSON.stringify(value));
const translate = (key, ...args) => args.length ? `${key}: ${args.join(', ')}` : key;
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return {promise, resolve, reject};
};
const row = (extra = {}) => ({product: 'PRODUCT-1', unit: 'piece', qty: '2', unit_cost: '3.50', ...extra});
const review = (extra = {}) => ({valid: true, review_token: 'review-one', total_value: '7.00',
  rows: [{row_number: 1, product: 'PRODUCT-1', product_name: 'Coffee', qty: 2,
    unit: 'piece', unit_cost: 3.5, amount: 7, status: 'ready'}], ...extra});

function fixture({inventory = false, allowed = true, initialRoute, delayTotals = false,
  asyncNativeChanges = false, delayPagination = false} = {}) {
  const requests = [], notices = [], successes = [], confirmations = [], uploaders = [], redirects = [];
  const downloads = [], forms = [], lists = [];
  const moduleRequests = [], paginators = [], paginationModule = deferred();
  let scrolls = 0;
  const nativeChanges = [];
  let flushingNativeChanges = false;
  function nativeChange(control) {
    if (asyncNativeChanges && control.df.onchange)
      nativeChanges.push(() => control.df.onchange.call(control));
  }
  let route = initialRoute || ['pearnly-inventory', ...(inventory ? [] : ['opening'])];
  let alive = true, renderRoute, routeVersion = 0;
  const dispatch = () => {
    const version = ++routeVersion;
    return renderRoute(route.slice(1), () => alive && version === routeVersion);
  };
  class Element {
    constructor() {
      this.props = {}; this.handlers = new Map(); this.removed = false; this.found = new Map(); this.captures = new Map();
      this[0] = {addEventListener: (event, fn) => this.captures.set(event, fn)};
    }
    prop(key, value) { this.props[key] = value; return this; }
    on(event, fn) { this.handlers.set(event, fn); return this; }
    trigger(event, detail) { this.handlers.get(event)?.(detail); return this; }
    appendTo(parent) { this.parent = parent; return this; }
    remove() { this.removed = true; return this; }
    attr() { return this; }
    css() { return this; }
    html(value) { this.htmlValue = value; return this; }
    text(value) { this.textValue = value; return this; }
    val(value) { if (arguments.length) { this.value = value; return this; } return this.value; }
    find(selector) {
      if (!this.found.has(selector)) this.found.set(selector, new Element().appendTo(this));
      return this.found.get(selector);
    }
    addClass() { return this; }
    contents() { return this; }
    filter() { return this; }
    first() { return this; }
    replaceWith(node) { this.text = node.text; return this; }
    get attached() { return !this.removed && (!this.parent || this.parent.attached); }
  }
  const page = {
    main: new Element(), buttons: [], btn_primary: new Element(),
    set_title(value) { this.title = value; },
    set_indicator(value) { this.indicator = value; },
    set_primary_action(label, run) { this.primary = {label, run}; },
    set_secondary_action(label, run) { this.secondary = {label, run}; },
    clear_secondary_action() { this.secondary = null; },
    add_inner_button(label, run) { this.buttons.push({label, run}); },
    add_field(df) { return {df, get_value: () => ''}; },
  };
  function layout(parent, definitions) {
    const fields_dict = {}, sections = [];
    let section;
    const addSection = () => {
      section = {head: new Element(), wrapper: new Element(), columns: [], fields: [],
        collapse(value) { this.collapsed = value; }};
      sections.push(section);
    };
    for (const df of definitions) {
      if (df.fieldtype === 'Section Break') addSection();
      if (!df.fieldname) continue;
      if (!section) addSection();
      const control = {df, value: df.default ?? '', $wrapper: new Element().appendTo(parent), $input: new Element(), refreshes: 0,
        section, get_value() { return this.value; }, refresh() { this.refreshes++; }};
      section.fields.push(control);
      fields_dict[df.fieldname] = control;
      nativeChange(control);
    }
    const form = {fields_dict, parent, sections,
      get_value(name) { return fields_dict[name].value; },
      set_value(name, value) { fields_dict[name].value = value; nativeChange(fields_dict[name]); },
      async set_values(values) {
        for (const [name, value] of Object.entries(values)) this.set_value(name, value);
      },
      set_df_property(name, key, value) { fields_dict[name].df[key] = value; },
      // Native Layout caches empty-section visibility separately from each control.
      // Updating hidden on a control does not refresh its ancestor section.
      refresh_sections() {
        for (const section of sections) section.visible = section.fields.some(control => !control.df.hidden);
      },
    };
    form.refresh_sections();
    forms.push(form); return form;
  }
  const p = {
    layout, breadcrumbs() {}, subroute: root => root, warehouse_query: () => () => ({}),
    success: value => successes.push(value),
    redirect_up(root) { redirects.push(root); route = [root]; },
    call(method, args, verb) {
      if (method.endsWith('api_stock_totals') && !delayTotals)
        return Promise.resolve({sku_count: 0, out_count: 0, low_count: 0, value: 0});
      const request = {...deferred(), method, args: plain(args), verb};
      requests.push(request);
      if (flushingNativeChanges && method.endsWith('api_get_product')) request.resolve({base_unit: 'piece'});
      return request.promise;
    },
    clear_page() { page.buttons = []; page.primary = null; page.indicator = null; },
    routed_page({render}) { renderRoute = render; return dispatch; },
    filter_values: () => ({}), selection_action: () => () => {},
    money: String, number: String,
    list() {
      const list = {ready: Promise.resolve(), destroyed: false,
        destroy() { this.destroyed = true; }, async reload() {}};
      lists.push(list); return list;
    },
  };
  const frappe = {
    pearnly: p,
    boot: {pearnly_pages: allowed ? ['pearnly-inventory', 'pearnly-stock-in'] : ['pearnly-inventory'],
      pearnly_companies: [{name: 'COMPANY-A', company_name: 'Company A', is_current: 1}],
      pearnly_warehouse: 'WAREHOUSE-A'},
    get_route: () => route,
    set_route(...next) { route = next; },
    datetime: {get_today: () => '2026-09-24'},
    msgprint: value => notices.push(value),
    confirm(message, yes, no) { confirmations.push({message, yes, no}); },
    ui: {FileUploader: class {constructor(options) { uploaders.push(options); }}},
    utils: {icon: () => '', scroll_to: () => { scrolls++; }},
  };
  const dollar = () => new Element(); dollar.extend = Object.assign;
  const context = {frappe, __: translate, URLSearchParams, $: dollar,
    Node: {TEXT_NODE: 3}, document: {createTextNode: text => ({text})},
    window: {location: {assign: value => downloads.push(value)}}};
  // Default test double covers only the public pagination API, without rendering any UI.
  class PaginationAPI {
    constructor(opts) {
      Object.assign(this, opts); this.page_length = this.grid.meta.grid_page_length; this.page_index = 1;
      this.$page_number = new Element(); this.last_page_button = new Element(); this.update_page_numbers();
    }
    update_page_numbers() { this.total_pages = Math.ceil(this.grid.data.length / this.page_length); }
    go_to_page(index = this.page_index, fromRefresh = false) {
      this.page_index = index; this.grid.render_result_rows(); this.update_page_numbers();
      if (!fromRefresh) this.grid.scroll_to_top();
    }
    render_next_page() { if (this.page_index * this.page_length < this.grid.data.length) this.go_to_page(this.page_index + 1); }
    render_prev_page() { if (this.page_index > 1) this.go_to_page(this.page_index - 1); }
  }
  const NativePagination = nativePaginationSource ? vm.runInNewContext(
    nativePaginationSource.replace('export default class GridPagination', 'class GridPagination') + '\nGridPagination;',
    context, {filename: 'installed-frappe-grid-pagination.js'}) : PaginationAPI;
  class ObservedPagination extends NativePagination {
    constructor(opts) { super(opts); paginators.push(this); }
  }
  context.__import__ = url => { moduleRequests.push(url); return paginationModule.promise; };
  if (!delayPagination) paginationModule.resolve({default: ObservedPagination});
  vm.runInNewContext(openingRunnable, context, {filename: 'stock-opening.js'});
  vm.runInNewContext(inventorySource, context, {filename: 'inventory.js'});
  const ready = inventory ? p.inventory(page, translate, {})
    : Promise.resolve(p.stockOpening(page, translate, () => alive));
  return {
    page, requests, notices, successes, confirmations, uploaders, redirects, downloads, lists, ready,
    moduleRequests,
    get pager() { return paginators.at(-1); },
    get scrolls() { return scrolls; },
    finishPagination() { paginationModule.resolve({default: ObservedPagination}); },
    paginationEvent(name, action) {
      const event = {preventDefault() { this.prevented = true; },
        stopImmediatePropagation() { this.stopped = true; }, stopPropagation() { this.stopped = true; }};
      this.form.fields_dict.pagination.$wrapper.captures.get(name)?.(event);
      if (!event.stopped) action(); return event;
    },
    get form() { return forms.find(form => form.fields_dict.warehouse); },
    get lineForms() { return forms.filter(form => form.fields_dict.product && form.parent.attached); },
    get lineSections() { return forms.filter(form => form.fields_dict.fields && form.parent.attached)
      .map(form => form.sections[0]); },
    get layoutsMade() { return forms.length; },
    get rows() { return this.lineForms.map(form => Object.fromEntries(
      ['product', 'unit', 'qty', 'unit_cost', 'product_code', 'barcode', 'location']
        .map(name => [name, form.get_value(name)])
        .filter(([name, value]) => ['product', 'unit', 'qty', 'unit_cost'].includes(name) || value !== '')
    )); },
    setRows(value) {
      while (this.lineForms.length < value.length) this.form.fields_dict.add_line.df.click();
      value.forEach((row, index) => {
        for (const [name, value] of Object.entries(row)) {
          if (this.lineForms[index].fields_dict[name]) this.lineForms[index].fields_dict[name].value = value;
        }
      });
    },
    async field(name, value) {
      const control = this.form.fields_dict[name]; control.value = value;
      await control.df.onchange?.call(control);
    },
    async cell(index, name, value, notify = true) {
      const control = this.lineForms[index].fields_dict[name]; control.value = value;
      if (notify) await control.df.onchange?.call(control);
    },
    async flushNativeChanges() {
      flushingNativeChanges = true;
      try {
        for (let remaining = 10000; nativeChanges.length; remaining--) {
          assert.ok(remaining > 0, 'Native callbacks must settle without an initialization loop');
          await nativeChanges.shift()();
        }
      } finally { flushingNativeChanges = false; }
    },
    upload() { return this.form.fields_dict.pick_file.df.click(); },
    depart(next = ['pearnly-purchase']) { route = next; },
    invalidate() { alive = false; },
    async navigate(rest) { route = ['pearnly-inventory', ...rest]; return dispatch(); },
  };
}

async function acceptedPreview(f, result = review()) {
  const done = f.page.primary.run();
  assert.match(f.requests.at(-1).method, /api_preview_stock_opening$/);
  f.requests.at(-1).resolve(result); await done;
}

async function importRows(f, rows) {
  const confirms = f.confirmations.length;
  f.upload();
  if (f.confirmations.length > confirms) f.confirmations.at(-1).yes();
  const done = f.uploaders.at(-1).on_success({file_url: '/private/files/opening.xlsx'});
  f.requests.at(-1).resolve({rows});
  await new Promise(resolve => setImmediate(resolve));
  assert.match(f.requests.at(-1).method, /api_preview_stock_opening$/);
  f.requests.at(-1).resolve(review({rows: rows.map((entry, index) => ({...entry,
    row_number: entry.row_number ?? index + 1, status: 'ready'}))}));
  await done;
}

test('blank quantity/cost and explicit zero remain distinct in the preview payload', async () => {
  const f = fixture();
  f.setRows([row({qty: '', unit_cost: ''}), row({qty: '0', unit_cost: '0'})]);
  const fields = f.lineForms[0].fields_dict;
  assert.equal(fields.qty.df.fieldtype, 'Data');
  assert.equal(fields.unit_cost.df.fieldtype, 'Data');
  const done = f.page.primary.run();
  assert.deepEqual(f.requests[0].args.rows.map(({qty, unit_cost}) => ({qty, unit_cost})),
    [{qty: '', unit_cost: ''}, {qty: '0', unit_cost: '0'}]);
  f.requests[0].resolve(review({valid: false})); await done;
  assert.equal(f.page.primary.label, 'stock.opening.preview');
});

test('missing header and invalid review never expose submit or discard entered rows', async () => {
  const f = fixture(); f.setRows([row()]);
  await f.field('warehouse', ''); await f.page.primary.run();
  assert.equal(f.requests.length, 0);
  await f.field('warehouse', 'WAREHOUSE-A');
  await acceptedPreview(f, review({valid: false, rows: [{row_number: 1, error: 'Invalid cost'}]}));
  assert.equal(f.page.primary.label, 'stock.opening.preview');
  assert.equal(f.lineForms[0].fields_dict.qty.df.read_only || 0, 0);
  assert.deepEqual(f.rows, [row()]);
  assert.equal(f.lineForms[0].fields_dict.status_text.value, 'Invalid cost');
  assert.ok(f.notices.includes('stock.opening.fixErrors'));
});

test('valid review locks input; edit retains values and invalidates the previous submit callback', async () => {
  const f = fixture(); f.setRows([row()]); await f.field('note', 'Keep this note');
  await acceptedPreview(f);
  const staleSubmit = f.page.primary.run;
  for (const name of ['warehouse', 'as_of_date', 'note', 'pick_file', 'add_line'])
    assert.equal(f.form.fields_dict[name].df.read_only, 1, name);
  assert.equal(f.lineForms[0].fields_dict.qty.df.read_only, 1);
  f.upload(); assert.equal(f.uploaders.length, 0);
  f.page.secondary.run();
  assert.deepEqual(f.rows, [row()]); assert.equal(f.form.fields_dict.note.value, 'Keep this note');
  assert.equal(f.lineForms[0].fields_dict.qty.df.read_only, 0);
  assert.equal(f.lineForms[0].fields_dict.remove.df.hidden, 0);
  await staleSubmit(); assert.equal(f.requests.length, 1, 'Old action must not post an invalidated review');
});

test('submit sends exactly the reviewed snapshot/token once and retains it after a failed request', async () => {
  const f = fixture(); f.setRows([row({__checked: 1, doctype: 'untrusted metadata'})]);
  await acceptedPreview(f);
  const snapshot = f.requests[0].args, submit = f.page.primary.run;
  const first = submit(); await submit();
  assert.equal(f.requests.length, 2, 'Double click cannot create a second in-flight request');
  assert.deepEqual(f.requests[1].args, {...snapshot, review_token: 'review-one'});
  assert.equal(f.requests[1].verb, 'POST');
  assert.equal(f.requests[1].args.rows[0].doctype, undefined);
  f.requests[1].reject(new Error('server rejected'));
  await assert.rejects(first, /server rejected/);
  assert.equal(f.redirects.length, 0); assert.equal(f.page.btn_primary.props.disabled, false);
  const retry = submit();
  assert.deepEqual(f.requests[2].args, f.requests[1].args, 'Retry keeps the same reviewed token/payload');
  f.requests[2].resolve({created: 1, skipped: 0}); await retry;
  assert.equal(f.successes.length, 1); assert.deepEqual(f.redirects, ['pearnly-inventory']);
});

for (const mutation of ['change event', 'uncommitted cell', 'route departure', 'obsolete render']) {
  test(`a late preview cannot approve ${mutation}`, async () => {
    const f = fixture(); f.setRows([row()]); const done = f.page.primary.run();
    if (mutation === 'change event') await f.cell(0, 'qty', '9');
    if (mutation === 'uncommitted cell') await f.cell(0, 'qty', '9', false);
    if (mutation === 'route departure') f.depart();
    if (mutation === 'obsolete render') f.invalidate();
    f.requests[0].resolve(review()); await done;
    assert.equal(f.page.primary.label, 'stock.opening.preview');
    assert.equal(f.form.fields_dict.total_value.df.hidden, 1);
    assert.equal(f.lineForms[0].fields_dict.qty.df.read_only || 0, 0);
  });
}

test('cancel while preview is pending leaves without a submit or a late review replacing the page', async () => {
  const f = fixture(); f.setRows([row()]); const done = f.page.primary.run();
  f.page.secondary.run(); f.requests[0].resolve(review()); await done;
  assert.deepEqual(f.redirects, ['pearnly-inventory']); assert.equal(f.requests.length, 1);
  assert.equal(f.form.fields_dict.total_value.df.hidden, 1); assert.deepEqual(f.rows, [row()]);
});

test('cancelling replacement or the uploader preserves rows and does not parse or post', () => {
  const f = fixture(); f.setRows([row()]); f.upload();
  assert.equal(f.confirmations.length, 1); assert.equal(f.uploaders.length, 0);
  f.confirmations[0].no?.();
  assert.deepEqual(f.rows, [row()]); assert.equal(f.requests.length, 0);
  f.upload(); f.confirmations[1].yes();
  assert.equal(f.uploaders.length, 1);
  assert.deepEqual(plain(f.uploaders[0].restrictions.allowed_file_types), ['.xlsx']);
  assert.equal(f.uploaders[0].allow_multiple, false);
  // Closing the native upload dialog invokes no success callback.
  assert.deepEqual(f.rows, [row()]); assert.equal(f.requests.length, 0);
});

for (const laterAction of ['edit', 'review', 'leave']) {
  test(`a delayed replacement confirmation cannot open an uploader after ${laterAction}`, async () => {
    const f = fixture(); f.setRows([row()]); f.upload();
    if (laterAction === 'edit') await f.cell(0, 'qty', '8');
    if (laterAction === 'review') await acceptedPreview(f);
    if (laterAction === 'leave') f.depart();
    f.confirmations[0].yes();
    assert.equal(f.uploaders.length, 0, 'The confirmation belongs to the earlier editable snapshot');
  });
}

test('file parse failure retains input and re-enables preview for correction/retry', async () => {
  const f = fixture(); f.setRows([row()]); await f.field('note', 'Original');
  f.upload(); f.confirmations[0].yes();
  const done = f.uploaders[0].on_success({file_url: '/private/files/opening.xlsx'});
  assert.match(f.requests[0].method, /api_read_stock_opening_file$/);
  f.requests[0].reject(new Error('Invalid workbook'));
  await assert.rejects(done, /Invalid workbook/);
  assert.deepEqual(f.rows, [row()]); assert.equal(f.form.fields_dict.note.value, 'Original');
  assert.equal(f.page.btn_primary.props.disabled, false);
  assert.equal(f.page.primary.label, 'stock.opening.preview'); assert.equal(f.redirects.length, 0);
});

test('confirmed import replaces only rows and still requires a valid review before submit', async () => {
  const f = fixture(); f.setRows([row()]); await f.field('note', 'Retain header');
  f.upload(); f.confirmations[0].yes();
  const done = f.uploaders[0].on_success({file_url: '/private/files/opening.xlsx'});
  const imported = [row({product: 'PRODUCT-2', qty: '0', unit_cost: ''})];
  f.requests[0].resolve({rows: imported});
  await new Promise(resolve => setImmediate(resolve));
  assert.match(f.requests[1].method, /api_preview_stock_opening$/);
  f.requests[1].resolve(review({rows: [{row_number: 1, product: 'PRODUCT-2', unit: 'piece'}]}));
  await done;
  assert.deepEqual(f.rows, imported); assert.equal(f.form.fields_dict.note.value, 'Retain header');
  assert.equal(f.form.fields_dict.warehouse.value, 'WAREHOUSE-A');
  assert.equal(f.page.primary.label, 'stock.opening.preview');
  assert.equal(f.requests.length, 2, 'Automatic matching is not approval to post');
});

for (const stage of ['before upload completes', 'during file parsing']) {
  test(`an imported file cannot overwrite input changed ${stage}`, async () => {
    const f = fixture(); f.setRows([row()]); f.upload(); f.confirmations[0].yes();
    if (stage === 'before upload completes') await f.cell(0, 'qty', '11');
    const done = f.uploaders[0].on_success({file_url: '/private/files/opening.xlsx'});
    if (stage === 'during file parsing') await f.cell(0, 'qty', '11');
    for (const request of f.requests) request.resolve({rows: [row({qty: '99'})]});
    await done;
    assert.equal(f.rows[0].qty, '11', 'A delayed file must not erase input entered after replacement was approved');
    assert.equal(f.page.primary.label, 'stock.opening.preview');
  });
}

test('a late upload cannot replace rows that have since been reviewed and locked', async () => {
  const f = fixture(); f.setRows([row()]); f.upload(); f.confirmations[0].yes();
  // Native uploader may close while its request finishes; user then reviews the page.
  await acceptedPreview(f);
  const done = f.uploaders[0].on_success({file_url: '/private/files/opening.xlsx'});
  for (const request of f.requests.slice(1)) request.resolve({rows: [row({qty: '99'})]});
  await done;
  assert.deepEqual(f.rows, [row()]);
  assert.equal(f.page.primary.label, 'stock.opening.submit');
  assert.equal(f.lineForms[0].fields_dict.qty.df.read_only, 1);
});

test('late upload/parse callbacks after route departure cannot mutate the old form', async () => {
  for (const stage of ['upload', 'parse']) {
    const f = fixture(); f.setRows([row()]); f.upload(); f.confirmations[0].yes();
    if (stage === 'upload') f.depart();
    const done = f.uploaders[0].on_success({file_url: '/private/files/opening.xlsx'});
    if (stage === 'parse') f.depart();
    for (const request of f.requests) request.resolve({rows: [row({qty: '99'})]});
    await done; assert.deepEqual(f.rows, [row()]);
  }
});

test('a product detail response cannot fill a newer product selection or a departed page', async () => {
  const f = fixture(); f.setRows([row()]);
  const first = f.cell(0, 'product', 'PRODUCT-1');
  const second = f.cell(0, 'product', 'PRODUCT-2');
  await new Promise(resolve => setImmediate(resolve));
  f.requests[1].resolve({base_unit: 'box'}); await second;
  f.requests[0].resolve({base_unit: 'piece'}); await first;
  assert.equal(f.rows[0].unit, 'box');
  const third = f.cell(0, 'product', 'PRODUCT-3'); f.depart();
  await new Promise(resolve => setImmediate(resolve));
  f.requests[2].resolve({base_unit: 'kg'}); await third;
  assert.equal(f.rows[0].unit, '');
});

test('inventory exposes opening only with stock-in access and rejects an unauthorized deep link', async () => {
  const denied = fixture({inventory: true, allowed: false}); await denied.ready;
  assert.ok(!denied.page.buttons.some(button => button.label === 'stock.opening.title'));
  await denied.navigate(['opening']);
  assert.deepEqual(denied.redirects, ['pearnly-inventory']); assert.equal(denied.form, undefined);
  assert.equal(denied.requests.length, 0);
  const allowed = fixture({inventory: true}); await allowed.ready;
  const entry = allowed.page.buttons.find(button => button.label === 'stock.opening.title');
  assert.ok(entry); entry.run(); await allowed.navigate(['opening']);
  assert.equal(allowed.page.title, 'stock.opening.title');
  assert.equal(allowed.page.primary.label, 'stock.opening.preview');
  assert.equal(allowed.lists[0].destroyed, true);
  await allowed.navigate(['opening', 'unexpected']);
  assert.deepEqual(allowed.redirects, ['pearnly-inventory']);
});

test('late inventory totals cannot repopulate the opening page after its route has changed', async () => {
  const f = fixture({inventory: true, delayTotals: true});
  await new Promise(resolve => setImmediate(resolve));
  assert.match(f.requests[0].method, /api_stock_totals$/);
  await f.navigate(['opening']);
  f.requests[0].resolve({sku_count: 19, out_count: 3, low_count: 2, value: 999}); await f.ready;
  assert.equal(f.page.title, 'stock.opening.title');
  assert.equal(f.page.indicator, null, 'An obsolete stock list request must not decorate the new form');
});

test('5000 imported rows render only 20; page changes preserve decimals and review/submit all rows', async () => {
  const f = fixture();
  const imported = Array.from({length: 5000}, (_, index) => row({
    product: `PRODUCT-${index + 1}`, product_name: `Coffee ${index + 1}`,
    product_code: `CODE-${index + 1}`, row_number: index + 2,
  }));
  await importRows(f, imported);
  assert.equal(`${f.pager.page_index} / ${f.pager.total_pages}`, '1 / 250');
  assert.equal(f.lineForms.length, 20);
  assert.ok(f.layoutsMade < 50, 'Do not construct thousands of hidden native FieldGroups');
  assert.ok(f.lineSections[0].head.text.includes('Coffee 1 · CODE-1'));
  // Simulate data that has not emitted change yet; pagination still must save it.
  await f.cell(0, 'qty', '10.000001', false); await f.cell(0, 'unit_cost', '', false);
  f.pager.render_next_page();
  assert.equal(`${f.pager.page_index} / ${f.pager.total_pages}`, '2 / 250');
  assert.equal(f.rows[0].product, 'PRODUCT-21');
  await f.cell(0, 'qty', '0'); await f.cell(0, 'unit_cost', '0');
  f.pager.render_prev_page();
  assert.equal(f.rows[0].qty, '10.000001'); assert.equal(f.rows[0].unit_cost, '');
  await f.cell(0, 'unit_cost', '3.50'); // Correct the deliberately blank cost before a valid review.
  const done = f.page.primary.run(), request = f.requests.at(-1);
  assert.equal(request.args.rows.length, 5000);
  assert.deepEqual(request.args.rows.map(entry => entry.row_number), Array.from({length: 5000}, (_, index) => index + 1));
  assert.equal(request.args.rows[20].qty, '0'); assert.equal(request.args.rows[20].unit_cost, '0');
  request.resolve(review({rows: request.args.rows.map(entry => ({...entry, status: 'ready', amount: 0}))}));
  await done;
  f.pager.render_next_page();
  assert.equal(f.page.primary.label, 'stock.opening.submit');
  assert.equal(f.lineForms[0].fields_dict.status_text.value, 'stock.opening.ready');
  assert.equal(f.lineForms[0].fields_dict.status_text.df.hidden, false);
  for (const form of f.lineForms) {
    assert.equal(form.fields_dict.qty.df.read_only, 1);
    assert.equal(form.fields_dict.remove.df.hidden, 1);
  }
  f.form.fields_dict.add_line.df.click(); f.lineForms[0].fields_dict.remove.df.click();
  assert.equal(`${f.pager.page_index} / ${f.pager.total_pages}`, '2 / 250');
  const sent = f.page.primary.run(), posting = f.requests.at(-1);
  assert.deepEqual(posting.args, {...request.args, review_token: 'review-one'});
  posting.resolve({created: 4999, skipped: 1}); await sent;
  assert.equal(f.successes.length, 1);
});

test('invalid review jumps to the first error page and expands its editable line', async () => {
  const f = fixture();
  await importRows(f, Array.from({length: 45}, (_, index) => row({
    product: `PRODUCT-${index + 1}`, product_code: `CODE-${index + 1}`, row_number: index + 2,
  })));
  const pending = f.page.primary.run(), request = f.requests.at(-1);
  request.resolve(review({valid: false, rows: request.args.rows.map(entry => ({...entry,
    status: 'ready', ...(entry.row_number === 43 ? {error: 'Invalid cost'} : {}),
  }))})); await pending;
  assert.equal(`${f.pager.page_index} / ${f.pager.total_pages}`, '3 / 3');
  assert.equal(f.rows.length, 5); assert.equal(f.lineSections[2].collapsed, false);
  assert.equal(f.lineForms[2].fields_dict.status_text.value, 'Invalid cost');
  assert.equal(f.lineForms[2].fields_dict.product_code.df.hidden, false, 'Imported identifiers can be corrected');
  await f.cell(2, 'unit_cost', '12.3456');
  f.pager.render_prev_page();
  assert.equal(f.lineForms[0].fields_dict.status_text.df.hidden, true, 'A correction invalidates reviews on other pages');
  const retry = f.page.primary.run(), latest = f.requests.at(-1);
  assert.equal(latest.args.rows[42].unit_cost, '12.3456'); assert.equal(latest.args.rows.length, 45);
  latest.resolve(review({valid: false, rows: []})); await retry;
});

test('adding opens the last page; deletion reorders unique UI row numbers after Excel import', async () => {
  const f = fixture();
  await importRows(f, Array.from({length: 20}, (_, index) => row({product: `PRODUCT-${index + 1}`, row_number: index + 2})));
  f.form.fields_dict.add_line.df.click();
  assert.equal(`${f.pager.page_index} / ${f.pager.total_pages}`, '2 / 2'); assert.equal(f.lineForms.length, 1);
  f.setRows([row({product: 'MANUAL-21'})]);
  f.pager.render_prev_page();
  f.lineForms[0].fields_dict.remove.df.click();
  assert.equal(`${f.pager.page_index} / ${f.pager.total_pages}`, '1 / 1'); assert.equal(f.lineForms.length, 20);
  assert.equal(f.rows[0].product, 'PRODUCT-2'); assert.equal(f.rows[19].product, 'MANUAL-21');
  const done = f.page.primary.run(), request = f.requests.at(-1);
  assert.equal(new Set(request.args.rows.map(entry => entry.row_number)).size, 20);
  assert.equal(request.args.rows[19].row_number, 20);
  request.resolve(review({valid: false})); await done;
});

test('a product lookup finishing on another page updates the model without losing typed values', async () => {
  const f = fixture();
  await importRows(f, Array.from({length: 21}, (_, index) => row({product: `PRODUCT-${index + 1}`})));
  const done = f.cell(0, 'product', 'NEW-PRODUCT');
  await new Promise(resolve => setImmediate(resolve));
  const lookup = f.requests.at(-1); assert.match(lookup.method, /api_get_product$/);
  await f.cell(0, 'qty', '88'); f.pager.render_next_page();
  lookup.resolve({base_unit: 'box', product_name: '<New coffee>', code: 'NEW-CODE'}); await done;
  f.pager.render_prev_page();
  assert.equal(f.rows[0].unit, 'box'); assert.equal(f.rows[0].qty, '88');
  assert.ok(f.lineSections[0].head.text.includes('<New coffee> · NEW-CODE'));
});

test('failed import matching keeps the original rows just like a failed file parse', async () => {
  const f = fixture(); f.setRows([row()]); f.upload(); f.confirmations[0].yes();
  const done = f.uploaders[0].on_success({file_url: '/private/files/opening.xlsx'});
  f.requests[0].resolve({rows: [row({product: 'NEW'})]});
  await new Promise(resolve => setImmediate(resolve));
  f.requests[1].reject(new Error('Matching failed'));
  await assert.rejects(done, /Matching failed/);
  assert.deepEqual(f.rows, [row()]); assert.equal(f.page.btn_primary.props.disabled, false);
});

test('native asynchronous default callbacks cannot invalidate a successful review or refetch its products', async () => {
  const f = fixture({asyncNativeChanges: true}); await f.flushNativeChanges();
  await importRows(f, Array.from({length: 21}, (_, index) => row({
    product: `PRODUCT-${index + 1}`, product_code: `CODE-${index + 1}`, qty: 10, unit_cost: 25,
  })));
  // Native Data controls represent imported numeric defaults as strings.
  for (const form of f.lineForms) for (const key of ['qty', 'unit_cost'])
    form.fields_dict[key].value = String(form.fields_dict[key].value);
  const importedCalls = f.requests.length; await f.flushNativeChanges();
  assert.equal(f.requests.length, importedCalls, 'Import defaults do not trigger product selection side effects');
  const done = f.page.primary.run(), request = f.requests.at(-1);
  request.resolve(review({rows: request.args.rows.map(entry => ({...entry, status: 'ready', amount: 250}))}));
  await done;
  const reviewedCalls = f.requests.length;
  await f.flushNativeChanges();
  assert.equal(f.page.primary.label, 'stock.opening.submit');
  assert.equal(f.form.fields_dict.total_value.df.hidden, 0);
  assert.equal(f.lineForms[0].fields_dict.amount.df.hidden, false);
  assert.equal(f.lineForms[0].fields_dict.qty.df.read_only, 1);
  f.pager.render_next_page(); await f.flushNativeChanges();
  assert.equal(f.page.primary.label, 'stock.opening.submit');
  assert.equal(f.requests.length, reviewedCalls);
  assert.equal(f.lineForms[0].fields_dict.product_code.value, 'CODE-21');
  assert.equal(f.lineForms[0].fields_dict.amount.df.hidden, false);
});

test('same-value native callbacks retain invalid-review errors but manual edits still invalidate them', async () => {
  const f = fixture({asyncNativeChanges: true}); await f.flushNativeChanges();
  f.setRows([row()]);
  await acceptedPreview(f, review({valid: false, rows: [{row_number: 1, error: 'Invalid cost'}]}));
  const calls = f.requests.length; await f.flushNativeChanges();
  assert.equal(f.requests.length, calls);
  assert.equal(f.lineForms[0].fields_dict.status_text.df.hidden, false);
  assert.equal(f.lineForms[0].fields_dict.status_text.value, 'Invalid cost');
  await f.cell(0, 'unit_cost', '4');
  assert.equal(f.lineForms[0].fields_dict.status_text.df.hidden, 1);
  const product = f.cell(0, 'product', 'PRODUCT-NEW');
  await new Promise(resolve => setImmediate(resolve));
  assert.match(f.requests.at(-1).method, /api_get_product$/);
  f.requests.at(-1).resolve({base_unit: 'box'}); await product;
  assert.equal(f.rows[0].product, 'PRODUCT-NEW'); assert.equal(f.rows[0].unit, 'box');
});

test('manual product validation still runs when another field saved its value before the callback', async () => {
  const f = fixture({asyncNativeChanges: true}); await f.flushNativeChanges();
  await importRows(f, [row({product_code: 'OLD-CODE', barcode: 'OLD-BARCODE'})]);
  await f.flushNativeChanges();
  await f.cell(0, 'product', 'NEW-PRODUCT', false);
  await f.cell(0, 'qty', '19'); // Saves the visible model before Link validation completes.
  const product = f.cell(0, 'product', 'NEW-PRODUCT');
  await new Promise(resolve => setImmediate(resolve));
  const lookup = f.requests.at(-1);
  assert.match(lookup.method, /api_get_product$/); assert.equal(lookup.args.name, 'NEW-PRODUCT');
  lookup.resolve({base_unit: 'box'}); await product;
  assert.equal(f.rows[0].qty, '19'); assert.equal(f.rows[0].unit, 'box');
  assert.equal(f.lineForms[0].fields_dict.product_code.value, '');
  assert.equal(f.lineForms[0].fields_dict.barcode.value, '');
});

test('review reveals the native total section and editing hides the section again', async () => {
  const f = fixture(); f.setRows([row()]);
  const total = f.form.fields_dict.total_value;
  assert.equal(total.section.visible, false);
  await acceptedPreview(f, review({total_value: '87.50'}));
  assert.equal(total.value, '87.50'); assert.equal(total.df.hidden, 0);
  assert.equal(total.section.visible, true, 'The total must be visible through its native section ancestor');
  f.page.secondary.run();
  assert.equal(total.df.hidden, 1); assert.equal(total.section.visible, false);
});

test('native pagination mounts only for the current route and hides itself for a single page', async () => {
  const departed = fixture({delayPagination: true});
  assert.deepEqual(departed.moduleRequests, [paginationURL]);
  departed.depart(); departed.finishPagination(); await departed.ready;
  assert.equal(departed.pager, undefined);
  const f = fixture(); await f.ready;
  assert.equal(f.form.fields_dict.pagination.df.hidden, true);
  assert.equal(f.form.fields_dict.previous_page, undefined);
  assert.equal(f.form.fields_dict.next_page, undefined);
  assert.equal(f.form.fields_dict.page_number, undefined);
  await importRows(f, Array.from({length: 22}, (_, index) => row({product: `PRODUCT-${index + 1}`})));
  assert.equal(f.form.fields_dict.pagination.df.hidden, false);
  assert.equal(f.pager.page_length, 20); assert.equal(f.pager.grid.data.length, 22);
  assert.equal(f.pager.last_page_button.textValue, 'stock.opening.lastPage');
  f.pager.render_prev_page(); assert.equal(f.pager.page_index, 1);
  f.pager.go_to_page(f.pager.total_pages); assert.equal(f.rows[0].product, 'PRODUCT-21');
  f.pager.render_next_page(); assert.equal(f.pager.page_index, 2);
  f.pager.go_to_page(1); assert.equal(f.rows[0].product, 'PRODUCT-1');
  if (nativePaginationSource) {
    f.pager.last_page_button.trigger('click'); assert.equal(f.pager.page_index, 2);
    f.pager.first_page_button.trigger('click'); assert.equal(f.pager.page_index, 1);
    f.pager.$page_number.trigger('focusout', {currentTarget: {value: '99'}});
    assert.equal(f.pager.page_index, 2);
    f.pager.$page_number.trigger('focusout', {currentTarget: {value: '0'}});
    assert.equal(f.pager.page_index, 1);
  }
});

test('native pagination is disabled and captures late events while reviewing or after route departure', async () => {
  const f = fixture();
  await importRows(f, Array.from({length: 22}, (_, index) => row({product: `PRODUCT-${index + 1}`})));
  const pending = f.page.primary.run(), request = f.requests.at(-1);
  assert.equal(f.pager.wrapper.find('button, input').props.disabled, true);
  for (const event of ['click', 'keydown', 'focusout']) {
    assert.equal(f.paginationEvent(event, () => f.pager.render_next_page()).stopped, true);
    assert.equal(f.pager.page_index, 1); assert.equal(f.rows[0].product, 'PRODUCT-1');
  }
  request.resolve(review({rows: request.args.rows.map(entry => ({...entry, status: 'ready'}))})); await pending;
  assert.equal(f.pager.wrapper.find('button, input').props.disabled, false);
  f.paginationEvent('click', () => f.pager.render_next_page());
  assert.equal(f.pager.page_index, 2); assert.equal(f.page.primary.label, 'stock.opening.submit');
  const layouts = f.layoutsMade, scrolls = f.scrolls; f.depart();
  assert.equal(f.paginationEvent('click', () => f.pager.render_prev_page()).stopped, true);
  f.pager.go_to_page(1); // Even a queued native call must not redraw an obsolete page.
  assert.equal(f.layoutsMade, layouts); assert.equal(f.scrolls, scrolls);
});

test('late-loaded native pagination adopts current data and the last-page position without clearing input', async () => {
  const f = fixture({delayPagination: true});
  await importRows(f, Array.from({length: 22}, (_, index) => row({product: `PRODUCT-${index + 1}`})));
  f.form.fields_dict.add_line.df.click();
  await f.cell(2, 'qty', '12.5000');
  f.finishPagination(); await f.ready;
  assert.equal(f.pager.page_index, 2); assert.equal(f.pager.total_pages, 2);
  assert.equal(f.rows[2].qty, '12.5000'); assert.equal(f.lineForms.length, 3);
});

test('invalid native page input preserves the current page and restores its displayed number', async () => {
  const f = fixture();
  await importRows(f, Array.from({length: 22}, (_, index) => row({product: `PRODUCT-${index + 1}`})));
  f.pager.go_to_page(2); await f.cell(0, 'qty', '7.25', false);
  const layouts = f.layoutsMade;
  for (const value of ['abc', '1.5']) {
    if (nativePaginationSource) f.pager.$page_number.trigger('focusout', {currentTarget: {value}});
    else f.pager.go_to_page(value);
    await Promise.resolve();
    assert.equal(f.pager.page_index, 2); assert.equal(String(f.pager.$page_number.val()), '2');
    assert.equal(f.layoutsMade, layouts); assert.equal(f.rows[0].product, 'PRODUCT-21');
    assert.equal(f.rows[0].qty, '7.25');
  }
  f.pager.go_to_page(1); f.pager.go_to_page(2);
  assert.equal(f.rows[0].qty, '7.25');
});
