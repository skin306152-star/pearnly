// Real connect.js + product.js Dialog helper; native UI/HTTP/timer adapters only.
// No browser, Site, queues, credentials or network. Run: node tests/test-dms-connect-snapshots.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
// Repository checkout locally; task lab mounts packages under its native bench/apps.
const appRoot = [path.join(root, 'apps'), path.join(process.cwd(), 'apps')].find(candidate =>
  fs.existsSync(path.join(candidate, 'pearnly_dms/pearnly_dms/public/desk/connect.js')) &&
  fs.existsSync(path.join(candidate, 'pearnly_core/pearnly_core/public/desk/product.js')));
assert(appRoot, 'Both task-owned DMS and Core sources must be readable');
const source = fs.readFileSync(path.join(appRoot, 'pearnly_dms/pearnly_dms/public/desk/connect.js'), 'utf8');
const product = fs.readFileSync(path.join(appRoot, 'pearnly_core/pearnly_core/public/desk/product.js'), 'utf8');
// Keep the production duplicate-submit guard, not a test imitation of that guard.
const dialogStart = product.indexOf('\tp.dialog = ');
const dialogEnd = product.indexOf('\tp.layout = ', dialogStart);
assert(dialogStart >= 0 && dialogEnd > dialogStart, 'Native Dialog helper must remain discoverable');
const dialogSource = product.slice(dialogStart, dialogEnd);
const clone = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));
const DATASETS = ['vehicles', 'customers', 'geo', 'company'];
const ID = 'a'.repeat(32), OLD_ID = 'b'.repeat(32);
const status = (dataset, state = 'idle', request_id = '', extra = {}) => ({
  dataset, state, request_id, progress: {processed: 0, total: null},
  count: null, as_of: 0, completed_at: 0, stale: false, error_code: '', ...extra,
});
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

