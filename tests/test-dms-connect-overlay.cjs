// Real connect.js against a fake Desk: no browser, Site, queue, credentials or network.
//
// 这套断言替代了旧的 `test-dms-connect-snapshots.cjs`：那个文件的整个前提是"连接页上
// 有一个手动触发的选择弹窗"（`request_refresh` + 四组勾选）。按用户 2026-09-25 的决定，
// 手动触发已删除，读取只发生在①绑定成功时建缓存底座、②操作动作顺手带回增量。所以这里
// 测的是**新的行为**：白屏覆盖跟着真实进度走、读完只提示一次、空闲不轮询、失败不重试风暴。
// 不是把旧断言删掉换绿灯。
//
// Run: node tests/test-dms-connect-overlay.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(
  path.join(root, 'apps/pearnly_dms/pearnly_dms/public/desk/connect.js'), 'utf8');
const glossaryPath = path.join(root, 'apps/pearnly_dms/pearnly_dms/i18n/messages.tsv');
const glossary = fs.readFileSync(glossaryPath, 'utf8').trim().split('\n')
  .map(line => line.split('\t')).filter(columns => columns.length >= 5);
const zh = Object.fromEntries(glossary.map(columns => [columns[4], columns[1]]));
const allText = Object.fromEntries(glossary.map(columns => [columns[4], columns.slice(0, 4)]));
const groups = ['vehicles', 'customers', 'company', 'geo'];
const ID = 'a'.repeat(32);
const clone = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));
const row = (dataset, state = 'idle', extra = {}) => ({
  dataset, state, request_id: '', progress: {processed: 0, total: null}, count: null,
  as_of: 0, completed_at: 0, stale: false, error_code: '', ...extra,
});

// 页面用 jQuery 拼账号行：替身只需要"插进去 / 移除"。
function element() {
  return {removed: false, appendTo() {return this;}, remove() {this.removed = true;}};
}

