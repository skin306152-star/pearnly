// Real connect.js against a fake Desk: no browser, Site, queue, credentials or network.
//
// 连接页只管两种账号（用户 2026-09-27）：读档账号（就是连 DMS 的账号）与改写权限账号。
// 读资料全在后台：页面不遮挡、不轮询、不播报读取进度（"那个白屏进度不要吧"）。
//
// Run: node tests/test-dms-connect-page.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(
  path.join(root, 'apps/pearnly_dms/pearnly_dms/public/desk/connect.js'), 'utf8');
const glossary = fs.readFileSync(path.join(root, 'apps/pearnly_dms/pearnly_dms/i18n/messages.tsv'), 'utf8')
  .trim().split('\n').map(line => line.split('\t')).filter(columns => columns.length >= 5);
const zh = Object.fromEntries(glossary.map(columns => [columns[4], columns[1]]));
const clone = value => JSON.parse(JSON.stringify(value));

// 页面用 jQuery 拼账号行：替身只需要"插进去 / 移除"。
function element() {
  return {removed: false, appendTo() {return this;}, remove() {this.removed = true;}};
}

function harness({connected = true, accounts = null} = {}) {
  const calls = [], messages = [], successes = [], menu = [], dialogs = [], layouts = [], controls = [];
  const serverAccounts = clone(accounts || {read: null, write: []});
  let reply = {ok: true}, check = null, primary = null, primaryLabel = '', secondary = null, secondaryCleared = 0, frozen = 0;
  const page = {
    main: {empty() {}}, clear_inner_toolbar() {}, set_indicator() {},
    set_primary_action(label, fn) {primary = fn; primaryLabel = label;},
    set_secondary_action(label, fn) {secondary = {label, fn};},
    clear_secondary_action() {secondary = null; secondaryCleared++;},
    clear_menu() {menu.length = 0;},
    add_menu_item(label, fn) {menu.push({label, fn});},
  };
  const t = (key, ...args) => (zh[key] || key).replace(/\{(\d+)\}/g, (_, i) => String(args[Number(i)] ?? ''));
  const p = {
    async texts(prefix) {assert.equal(prefix, 'dms.connect.acct.'); return t;},
    async call(method, args = {}, type = 'GET') {
      calls.push({method, args: clone(args), type});
      if (method.endsWith('get_connection')) {
        return {connected, endpoint: connected ? {name: 'PRIMARY-LOCAL', system_url: 'https://dms.example.test'} : null};
      }
      if (method.endsWith('get_accounts')) return clone(serverAccounts);
      // 试账号在后台跑（2026-10-02）：发起只回排队号，结果按号问 account_check。
      if (method.endsWith('set_read_account') || method.endsWith('save_write_accounts')) return {queued: args.check_key};
      if (method.endsWith('account_check')) return clone(check === null ? {state: 'done', result: reply} : check);
      throw new Error('Unexpected call: ' + method);
    },
    dialog(title, fields, action, submit) {
      const dialog = {title, fields, action, submit, fields_dict: {rows: {$wrapper: element()}}};
      dialogs.push(dialog);
      return dialog;
    },
    layout(parent, fields) {layouts.push(fields); return {};},
    escape: value => String(value),
    success(value) {successes.push(value);},
  };
  const frappe = {
    pearnly: p, boot: {lang: 'zh'},
    ui: {form: {make_control({df}) {
      const control = {df, value: '', set_value(v) {this.value = v;}, get_value() {return this.value;}, refresh() {}};
      controls.push(control);
      return control;
    }}},
    dom: {freeze() {frozen++;}, unfreeze() {}},
    msgprint(value) {messages.push(value);},
  };
  const context = vm.createContext({
    frappe, p, __: text => text, console, $: () => element(),
    setTimeout() {throw new Error('the connection page must not poll');},
  });
  vm.runInContext(source, context);
  return {
    calls, messages, successes, menu, dialogs, layouts, controls,
    get primaryLabel() {return primaryLabel;}, get secondary() {return secondary;},
    get secondaryCleared() {return secondaryCleared;}, get frozen() {return frozen;},
    primary() {assert(primary, 'a primary action must exist'); primary();},
    setReply(value) {reply = clone(value);},
    setCheck(value) {check = value === null ? null : clone(value);},
    count(method) {return calls.filter(call => call.method.endsWith(method)).length;},
    start: () => p.connect(page, t, {}),
  };
}

test('connected page: two buttons, three rows, no mask or reading status', async () => {
  const h = harness();
  await h.start();
  assert.equal(h.primaryLabel, zh['dms.connect.acct.writeAdd']);
  assert.equal(h.secondary.label, zh['dms.connect.acct.readAdd']);
  assert.equal(h.menu.length, 0, 'no separate connection account');
  const fields = h.layouts.at(-1).filter(f => f.fieldtype === 'Data');
  assert.deepEqual(clone(fields.map(f => f.fieldname)), ['url', 'read', 'write']);
  assert.equal(fields[1].default, zh['dms.connect.acct.readNone']);
  assert.equal(fields[2].default, zh['dms.connect.acct.writeNone']);
  assert.equal(h.frozen, 0, 'reading runs in the background: no white mask');
  assert.equal(h.count('get_status'), 0, 'the page does not follow the background read');
});