function harness({connected = true, endpoint = true, rows = DATASETS.map(name => status(name)), messagesByKey = {}} = {}) {
  const calls = [], forms = [], dialogs = [], handlers = {}, timers = new Map();
  const buttons = new Map(), messages = [], successes = [], errors = [], freezeTexts = [];
  let serial = 0, frozen = false, freezeCount = 0, unfreezeCount = 0;
  let statusError = null, statusWait = null, requestError = null, requestWait = null, requestRows = null;
  let serverRows = clone(rows);
  const page = {main: {empty() {}}, clear_inner_toolbar() {buttons.clear();},
    set_primary_action() {}, set_indicator() {}, add_inner_button(label, fn) {buttons.set(label, fn);}};
  const wrapper = {};
  const t = (key, ...args) => (messagesByKey[key] || key) + (args.length ? ':' + args.join('|') : '');
  const p = {
    async texts(prefix) {assert.equal(prefix, 'dms.connect.cache.'); return t;},
    async call(method, args = {}, type = 'GET') {
      calls.push({method, args: clone(args), type});
      if (method.endsWith('get_connection')) return {connected, endpoint: endpoint ? {
        name: 'PREVIEW-ONLY', enabled: connected, username: 'synthetic', has_admin_creds: false,
      } : null};
      if (method.endsWith('get_status')) {
        if (statusError) throw statusError;
        if (statusWait) {const wait = statusWait; statusWait = null; return await wait.promise;}
        return {configured: true, datasets: clone(serverRows)};
      }
      if (method.endsWith('request_refresh')) {
        if (requestError) throw requestError;
        if (requestWait) {const wait = requestWait; requestWait = null; return await wait.promise;}
        const accepted = requestRows || args.datasets.map(name => status(name, 'queued', ID));
        for (const row of accepted) serverRows = serverRows.filter(old => old.dataset !== row.dataset).concat(clone(row));
        return {configured: true, datasets: clone(accepted)};
      }
      throw new Error('Unexpected connection/transport call: ' + method);
    },
    layout(parent, fields) {
      const form = {fields, values: {}, set_value(key, value) {this.values[key] = value;}};
      forms.push(form); return form;
    },
    escape: value => String(value).replaceAll('<', '&lt;'),
    success(value) {successes.push(value);}, tidy_number() {},
  };
  const frappe = {pearnly: p, boot: {lang: 'zh'},
    dom: {freeze(value) {frozen = true; freezeCount++; freezeTexts.push(value);},
      unfreeze() {frozen = false; unfreezeCount++;}},
    ui: {Dialog: class {
      constructor(options) {
        this.options = options; this.fields = options.fields; this.hidden = true;
        this.disabled = false; this.fields_dict = {}; this.$wrapper = {addClass() {}};
        dialogs.push(this);
      }
      show() {this.hidden = false;}
      hide() {this.hidden = true;}
      disable_primary_action() {this.disabled = true;}
      enable_primary_action() {this.disabled = false;}
      submit(values) {return this.options.primary_action(values);}
    }},
    msgprint(value) {messages.push(value);},
    confirm() {throw new Error('Unexpected connection mutation');},
  };
  const context = vm.createContext({frappe, p, __: text => text,
    console: {error: error => errors.push(error)},
    $: value => ({on(event, fn) {assert.equal(value, wrapper); handlers[event] = fn;}}),
    setTimeout(fn, ms) {assert(ms > 0 && ms <= 10000); const id = ++serial; timers.set(id, fn); return id;},
    clearTimeout(id) {timers.delete(id);},
  });
  vm.runInContext(dialogSource, context);
  vm.runInContext(source, context);
  return {
    calls, forms, dialogs, handlers, timers, buttons, messages, successes, errors, freezeTexts,
    get frozen() {return frozen;}, get freezeCount() {return freezeCount;},
    get unfreezeCount() {return unfreezeCount;},
    setRows(value) {serverRows = clone(value);},
    setRequestRows(value) {requestRows = value;},
    failStatus() {statusError = new Error('synthetic denied');},
    failRequest() {requestError = new Error('synthetic request failure');},
    deferStatus() {statusWait = deferred(); return statusWait;},
    deferRequest() {requestWait = deferred(); return requestWait;},
    start: () => p.connect(page, t, wrapper),
    choose() {buttons.get(t('dms.connect.cache.read'))(); return dialogs.at(-1);},
    count(method) {return calls.filter(call => call.method.endsWith(method)).length;},
    async poll() {
      assert.equal(timers.size, 1, 'Exactly one active poll is scheduled');
      const [id, fn] = timers.entries().next().value; timers.delete(id); await fn(); await tick();
    },
  };
}

test('idle page has no saved-data fields or ongoing polling; first selection is explicit', async () => {
  const h = harness(); await h.start();
  assert.equal(h.forms.length, 1, 'Only existing connection details remain on the page');
  const names = h.forms.flatMap(form => form.fields.map(field => field.fieldname).filter(Boolean));
  assert(!names.some(name => /^(masters|vehicles|customers|geo|company)_/.test(name)), 'No data status/count/time/error fields');
  assert.equal(h.timers.size, 0); assert.equal(h.frozen, false);
  assert.equal(h.calls.filter(call => call.type === 'POST').length, 0);
  const dialog = h.choose();
  assert.deepEqual(Array.from(dialog.fields.filter(field => field.fieldtype === 'Check'), field => [field.fieldname, field.default]),
    [['vehicles', 1], ['customers', 1], ['geo', 1], ['company', 1]]);
  await dialog.submit({});
  assert.equal(h.messages.length, 1); assert.equal(dialog.hidden, false);
  assert.equal(h.count('request_refresh'), 0); assert.equal(h.frozen, false);
});