function harness({connected = true, rows = null, accounts = null, reading = 'reading'} = {}) {
  const calls = [], timers = new Map(), messages = [], successes = [], freezeTexts = [];
  const buttons = [], handlers = {}, menu = [], dialogs = [], layouts = [];
  let serverAccounts = clone(accounts || {read: null, write: []});
  let accountReply = {ok: true};
  let secondary = null, secondaryCleared = 0;
  let dialogSubmit = null;
  let savedConnection = false;
  let serial = 0, frozen = false, freezeCount = 0, unfreezeCount = 0, freezeMessage = '';
  let statusError = null;
  let serverRows = clone(rows || groups.map(name => row(name)));
  const page = {
    main: {empty() {}}, clear_inner_toolbar() {},
    set_primary_action(label, fn) {primaryAction = fn; primaryLabel = label;},
    set_secondary_action(label, fn) {secondary = {label, fn};},
    clear_secondary_action() {secondary = null; secondaryCleared++;},
    clear_menu() {menu.length = 0;},
    add_menu_item(label, fn) {menu.push({label, fn});},
    set_indicator() {},
    add_inner_button(label) {buttons.push(label);},
  };
  let primaryAction = null, primaryLabel = '';
  const wrapper = {};
  // 必须和 `p.texts` 的真实签名一致：它**总是**替换 `{n}`，不带参数时就换成空串。
  // 老替身只做拼接，于是「{0}/{1} 组」在测试里通过、在线上变成「/ 组」——2026-09-25 实际踩到。
  const t = (key, ...args) => (zh[key] || key)
    .replace(/\{(\d+)\}/g, (_, i) => String(args[Number(i)] ?? ''));
  const p = {
    async texts(prefix) {assert(['dms.connect.cache.', 'dms.connect.acct.'].includes(prefix), prefix); return t;},
    async call(method, args = {}, type = 'GET') {
      calls.push({method, args: clone(args), type});
      if (method.endsWith('get_connection')) {
        return {connected, endpoint: connected
          ? {name: 'PRIMARY-LOCAL', enabled: 1, username: 'synthetic', has_admin_creds: false}
          : null};
      }
      if (method.endsWith('get_status')) {
        if (statusError) throw statusError;
        return {configured: true, datasets: clone(serverRows)};
      }
      if (method.endsWith('test_credentials')) return {ok: true, masters: {car_models: [1]}};
      if (method.endsWith('save_connection')) {savedConnection = true; return {ok: true, reading};}
      if (method.endsWith('get_accounts')) return clone(serverAccounts);
      if (method.endsWith('set_read_account') || method.endsWith('save_write_accounts')) return clone(accountReply);
      throw new Error('Unexpected connection call: ' + method);
    },
    dialog(title, fields, action, submit) {
      dialogSubmit = submit;
      const dialog = {title, fields, action, submit, fields_dict: {rows: {$wrapper: element()}}};
      dialogs.push(dialog);
      return dialog;
    },
    layout(parent, fields) {layouts.push(fields); return {fields: [], values: {}, set_value() {}};},
    escape: value => String(value),
    success(value) {successes.push(value);},
    tidy_number() {},
  };
  const controls = [];
  const frappe = {
    pearnly: p, boot: {lang: 'zh'},
    ui: {form: {make_control({df}) {
      const control = {df, value: '', set_value(v) {this.value = v;}, get_value() {return this.value;}, refresh() {}};
      controls.push(control);
      return control;
    }}},
    dom: {
      freeze(value) {frozen = true; freezeCount++; freezeTexts.push(value); freezeMessage = value;},
      unfreeze() {frozen = false; unfreezeCount++;},
    },
    msgprint(value) {messages.push(value);},
    confirm() {throw new Error('The connection page must not mutate anything on its own');},
  };
  const context = vm.createContext({
    frappe, p, __: text => text, console,
    $: value => (value === wrapper
      ? {on(event, fn) {handlers[event] = fn;}}
      : element()),
    // 只还原我们真正用的那一个选择器：原生覆盖的正文节点。
    document: {querySelector(selector) {
      assert.equal(selector, '#freeze .freeze-message p.lead');
      return {set textContent(value) {freezeMessage = value;}, get textContent() {return freezeMessage;}};
    }},
    setTimeout(fn, ms) {assert(ms > 0 && ms <= 10000); const id = ++serial; timers.set(id, fn); return id;},
    clearTimeout(id) {timers.delete(id);},
  });
  vm.runInContext(source, context);
  return {
    calls, buttons, messages, successes, freezeTexts, handlers,
    get savedConnection() {return savedConnection;},
    menu, dialogs, layouts, controls,
    primary() {assert(primaryAction, 'a primary action must exist'); primaryAction();},
    get primaryLabel() {return primaryLabel;}, get secondary() {return secondary;},
    get secondaryCleared() {return secondaryCleared;},
    setAccountReply(value) {accountReply = clone(value);},
    // 已连上时换连接账号在「…」菜单里；没连上时右上角唯一的按钮就是「连接」。
    async connect(values = {username: 'synthetic', password: 'synthetic'}) {
      const edit = menu.find(item => item.label === 'connect.action.edit');
      const open = connected ? edit?.fn : primaryAction;
      assert(open, 'the connection dialog must be reachable');
      open();
      await tick();
      assert(dialogSubmit, 'the connection dialog must have been opened');
      await dialogSubmit(values, {set_value() {}, hide() {}});
      await tick();
    },
    get timers() {return timers;},
    get frozen() {return frozen;}, get freezeCount() {return freezeCount;},
    get unfreezeCount() {return unfreezeCount;}, get freezeMessage() {return freezeMessage;},
    setRows(value) {serverRows = clone(value);},
    failStatus() {statusError = new Error('synthetic denied');},
    count(method) {return calls.filter(call => call.method.endsWith(method)).length;},
    start: () => p.connect(page, t, wrapper),
    async poll() {
      assert.equal(timers.size, 1, 'exactly one poll may be scheduled');
      const [id, fn] = timers.entries().next().value;
      timers.delete(id);
      await fn();
      await tick();
    },
  };
}