test('a saved read account turns the button into "change" and shows the names', async () => {
  const h = harness({accounts: {read: {name: 'R1', username: 'reader'},
    write: [{name: 'W1', username: 'w1'}, {name: 'W2', username: 'w2'}]}});
  await h.start();
  assert.equal(h.secondary.label, zh['dms.connect.acct.readChange']);
  const fields = h.layouts.at(-1);
  assert.equal(fields.find(f => f.fieldname === 'read').default, 'reader');
  assert.equal(fields.find(f => f.fieldname === 'write').default, 'w1, w2');
});

test('not connected: the read account is the only button and saving it is the connection', async () => {
  const h = harness({connected: false});
  await h.start();
  assert.equal(h.primaryLabel, zh['dms.connect.acct.readAdd']);
  assert.equal(h.secondary, null);
  assert(h.secondaryCleared > 0);
  assert.equal(h.count('get_accounts'), 0);
  h.primary();
  await h.dialogs.at(-1).submit({username: 'reader', password: 'x'}, {set_value() {}, hide() {}});
  const call = h.calls.find(c => c.method.endsWith('set_read_account'));
  assert.match(call.args.check_key, /^[A-Za-z0-9_-]{8,64}$/);
  assert.deepEqual({...call.args, check_key: undefined}, {username: 'reader', password: 'x', check_key: undefined});
  const asked = h.calls.find(c => c.method.endsWith('account_check'));
  assert.deepEqual(asked.args, {check_key: call.args.check_key}, 'the status call carries only the key, never the password');
  assert.equal(call.type, 'POST');
  assert.equal(h.count('save_connection') + h.count('test_credentials'), 0);
  assert.equal(h.successes.at(-1), zh['dms.connect.acct.saved']);
  assert.equal(h.frozen, 0);
});

test('read account failures name the missing rights or show the reply in the page language', async () => {
  const h = harness();
  await h.start();
  h.setReply({ok: false, username: 'reader', missing: ['events', 'customer']});
  h.secondary.fn();
  const dialog = h.dialogs.at(-1);
  assert.equal(dialog.action, zh['dms.connect.acct.testAndSave']);
  await dialog.submit({username: 'reader', password: 'x'}, {set_value() {}, hide() {}});
  assert.match(h.messages.at(-1).message,
    new RegExp(`${zh['dms.connect.acct.check.events']}.*${zh['dms.connect.acct.check.customer']}`));
  h.setReply({ok: false, username: 'reader', missing: [], error_friendly: {th: 'ไทย', zh: '登录失败', en: 'failed'}});
  await dialog.submit({username: 'reader', password: 'bad'}, {set_value() {}, hide() {}});
  assert.equal(h.messages.at(-1).message, '登录失败');
});

test('write accounts: one account/password row by default, add and remove rows', async () => {
  const fresh = harness();
  await fresh.start();
  fresh.primary();
  assert.deepEqual(clone(fresh.controls.map(c => c.df.fieldtype)), ['Data', 'Password', 'Button']);
  const dialog = fresh.dialogs.at(-1);
  assert.equal(dialog.fields.find(f => f.fieldname === 'add').label, zh['dms.connect.acct.addRow']);
  assert(!dialog.fields.some(f => f.fieldtype === 'Table'), 'no grid: its password column shows plain text');
  dialog.fields.find(f => f.fieldname === 'add').click();
  assert.equal(fresh.controls.filter(c => c.df.fieldtype === 'Password').length, 2);

  const h = harness({accounts: {read: null, write: [{name: 'W1', username: 'w1'}, {name: 'W2', username: 'w2'}]}});
  await h.start();
  h.primary();
  assert.deepEqual(clone(h.controls.filter(c => c.df.fieldtype === 'Data').map(c => [c.value, c.df.read_only])),
    [['w1', 1], ['w2', 1]], 'saved accounts come back read-only and without a password');
  h.controls.filter(c => c.df.fieldtype === 'Button')[1].df.click();  // remove w2
  h.dialogs.at(-1).fields.find(f => f.fieldname === 'add').click();
  h.controls.filter(c => c.df.fieldtype === 'Data').at(-1).set_value(' w3 ');
  h.controls.filter(c => c.df.fieldtype === 'Password').at(-1).set_value('p3');
  let hidden = false;
  await h.dialogs.at(-1).submit({}, {set_value() {}, hide() {hidden = true;}});
  const call = h.calls.find(c => c.method.endsWith('save_write_accounts'));
  assert.deepEqual(JSON.parse(call.args.accounts), [
    {name: 'W1', username: 'w1', password: ''}, {name: '', username: 'w3', password: 'p3'}]);
  assert(hidden);
});

test('a check that fails in the background shows the server reason once and keeps the dialog open', async () => {
  const h = harness();
  await h.start();
  h.setCheck({state: 'failed', message: '账号已被操作员使用'});
  h.secondary.fn();
  let hidden = false;
  await h.dialogs.at(-1).submit({username: 'reader', password: 'x'}, {set_value() {}, hide() {hidden = true;}});
  assert.equal(h.messages.at(-1).message, '账号已被操作员使用');
  assert.equal(hidden, false);
  assert.equal(h.successes.length, 0);
});