test('Chinese glossary renders four exact group labels, update action and native freeze text', async () => {
  const glossary = fs.readFileSync(path.join(appRoot, 'pearnly_dms/pearnly_dms/i18n/messages.tsv'), 'utf8');
  const rows = glossary.trim().split(/\r?\n/).map(line => line.split('\t'));
  const header = rows.shift(), keyIndex = header.indexOf('key'), zhIndex = header.indexOf('zh');
  assert(keyIndex >= 0 && zhIndex >= 0, 'Read product-owned glossary columns by name');
  const messagesByKey = Object.fromEntries(rows.map(row => [row[keyIndex], row[zhIndex]]));
  const h = harness({messagesByKey}); await h.start();
  assert(h.buttons.has('更新数据'));
  const dialog = h.choose();
  assert.equal(dialog.options.title, '更新数据');
  assert.equal(dialog.options.primary_action_label, '更新数据');
  assert.deepEqual(Array.from(dialog.fields.filter(field => field.fieldtype === 'Check'), field => [field.fieldname, field.label]), [
    ['vehicles', '车辆主档信息'], ['customers', '客户资料'], ['geo', '地址目录'], ['company', '公司资料'],
  ]);
  await dialog.submit({vehicles: 1, customers: 1, geo: 1, company: 1});
  assert.deepEqual(h.calls.find(call => call.method.endsWith('request_refresh')).args, {datasets: DATASETS});
  assert.deepEqual(h.freezeTexts, ['数据更新中...']);
  assert.equal(h.frozen, true);
  h.setRows(DATASETS.map(dataset => status(dataset, 'succeeded', ID))); await h.poll();
  assert.equal(h.frozen, false); assert.equal(h.successes.length, 1); assert.equal(h.timers.size, 0);
});

test('company can be selected alone and obsolete or forged groups never enter the request', async () => {
  const h = harness(); await h.start();
  await h.choose().submit({company: 1, vehicles: 0, customers: 0, geo: 0, masters: 1, unknown: 1});
  assert.deepEqual(h.calls.find(call => call.method.endsWith('request_refresh')).args, {datasets: ['company']});
  h.setRows([status('company', 'succeeded', ID)]); await h.poll();
  assert.equal(h.frozen, false); assert.equal(h.successes.length, 1); assert.equal(h.timers.size, 0);
});

test('subset request excludes forged endpoint; actual Dialog helper suppresses repeated submit', async () => {
  const h = harness(); await h.start(); const dialog = h.choose();
  const pending = h.deferRequest();
  const first = dialog.submit({customers: 1, geo: 0, endpoint: 'forged'});
  await tick(); assert.equal(dialog.disabled, true);
  h.choose(); assert.equal(h.dialogs.length, 1, 'Posting cannot open another request dialog');
  await dialog.submit({vehicles: 1});
  assert.equal(h.count('request_refresh'), 1);
  assert.deepEqual(h.calls.find(call => call.method.endsWith('request_refresh')), {
    method: 'pearnly_dms.connections.snapshots.request_refresh', args: {datasets: ['customers']}, type: 'POST',
  });
  h.setRows([status('customers', 'queued', ID)]);
  pending.resolve({datasets: [status('customers', 'queued', ID)]}); await first;
  assert.equal(dialog.hidden, true); assert.equal(dialog.disabled, false);
  assert.equal(h.frozen, true); assert.equal(h.timers.size, 1); assert.equal(h.successes.length, 0);
  assert.deepEqual(h.freezeTexts, ['dms.connect.cache.processing']);
  h.choose(); assert.equal(h.dialogs.length, 1, 'Active read cannot open another request dialog');
  await dialog.submit({vehicles: 1}); assert.equal(h.count('request_refresh'), 1);
  h.setRows([status('customers', 'succeeded', ID)]); await h.poll();
  assert.equal(h.frozen, false); assert.equal(h.timers.size, 0); assert.equal(h.successes.length, 1);
  assert.deepEqual(h.successes, ['dms.connect.cache.state.succeeded']);
  assert(h.freezeCount >= 1 && h.unfreezeCount >= 1);
});

test('cached data defaults to no selection; only all matching current requests may report success', async () => {
  const h = harness({rows: [status('vehicles', 'succeeded', OLD_ID, {as_of: 1720000000})]});
  await h.start(); assert.equal(h.timers.size, 0);
  const dialog = h.choose();
  assert(dialog.fields.filter(field => field.fieldtype === 'Check').every(field => field.default === 0));
  await dialog.submit({vehicles: 1, customers: 1});
  h.setRows([status('vehicles', 'succeeded', ID), status('customers', 'running', ID)]);
  await h.poll(); assert.equal(h.frozen, true); assert.equal(h.successes.length, 0);
  h.setRows([status('vehicles', 'succeeded', ID), status('customers', 'succeeded', ID)]);
  await h.poll(); assert.equal(h.frozen, false); assert.equal(h.successes.length, 1);
  assert.equal(h.timers.size, 0);
});