test('idle connection page has no manual trigger and stops polling after one status read', async () => {
  const h = harness();
  await h.start();
  assert.equal(h.count('get_status'), 1, 'idle page reads status once');
  assert.equal(h.calls.filter(call => call.type === 'POST').length, 0);
  assert.equal(h.timers.size, 0, 'nothing is being read, so nothing keeps polling');
  assert.equal(h.frozen, false);
  assert.equal(h.buttons.length, 0,
    'the toolbar keeps a single action: no inner "test" or "disable" buttons');
  assert(!h.buttons.includes(zh['dms.connect.cache.read']),
    'the "update data" button is gone: reads happen on bind and on operator action');
  assert(!h.buttons.some(label => /update|更新/i.test(label)));
});

test('a successful re-bind shows only the reading overlay, not the old probe notice', async () => {
  const h = harness({rows: [
    row('vehicles', 'queued', {request_id: ID, progress: {processed: 0, total: null}}),
    row('customers', 'queued', {request_id: ID, progress: {processed: 0, total: null}}),
    row('company', 'idle'), row('geo', 'idle'),
  ]});
  await h.start();
  await h.connect();
  assert.equal(h.savedConnection, true, 'the credentials were saved');
  assert.equal(h.frozen, true, 'the reading overlay is up');
  assert.equal(h.successes.length, 0,
    'no "connection ok · counts" toast: it used to collide with the reading overlay');
  assert.match(h.freezeMessage, /0\/4 组 · 00:00$/, 'the overlay carries live figures from the first frame');
});

test('binding shows the native mask with live group, record and elapsed figures', async () => {
  const h = harness({rows: [
    row('vehicles', 'running', {request_id: ID, progress: {processed: 12, total: 29}}),
    row('customers', 'queued', {request_id: ID, progress: {processed: 0, total: null}}),
    row('company', 'idle'), row('geo', 'idle'),
  ]});
  await h.start();
  assert.equal(h.frozen, true, 'a running read masks the page');
  // 一组都还没读完 → 0/4；车辆组已经读到 12 条，所以数字从第一帧就不是死的。
  assert.match(h.freezeTexts[0], /0\/4 组 · 12 条 · 00:00$/,
    'the mask carries live figures, not a group list');
  assert(!/车辆|客户|公司|地址/.test(h.freezeTexts[0]),
    'the group breakdown is replaced by the live figures');
  h.setRows([
    row('vehicles', 'running', {request_id: ID, progress: {processed: 25, total: 29}}),
    row('customers', 'running', {request_id: ID, progress: {processed: 4, total: 40}}),
    row('company', 'idle'), row('geo', 'idle'),
  ]);
  await h.poll();
  assert.equal(h.frozen, true);
  assert.equal(h.freezeCount, 1, 'the mask is reused, not re-created every frame');
  // 12 → 25+4 条：条数一直在动，管理员不用猜它是不是卡住了。
  assert.match(h.freezeMessage, /0\/4 组 · 29 条/, 'the visible figures keep moving');
});

test('the group count never goes backwards and a full read is never claimed early', async () => {
  const h = harness({rows: [
    row('vehicles', 'running', {request_id: ID, progress: {processed: 28, total: 29}}),
    row('customers', 'running', {request_id: ID, progress: {processed: 39, total: 40}}),
    row('company', 'succeeded', {request_id: ID, count: 27, as_of: 1}),
    row('geo', 'running', {request_id: ID, progress: {processed: 15000, total: 15729}}),
  ]});
  await h.start();
  // 四组里只有公司组读完 → 1/4，离"读完了"还差得远。
  assert.match(h.freezeMessage, /1\/4 组 · 15,067 条/,
    'one finished group out of four must not read as done');
  h.setRows([
    row('vehicles', 'queued', {request_id: ID, progress: {processed: 0, total: null}}),
    row('customers', 'running', {request_id: ID, progress: {processed: 5, total: 40}}),
    row('company', 'running', {request_id: ID, progress: {processed: 1, total: 27}}),
    row('geo', 'idle'),
  ]);
  await h.poll();
  assert.match(h.freezeMessage, /1\/4 组/, 'a poll that looks smaller must not rewind the group count');
});

