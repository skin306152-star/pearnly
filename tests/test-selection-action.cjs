// 共享的"选中即出现操作"helper：走真实 list.js，用假的 page/list/call。
// 断言的是一条行为链：选中 → 按钮出现 → 确认 → 批量调用 → 清空选择并刷新；
// 以及两条边界：失败要保留选择（让人改完再试）、有不合格的行就禁用（别让人白点）。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(
  'apps/pearnly_core/pearnly_core/public/desk/list.js', 'utf8');

function boot({ checked = [], fail = false, when, reason, empty } = {}) {
  const calls = [], notices = [], confirms = [];
  const state = { rows: [...checked], reloads: 0, cleared: 0 };
  const button = { props: {}, titles: [], prop(key, value) { this.props[key] = value; return this; },
    attr(key, value) { if (key === "title") this.titles.push(value); return this; } };
  const primary = { label: null, action: null };
  const page = {
    set_primary_action(label, action) { primary.label = label; primary.action = action; },
    clear_primary_action() { primary.label = null; primary.action = null; },
    btn_primary: button,
  };
  const list = () => ({
    get_checked_items: () => state.rows,
    clear_checked_items() { state.cleared += 1; state.rows = []; },
    async reload() { state.reloads += 1; },
  });
  const p = { escape: String, success: text => notices.push(text) };
  const frappe = { confirm(message, run) {
    const dialog = { onhide: null }; confirms.push({ message, run, dialog }); return dialog;
  } };
  // list.js 里的类继承官方 BaseList，并抄它的原型方法；这里只需要它"存在"。
  class BaseList {}
  vm.runInNewContext(source, { frappe: { ...frappe, pearnly: p,
    views: { BaseList, ListView: { prototype: {} } } }, window: {} });
  const on_selection = p.selection_action({
    page, list, when, reason, empty,
    label: rows => `作废选中的 ${rows.length} 张`,
    confirm: rows => `确定作废 ${rows.length} 张？`,
    run: async rows => {
      if (fail) throw Object.assign(new Error("server said no"), { _server_messages: "[]" });
      for (const row of rows) calls.push(row.name);
    },
    success: rows => `已作废 ${rows.length} 张`,
  });
  return { on_selection, page, primary, button, state, calls, notices, confirms, p };
}

const flush = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  // 没有选中：右上角不该有多余按钮。
  const blank = boot({ checked: [] });
  blank.on_selection([]);
  assert.equal(blank.primary.label, null);
  // 页面本来就有主按钮的（商品页"新建商品"）：清空选择时要把那颗恢复回来。
  const restoreEnv = boot({ checked: [],
    empty: () => restoreEnv.page.set_primary_action('新建商品', () => {}) });
  restoreEnv.on_selection([]);
  assert.equal(restoreEnv.primary.label, '新建商品');
  console.log('PASS nothing selected leaves the page action alone');

  // 选中两行：按钮出现、标签带数量；确认后逐张调用、清空选择、刷新列表。
  const env = boot({ checked: [{ name: 'A-1' }, { name: 'A-2' }] });
  env.on_selection(env.state.rows);
  assert.equal(env.primary.label, '作废选中的 2 张');
  assert.equal(env.button.props.disabled, false);
  await env.primary.action();
  assert.equal(env.confirms.length, 1);
  assert.equal(env.confirms[0].message, '确定作废 2 张？');
  await env.confirms[0].run();
  await flush();
  assert.deepEqual(env.calls, ['A-1', 'A-2']);
  assert.equal(env.state.cleared, 1);
  assert.equal(env.state.reloads, 1);
  assert.deepEqual(env.notices, ['已作废 2 张']);
  env.on_selection(env.state.rows);
  assert.equal(env.primary.label, null, '清空选择后按钮要收回去');
  console.log('PASS selection shows the action label, confirms, runs and refreshes');

  // 失败：选择留着，按钮恢复可点（服务端已经把原因说过，不再刷一遍）。
  const failing = boot({ checked: [{ name: 'B-1' }], fail: true });
  failing.on_selection(failing.state.rows);
  await failing.primary.action();
  await failing.confirms[0].run();
  await flush();
  assert.equal(failing.state.rows.length, 1, '失败后选择必须留着');
  assert.equal(failing.state.reloads, 0);
  assert.equal(failing.button.props.disabled, false, '失败后要能再点一次');
  console.log('PASS a failed batch keeps the selection so it can be retried');

  // 选中里有不合格的行（例如已作废的单据）：按钮禁用，title 说明原因。
  const gated = boot({ checked: [{ name: 'C-1', status: 'submitted' }, { name: 'C-2', status: 'void' }],
    when: row => row.status === 'submitted',
    reason: row => `${row.name} 已经是作废状态` });
  gated.on_selection(gated.state.rows);
  assert.equal(gated.primary.label, '作废选中的 2 张');
  assert.equal(gated.button.props.disabled, true);
  assert.equal(gated.button.titles.at(-1), 'C-2 已经是作废状态');
  await gated.primary.action();
  assert.equal(gated.confirms.length, 0, '禁用的按钮不该走到确认框');
  gated.state.rows = [gated.state.rows[0]];
  gated.on_selection(gated.state.rows);
  assert.equal(gated.button.props.disabled, false);
  console.log('PASS ineligible rows disable the action and explain why in the title');
})().catch(error => { console.error(error); process.exitCode = 1; });