for (const state of ['failed', 'waiting']) {
  test(state + ' is terminal feedback, never success, and releases the page', async () => {
    const h = harness(); await h.start(); await h.choose().submit({geo: 1});
    h.setRows([status('geo', state, ID, {error_code: 'ERR_DMS_SNAPSHOT_BUSY'})]);
    await h.poll();
    assert.equal(h.frozen, false); assert.equal(h.timers.size, 0);
    assert.equal(h.successes.length, 0); assert.equal(h.messages.length, 1);
    assert.equal(h.messages[0].message, 'dms.connect.cache.state.' + state);
  });
}

test('request-level failure with no request_id is failure feedback, not an empty-set success', async () => {
  const h = harness(); await h.start();
  h.setRequestRows([status('customers', 'failed', '', {error_code: 'ERR_DMS_SNAPSHOT_INTERRUPTED'})]);
  await h.choose().submit({customers: 1});
  assert.equal(h.frozen, false); assert.equal(h.timers.size, 0);
  assert.equal(h.successes.length, 0); assert.equal(h.messages.length, 1);
  assert.equal(h.messages[0].message, 'dms.connect.cache.state.failed');
});

for (const rows of [[status('vehicles', 'succeeded', OLD_ID)], [status('vehicles')], []]) {
  test('changed/missing request cannot turn into success: ' + JSON.stringify(rows.map(row => row.state)), async () => {
    const h = harness(); await h.start(); await h.choose().submit({vehicles: 1});
    h.setRows(rows); await h.poll();
    assert.equal(h.frozen, false); assert.equal(h.timers.size, 0);
    assert.equal(h.successes.length, 0); assert.equal(h.messages.length, 1);
    assert.equal(h.messages[0].message, 'dms.connect.cache.changed');
  });
}

test('RPC request failure releases mask and native Dialog submit button', async () => {
  const h = harness(); await h.start(); h.failRequest(); const dialog = h.choose();
  await dialog.submit({vehicles: 1});
  assert.equal(h.frozen, false); assert.equal(dialog.disabled, false);
  assert.equal(h.timers.size, 0); assert.equal(h.successes.length, 0);
  assert.equal(h.messages[0].message, 'dms.connect.cache.unconfirmed');
});

test('poll failure releases mask and stops retry', async () => {
  const h = harness(); await h.start(); await h.choose().submit({vehicles: 1});
  h.failStatus(); await h.poll();
  assert.equal(h.frozen, false); assert.equal(h.timers.size, 0); assert.equal(h.successes.length, 0);
  assert.equal(h.messages[0].message, 'dms.connect.cache.unconfirmed');
});

test('a failed group is remembered while another selected group is still running', async () => {
  const h = harness(); await h.start(); await h.choose().submit({vehicles: 1, geo: 1});
  h.setRows([status('vehicles', 'failed', ID), status('geo', 'running', ID)]); await h.poll();
  assert.equal(h.frozen, true); assert.equal(h.successes.length, 0); assert.equal(h.messages.length, 0);
  h.setRows([status('vehicles', 'failed', ID), status('geo', 'succeeded', ID)]); await h.poll();
  assert.equal(h.frozen, false); assert.equal(h.timers.size, 0);
  assert.equal(h.successes.length, 0); assert.equal(h.messages.length, 1);
  assert.match(JSON.stringify(h.messages[0]), /state\.failed/);
});