test('a finished bootstrap releases the mask and reports success exactly once', async () => {
  const h = harness({rows: [
    row('vehicles', 'running', {request_id: ID, progress: {processed: 1, total: 2}}),
    row('customers', 'succeeded', {request_id: ID, count: 46, as_of: 1}),
    row('company', 'succeeded', {request_id: ID, count: 27, as_of: 1}),
    row('geo', 'idle'),
  ]});
  await h.start();
  assert.equal(h.frozen, true);
  h.setRows(groups.map((name, index) => row(name, 'succeeded',
    {request_id: ID, count: 10 + index, as_of: 1})));
  await h.poll();
  assert.equal(h.frozen, false, 'mask is released as soon as nothing is left to read');
  assert.equal(h.successes.length, 1, 'success is announced once, not per poll');
  assert.equal(h.timers.size, 0, 'a finished read stops the poll');
});

test('a failed group releases the mask, reports red once and does not retry in a loop', async () => {
  const h = harness({rows: [
    row('vehicles', 'running', {request_id: ID, progress: {processed: 3, total: 9}}),
    row('customers', 'idle'), row('company', 'idle'), row('geo', 'idle'),
  ]});
  await h.start();
  h.setRows([
    row('vehicles', 'succeeded', {request_id: ID, count: 29, as_of: 1}),
    row('customers', 'failed', {request_id: ID, error_code: 'ERR_DMS_UNREACHABLE'}),
    row('company', 'succeeded', {request_id: ID, count: 27, as_of: 1}),
    row('geo', 'failed', {request_id: ID, error_code: 'ERR_DMS_CONCURRENT_LOGIN'}),
  ]);
  await h.poll();
  assert.equal(h.frozen, false);
  assert.equal(h.messages.length, 1, 'one failure notice, not one per poll');
  assert.equal(h.messages[0].indicator, 'red');
  assert.equal(h.timers.size, 0, 'no retry storm from the page; the backend owns retries');
});

test('a status transport failure releases the mask and says the result is unconfirmed', async () => {
  const h = harness({rows: [
    row('vehicles', 'running', {request_id: ID, progress: {processed: 1, total: 2}}),
    row('customers', 'idle'), row('company', 'idle'), row('geo', 'idle'),
  ]});
  await h.start();
  assert.equal(h.frozen, true);
  h.failStatus();
  await h.poll();
  assert.equal(h.frozen, false, 'a failed status read must not leave the native page frozen');
  assert.match(h.messages.at(-1), /无法确认|unconfirmed/i);
  assert.equal(h.timers.size, 0, 'the page stops asking after an unconfirmed status');
});

test('leaving releases the mask and returning resumes the live progress', async () => {
  const h = harness({rows: [
    row('vehicles', 'running', {request_id: ID, progress: {processed: 5, total: 9}}),
    row('customers', 'idle'), row('company', 'idle'), row('geo', 'idle'),
  ]});
  await h.start();
  assert.equal(h.frozen, true);
  h.handlers.hide();
  assert.equal(h.frozen, false, 'navigating away always releases the native mask');
  h.setRows([
    row('vehicles', 'running', {request_id: ID, progress: {processed: 7, total: 9}}),
    row('customers', 'idle'), row('company', 'idle'), row('geo', 'idle'),
  ]);
  h.handlers.show();
  await tick();
  await tick();
  assert.equal(h.frozen, true, 'coming back to an in-flight read shows the mask again');
  // 离开前 5 条，回来时 7 条：条数跟着真实读取走，不是记住的旧数字。
  assert.match(h.freezeMessage, /0\/4 组 · 7 条/);
});

