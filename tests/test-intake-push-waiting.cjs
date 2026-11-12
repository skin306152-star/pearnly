// Cowork 录入：推送在等 Express 关闭（服务端读的时候算的只读字段 `push_waiting`，2026-11-12）。
// 有人开着这本账套的 Express 时网页只显示「录入中」，看不出在等什么：状态仍是「录入中」，列表的悬浮说明与详情的官方
// 提示行显示现成那句 `intake.error.ACCOUNT_BUSY_LOCKED`。跑真实的 intake-upload.js / intake.js，框架换成离线替身，不连站点。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const desk = path.join(__dirname, '../apps/pearnly_cowork/pearnly_cowork/public/desk');
const upload = fs.readFileSync(path.join(desk, 'intake-upload.js'), 'utf8');
const listing = fs.readFileSync(path.join(desk, 'intake.js'), 'utf8');
const SENTENCE = 'Express 正在使用此账套，关闭后将自动推送';
const __ = key => (key === 'intake.error.ACCOUNT_BUSY_LOCKED' ? SENTENCE : key);

function helpers() {
  const p = {subroute: root => root};
  vm.runInNewContext(upload, {frappe: {pearnly: p, router: {}}, window: {history: {}, location: {}}, __,
    $: () => ({on() {}}), document: {}, crypto: {randomUUID: () => 'x'}, URLSearchParams});
  return p;
}

test('在等 Express 关闭的票给出现成那句，别的票不给', () => {
  const p = helpers();
  assert.equal(p.intake_push_waiting({last_push_status: 'pending', push_waiting: 'express_open'}), SENTENCE);
  assert.equal(p.intake_push_waiting({last_push_status: 'pending'}), '');
  assert.equal(p.intake_push_waiting({last_push_status: 'pending', push_waiting: 'other'}), '');
  // 票已经不是录入中（写成、转人工）：不再说在等
  assert.equal(p.intake_push_waiting({last_push_status: 'manual', push_waiting: 'express_open'}), '');
  assert.equal(p.intake_push_waiting(null), '');
});

test('列表：状态仍是黄色「录入中」，悬浮说明是那一句；失败的照旧显示失败原因', async () => {
  const real = helpers();
  let options;
  const list = {ready: Promise.resolve(), data: [], reload: async () => {}, destroy() {}};
  const page = {set_title() {}, set_primary_action() {}, clear_primary_action() {},
    add_field(df) { return {get_value: () => df.default || '', set_value() {}}; }};
  const p = {
    intake_targets: {RECOGNIZE_ONLY: 'none'},
    intake_push_reason: reason => `why:${reason}`, intake_push_waiting: real.intake_push_waiting,
    escape: String, money: String, breadcrumbs() {}, subroute: root => root, clear_page() {}, add_refresh() {},
    selection_action: () => () => {}, filter_values: () => ({}), live: () => ({stop() {}}),
    list: opts => { options = opts; return list; },
    routed_page: ({render}) => () => render([], () => true),
  };
  vm.runInNewContext(listing, {frappe: {pearnly: p, model: {can_write: () => true}, set_route() {}, session: {user: 'u'}},
    __, console});
  await p.intake(page, key => key, {});
  const column = options.columns.find(col => col.fieldname === 'last_push_status');
  const show = row => [column.format(row.last_push_status, row), column.cell_class(row.last_push_status, row),
    column.title(row.last_push_status, row)];
  assert.deepEqual(show({last_push_status: 'pending', push_waiting: 'express_open'}),
    ['intake.push.pending', 'indicator-pill yellow', SENTENCE]);
  assert.deepEqual(show({last_push_status: 'manual', last_push_reason: 'X'}),
    ['connect.records.failed', 'indicator-pill red', 'why:X']);
  // 列表接口为这一页补字段：不另发请求
  assert.match(listing, /load: \(\{start, page_length, sort_by, sort_order\}\) => p\.call\(API \+ "workbench"/);
});

test('详情：在等的票出一行官方蓝色提示行（form-message blue），文字是那一句', () => {
  const body = upload.slice(upload.indexOf('const show_notice = record =>'), upload.indexOf('const PUSH_TARGETS'));
  assert.match(body, /const waiting = p\.intake_push_waiting\(record\);/);
  assert.match(body, /if \(waiting\) \$\("<div class='form-message blue'>"\)\.text\(waiting\)\.appendTo\(wrapper\);/);
  // 不新增词条：这一句只从现成的词条键取
  assert.equal(upload.match(/intake\.error\.ACCOUNT_BUSY_LOCKED/g).length, 1);
  assert.equal(/ACCOUNT_BUSY_LOCKED/.test(listing), false);
});