test('failure takes priority over waiting regardless of selected group order', async () => {
  for (const states of [['failed', 'waiting'], ['waiting', 'failed']]) {
    const h = harness(); await h.start(); await h.choose().submit({vehicles: 1, customers: 1, geo: 1});
    h.setRows([status('vehicles', states[0], ID), status('customers', states[1], ID), status('geo', 'running', ID)]);
    await h.poll(); assert.equal(h.frozen, true); assert.equal(h.messages.length, 0);
    h.setRows([status('vehicles', states[0], ID), status('customers', states[1], ID), status('geo', 'succeeded', ID)]);
    await h.poll();
    assert.equal(h.frozen, false); assert.equal(h.successes.length, 0); assert.equal(h.timers.size, 0);
    assert.equal(h.messages[0].message, 'dms.connect.cache.state.failed');
  }
});

test('leaving releases mask; returning reads current active tasks and resumes one poll', async () => {
  const h = harness(); await h.start(); await h.choose().submit({vehicles: 1});
  const before = h.count('get_status'); h.handlers.hide();
  assert.equal(h.frozen, false); assert.equal(h.timers.size, 0); assert.equal(h.count('get_status'), before);
  h.handlers.show(); await tick();
  assert.equal(h.count('get_status'), before + 1);
  assert.equal(h.frozen, true); assert.equal(h.timers.size, 1);
  h.setRows([status('vehicles', 'succeeded', ID)]); await h.poll();
  assert.equal(h.successes.length, 1); assert.equal(h.frozen, false);
});

test('a result delivered after hide is ignored and cannot show success or freeze a hidden page', async () => {
  const h = harness(); await h.start(); await h.choose().submit({customers: 1});
  const pending = h.deferStatus(), polling = h.poll(); await tick();
  h.handlers.hide();
  pending.resolve({datasets: [status('customers', 'succeeded', ID)]}); await polling;
  assert.equal(h.frozen, false); assert.equal(h.timers.size, 0); assert.equal(h.successes.length, 0);
  h.setRows([status('customers', 'running', ID)]); h.handlers.show(); await tick();
  assert.equal(h.frozen, true); assert.equal(h.timers.size, 1);
});

test('old in-flight response cannot finish a new show generation or cancel its poll', async () => {
  const h = harness(); await h.start(); await h.choose().submit({customers: 1});
  const old = h.deferStatus(), polling = h.poll(); await tick();
  h.handlers.hide(); h.setRows([status('customers', 'running', ID)]);
  h.handlers.show(); await tick();
  assert.equal(h.frozen, true); assert.equal(h.timers.size, 1);
  old.resolve({datasets: [status('customers', 'succeeded', ID)]}); await polling;
  assert.equal(h.frozen, true); assert.equal(h.timers.size, 1); assert.equal(h.successes.length, 0);
  h.setRows([status('customers', 'succeeded', ID)]); await h.poll();
  assert.equal(h.frozen, false); assert.equal(h.successes.length, 1);
});

test('a POST accepted after leaving does not mask a different page; returning may resume it', async () => {
  const h = harness(); await h.start(); const dialog = h.choose(), pending = h.deferRequest();
  const submitting = dialog.submit({geo: 1}); await tick(); h.handlers.hide();
  h.setRows([status('geo', 'queued', ID)]);
  pending.resolve({datasets: [status('geo', 'queued', ID)]}); await submitting;
  assert.equal(h.frozen, false); assert.equal(h.timers.size, 0); assert.equal(h.successes.length, 0);
  h.handlers.show(); await tick(); assert.equal(h.frozen, true); assert.equal(h.timers.size, 1);
});

test('initial active task resumes; idle and disconnected pages never keep polling', async () => {
  const active = harness({rows: [status('geo', 'running', ID)]}); await active.start();
  assert.equal(active.frozen, true); assert.equal(active.timers.size, 1);
  active.handlers.hide(); assert.equal(active.frozen, false);
  for (const options of [{connected: false}, {connected: false, endpoint: false}]) {
    const h = harness(options); await h.start();
    assert.equal(h.buttons.has('dms.connect.cache.read'), false);
    assert.equal(h.count('get_status'), 0); assert.equal(h.count('request_refresh'), 0);
    assert.equal(h.frozen, false); assert.equal(h.timers.size, 0);
  }
});