test('the removed manual trigger leaves no glossary text promising a button', () => {
  for (const [key, value] of Object.entries(allText)) {
    if (!key.startsWith('dms.connect.cache.state.')) continue;
    for (const text of value) {
      assert(!/点击更新数据|click Update data|กดอัปเดตข้อมูล|「データを更新」を押/.test(text),
        `${key} still tells the admin to press a button that no longer exists: ${text}`);
    }
  }
  for (const key of ['dms.connect.cache.processing', 'dms.connect.cache.dataset.vehicles',
    'dms.connect.cache.dataset.customers', 'dms.connect.cache.dataset.company',
    'dms.connect.cache.dataset.geo', 'dms.connect.cache.state.succeeded',
    'dms.connect.cache.state.failed', 'dms.connect.cache.state.waiting']) {
    assert(allText[key], `${key} must exist in the glossary`);
    assert.equal(allText[key].filter(Boolean).length, 4, `${key} needs all four languages`);
  }
});

test('connected page keeps exactly two buttons: write accounts and the read account', async () => {
  const h = harness();
  await h.start();
  assert.equal(h.primaryLabel, zh['dms.connect.acct.writeAdd']);
  assert.equal(h.secondary.label, zh['dms.connect.acct.readAdd']);
  assert.deepEqual(clone(h.menu.map(item => item.label)), ['connect.action.edit'],
    'changing the connection account moved into the page menu');
  assert.equal(h.buttons.length, 0, 'no inner toolbar buttons');
  const fields = h.layouts.at(-1);
  assert.equal(fields.find(f => f.fieldname === 'read').default, zh['dms.connect.acct.readNone']);
  assert.equal(fields.find(f => f.fieldname === 'write').default, zh['dms.connect.acct.writeNone']);
});

test('a saved read account turns the button into "change" and shows its name', async () => {
  const h = harness({accounts: {read: {name: 'R1', username: 'reader'}, write: [
    {name: 'W1', username: 'w1'}, {name: 'W2', username: 'w2'}]}});
  await h.start();
  assert.equal(h.secondary.label, zh['dms.connect.acct.readChange']);
  const fields = h.layouts.at(-1);
  assert.equal(fields.find(f => f.fieldname === 'read').default, 'reader');
  assert.equal(fields.find(f => f.fieldname === 'write').default, 'w1, w2');
});

test('not connected: only the connect button, no account buttons', async () => {
  const h = harness({connected: false});
  await h.start();
  assert.equal(h.secondary, null);
  assert(h.secondaryCleared > 0);
  assert.equal(h.menu.length, 0);
  assert.equal(h.count('get_accounts'), 0);
});

test('read account dialog saves through the probe and names missing rights', async () => {
  const h = harness();
  await h.start();
  h.setAccountReply({ok: false, username: 'reader', missing: ['events', 'customer']});
  h.secondary.fn();
  const dialog = h.dialogs.at(-1);
  assert.equal(dialog.action, zh['dms.connect.acct.testAndSave']);
  await dialog.submit({username: 'reader', password: 'x'}, {set_value() {}, hide() {}});
  const call = h.calls.find(c => c.method.endsWith('set_read_account'));
  assert.equal(call.type, 'POST');
  assert.match(h.messages.at(-1).message, new RegExp(`${zh['dms.connect.acct.check.events']}.*${zh['dms.connect.acct.check.customer']}`));
  h.setAccountReply({ok: false, username: 'reader', missing: [],
    error_friendly: {th: 'ไทย', zh: '登录失败', en: 'failed', ja: '失敗'}});
  await dialog.submit({username: 'reader', password: 'bad'}, {set_value() {}, hide() {}});
  assert.equal(h.messages.at(-1).message, '登录失败', 'the four-language reply shows in the page language');
  h.setAccountReply({ok: true});
  let hidden = false;
  await dialog.submit({username: 'reader', password: 'x'}, {set_value() {}, hide() {hidden = true;}});
  assert(hidden);
  assert.equal(h.successes.at(-1), zh['dms.connect.acct.saved']);
});

