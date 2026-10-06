// Cowork 录入列表的「状态」列（2026-10-02）：推送三态 + 仅识别的票「已识别」；识别后的需复核原因不在列表显示。
// 跑真实的 intake.js，框架与产品壳换成离线替身，不连站点、不写数据。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('apps/pearnly_cowork/pearnly_cowork/public/desk/intake.js', 'utf8');

(async () => {
  let options;
  const list = {ready: Promise.resolve(), data: [], reload: async () => {}, destroy() {}};
  // 原生 Page 的 add_field（推送状态下拉，2026-10-07）：替身记下字段定义，值默认「全部」。
  const fields = {};
  const page = {set_title() {}, set_primary_action() {}, clear_primary_action() {},
    add_field(df) { fields[df.fieldname] = df; return {get_value: () => df.default || '', set_value() {}}; }};
  const p = {
    intake_targets: {RECOGNIZE_ONLY: 'none'},
    intake_push_reason: reason => `why:${reason}`,
    escape: String, money: String, breadcrumbs() {}, subroute: root => root, clear_page() {}, add_refresh() {},
    selection_action: () => () => {}, filter_values: () => ({}), live: () => ({stop() {}}),
    list: opts => { options = opts; return list; },
    routed_page: ({render}) => () => render([], () => true),
  };
  const frappe = {pearnly: p, model: {can_write: () => true}, set_route() {}, session: {user: 'u'}};
  vm.runInNewContext(source, {frappe, __: String, console});
  await p.intake(page, key => key, {});

  // 推送状态筛选：四个选项、默认全部，选中的值原样交给 workbench。
  assert.equal(JSON.stringify(fields.push_state.options.map(row => row.value)), JSON.stringify(['', 'failed', 'pending', 'success']));
  assert.equal(fields.push_state.default, '');

  const column = options.columns.find(col => col.fieldname === 'last_push_status');
  assert.equal(column.label, 'intake.col.status');
  const show = row => [column.format(row.last_push_status, row), column.cell_class(row.last_push_status, row),
    column.title(row.last_push_status, row)];
  const reasons = JSON.stringify([{code: 'tax_checksum', field: 'buyer_tax'}]);

  assert.deepEqual(show({last_push_status: 'pending'}).slice(0, 2), ['intake.push.pending', 'indicator-pill yellow']);
  assert.deepEqual(show({last_push_status: 'success', intake_target: 'none'}).slice(0, 2),
    ['connect.records.success', 'indicator-pill green']);
  assert.deepEqual(show({last_push_status: 'manual', last_push_reason: 'X'}),
    ['connect.records.failed', 'indicator-pill red', 'why:X']);
  assert.deepEqual(show({last_push_reason: 'target_unavailable', intake_target: 'express'}),
    ['connect.records.failed', 'indicator-pill red', 'why:target_unavailable']);
  // 仅识别（识别后导出，不推送）：蓝色「已识别」
  assert.deepEqual(show({intake_target: 'none', review_reasons: reasons}),
    ['intake.status.recognized', 'indicator-pill blue', '']);
  // 推送类还没推的票：有需复核原因也不显示（原来是橙色「待复核」）
  assert.deepEqual(show({intake_target: 'express', review_reasons: reasons}), ['', '', '']);
  assert.deepEqual(show({review_reasons: reasons}), ['', '', '']);
  console.log('intake status column: 7 cases PASS');
})().catch(error => { console.error(error); process.exit(1); });