test('write accounts dialog: one account/password row by default, add and remove rows', async () => {
  const fresh = harness();
  await fresh.start();
  fresh.primary();
  const types = () => fresh.controls.map(c => c.df.fieldtype);
  assert.deepEqual(clone(types()), ['Data', 'Password', 'Button'], 'one row: account, password, remove');
  const dialog = fresh.dialogs.at(-1);
  assert.equal(dialog.fields.find(f => f.fieldname === 'add').label, zh['dms.connect.acct.addRow']);
  assert(!dialog.fields.some(f => f.fieldtype === 'Table'), 'no grid: its password column shows plain text');
  dialog.fields.find(f => f.fieldname === 'add').click();
  assert.equal(fresh.controls.filter(c => c.df.fieldtype === 'Password').length, 2);

  const h = harness({accounts: {read: null, write: [{name: 'W1', username: 'w1'}, {name: 'W2', username: 'w2'}]}});
  await h.start();
  h.primary();
  const users = h.controls.filter(c => c.df.fieldtype === 'Data');
  assert.deepEqual(clone(users.map(c => [c.value, c.df.read_only])), [['w1', 1], ['w2', 1]],
    'saved accounts come back read-only and without a password');
  h.controls.filter(c => c.df.fieldtype === 'Button')[1].df.click();  // remove w2
  h.dialogs.at(-1).fields.find(f => f.fieldname === 'add').click();
  const added = h.controls.filter(c => c.df.fieldtype === 'Data').at(-1);
  added.set_value(' w3 ');
  h.controls.filter(c => c.df.fieldtype === 'Password').at(-1).set_value('p3');
  let hidden = false;
  await h.dialogs.at(-1).submit({}, {set_value() {}, hide() {hidden = true;}});
  const call = h.calls.find(c => c.method.endsWith('save_write_accounts'));
  assert.deepEqual(JSON.parse(call.args.accounts), [
    {name: 'W1', username: 'w1', password: ''}, {name: '', username: 'w3', password: 'p3'}]);
  assert(hidden);
});

test('the page lists address, connection, read and write accounts only', async () => {
  const h = harness({accounts: {read: {name: 'R1', username: 'reader'}, write: []}});
  await h.start();
  const fields = h.layouts.at(-1).filter(f => f.fieldtype === 'Data');
  assert.deepEqual(clone(fields.map(f => f.fieldname)), ['url', 'account', 'read', 'write']);
  assert.equal(fields[1].label, zh['dms.connect.acct.connectionField']);
});

test('a usable customer group stops masking the page while its details keep reading', async () => {
  const usable = {processed: 120, total: 15155, usable: true};
  const h = harness({rows: [
    row('geo', 'succeeded', {request_id: ID, count: 15729, as_of: 1}),
    row('customers', 'running', {request_id: ID, progress: usable}),
    row('vehicles', 'running', {request_id: ID, progress: {processed: 3, total: 31}}),
    row('company', 'succeeded', {request_id: ID, count: 27, as_of: 1}),
  ]});
  await h.start();
  assert.equal(h.frozen, true, 'vehicles are still needed before LINE can book');
  h.setRows([
    row('geo', 'succeeded', {request_id: ID, count: 15729, as_of: 1}),
    row('customers', 'running', {request_id: ID, progress: usable}),
    row('vehicles', 'succeeded', {request_id: ID, count: 31, as_of: 1}),
    row('company', 'succeeded', {request_id: ID, count: 27, as_of: 1}),
  ]);
  await h.poll();
  assert.equal(h.frozen, false, 'customer details reading in the background must not mask the page');
  assert.equal(h.successes.length + h.messages.length, 0, 'nothing is announced before the last group finishes');
  assert.equal(h.timers.size, 1, 'the page keeps following the background read');
  h.setRows(groups.map((name, index) => row(name, 'succeeded', {request_id: ID, count: 10 + index, as_of: 1})));
  await h.poll();
  assert.equal(h.successes.length, 1, 'success once the customer details are done');
});

test('binding without a read account says reading starts after adding it', async () => {
  const h = harness({connected: false, reading: 'awaiting_read_account'});
  await h.start();
  await h.connect();
  assert.equal(h.messages.at(-1), zh['dms.connect.acct.readFirst']);
});
