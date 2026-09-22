// 入库/出库的交互：走真实 stock-entry.js，用假的 Page/Dialog/List。
//
// 用户 2026-09-22 口径：两级都只有一颗「提交」，提交即过账（服务端 `submit=1`），
// 提交完回列表 —— 不停在弹窗、也不停在编辑页；仓库是**自由填写**，不再是官方选择器。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(
  'apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/stock-entry.js', 'utf8');

function element() {
  const node = {
    handlers: {},
    addClass() { return node; }, append() { return node; }, remove() { return node; },
    on(event, handler) { node.handlers[event] = handler; return node; },
    off() { node.handlers = {}; return node; }, find() { return node; },
    prop() { return node; }, attr() { return node; }, text() { return node; },
    empty() { return node; }, appendTo() { return node; }, detach() { return node; },
  };
  return node;
}

// `route` = [] 时停在列表，['new'] 时直接进二级页面。
function boot({ direction = 'in', route = [], doc = null, can_write = true } = {}) {
  const calls = [], dialogs = [], routes = [], successes = [];
  let list_options, list_reloads = 0, primary = null, form_values = null;

  const p = {
    escape: value => String(value ?? ""),
    number: value => String(value),
    money: value => String(value),
    success: message => successes.push(message),
    call: async (method, args) => {
      calls.push({ method, args });
      if (String(method).endsWith("api_next_stock_entry_number")) {
        return { doc_no: direction === "in" ? "IR2569-000009" : "IS2569-000009" };
      }
      if (String(method).endsWith("api_get_stock_entry")) return doc;
      return { name: "STE-1", doc_no: "IR2569-000010", status: "submitted" };
    },
    dialog: (title, fields, label, action) => {
      const by_name = {};
      for (const field of fields) if (field.fieldname) by_name[field.fieldname] = { df: field, $wrapper: element(), $input: element() };
      const dialog = { title, fields, label, action, by_name, hidden: false,
        $wrapper: { find: () => element() }, hide() { dialog.hidden = true; },
        get_field: name => by_name[name], get_values: () => form_values };
      dialogs.push(dialog);
      return dialog;
    },
    list: options => {
      list_options = options;
      return { ready: Promise.resolve(), reload: async () => { list_reloads += 1; },
        get_checked_items: () => [], clear_checked_items() {} };
    },
    selection_action: () => () => {},
    add_refresh: () => element(),
    subroute: (root, ...parts) => "/desk/" + [root, ...parts].filter(Boolean).join("/"),
    routed_page: ({ render }) => async () => { await render([...route]); },
    clear_page: () => {}, breadcrumbs: () => {}, edit_all: () => {},
    redirect_up: (...parts) => { routes.push(parts); },
    layout: (_parent, fields) => {
      const by_name = {};
      for (const field of fields) if (field.fieldname) by_name[field.fieldname] = { df: field };
      return { fields_dict: by_name, get_values: () => form_values, set_value() {} };
    },
    trade: {
      lines: (_t, options) => ({ fieldtype: "Table", fieldname: "lines", label: "trade.lines", options }),
      clean: lines => (lines || []).filter(row => row && row.product),
      load: (api, method, total, query) => async ({ start, page_length }) => ({ items: [], total: 0 }),
      act: async (_dialog, callback) => { await callback(); },
    },
  };
  const page = {
    add_field: () => element(), add_inner_button() {},
    set_primary_action: (label, action) => { primary = { label, action }; },
    clear_primary_action: () => { primary = null; },
  };
  const frappe = {
    pearnly: p, model: { can_write: () => can_write },
    datetime: { get_today: () => "2026-09-22" },
    get_route: () => ["pearnly-stock-" + direction, ...route],
    set_route: (...parts) => routes.push(parts),
    confirm: () => {}, msgprint: () => {},
  };
  vm.runInNewContext(source, { frappe, __: key => key, $: () => element() });
  return { calls, dialogs, routes, successes, page,
    get primary() { return primary; },
    get values() { return form_values; },
    set values(values) { form_values = values; },
    get list_options() { return list_options; },
    get list_reloads() { return list_reloads; },
    async ready() { await p.stockEntry(page, key => key, direction, element()); } };
}

const call_names = env => env.calls.map(call => call.method.split(".").pop());
const the_call = (env, name) => env.calls.filter(call => call.method.endsWith(name)).at(-1);
// 页面那颗主按钮是 `() => void submit_new()`（原生 Page 的动作不接 promise），点完要让
// 微任务跑干净再断言，不然断言会跑在提交完成之前。
async function settle(env) {
  let seen = -1;
  for (let i = 0; i < 500; i += 1) {
    await Promise.resolve();
    const now = env.calls.length + env.routes.length + env.successes.length;
    if (now === seen && i > 10) return;
    seen = now;
  }
}

(async () => {
  // 1) 列表 → 弹窗：单据号是预览出来的只读值，仓库是自由填写的 Data，底部是「提交」。
  const env = boot({ direction: "in" });
  await env.ready();
  assert.equal(env.primary.label, "trade.newIn");
  await env.primary.action();
  const dialog = env.dialogs.at(-1);
  assert.equal(dialog.label, "trade.action.submit", "主按钮叫提交，不叫保存草稿");
  const warehouse = dialog.fields.find(field => field.fieldname === "warehouse");
  assert.equal(warehouse.fieldtype, "Data", "仓库是自由填写，不是官方 Link 选择器");
  assert.equal(warehouse.reqd, 1);
  const doc_no = dialog.fields.find(field => field.fieldname === "doc_no");
  assert.equal(doc_no.default, "IR2569-000009", "新建时显示当前要发的单据号");
  assert.equal(doc_no.read_only, 1);
  assert.ok(!dialog.fields.some(field => field.fieldname === "submit"), "不再有第二颗提交按钮");
  console.log("PASS the dialog shows the number, takes a free-text warehouse and submits");

  // 2) 提交：一次调用就建单 + 过账，关掉弹窗、刷新列表，**不再打开编辑器**。
  env.values = { posting_date: "2026-09-22", warehouse: "验收仓库", reason: "n",
    lines: [{ product: "PRD-00001", qty: 2 }] };
  await dialog.action(env.values, dialog);
  const created = the_call(env, "api_create_stock_entry");
  assert.equal(created.args.submit, 1, "建单即过账");
  assert.equal(created.args.direction, "in");
  assert.equal(created.args.warehouse, "验收仓库", "仓库按自由填写的名字发给服务端");
  assert.equal(created.args.lines.length, 1);
  assert.equal(dialog.hidden, true, "提交完弹窗关掉");
  assert.equal(env.list_reloads, 1, "列表跟着刷新");
  assert.equal(call_names(env).filter(name => name === "api_get_stock_entry").length, 0,
    "提交后不该再把单据打开停留在弹窗里");
  assert.equal(env.successes.at(-1), "trade.notice.submitted");
  console.log("PASS submitting posts the entry and returns to the list without reopening it");

  // 3) 老数据里的草稿：打开后那颗按钮同样把它提交掉（走更新接口，仍然 submit=1）。
  const draft = boot({ direction: "out",
    doc: { name: "STE-9", status: "draft", doc_no: "IS2569-000002", warehouse: "WH-00002",
      posting_date: "2026-09-22", lines: [{ product: "PRD-00001", qty: 1 }] } });
  await draft.ready();
  await draft.list_options.on_open({ name: "STE-9" });
  const draft_dialog = draft.dialogs.at(-1);
  assert.equal(draft_dialog.label, "trade.action.submit");
  draft.values = { posting_date: "2026-09-22", warehouse: "WH-00002", lines: [{ product: "PRD-00001", qty: 1 }] };
  await draft_dialog.action(draft.values, draft_dialog);
  const updated = the_call(draft, "api_update_stock_entry");
  assert.equal(updated.args.name, "STE-9");
  assert.equal(updated.args.submit, 1, "草稿也是提交即过账");
  assert.equal(draft_dialog.hidden, true);
  console.log("PASS a legacy draft is submitted in one step from the same dialog");

  // 4) 已提交的单据：只读（按钮是关闭），也不再挂「全屏编辑」。
  const done = boot({ direction: "in",
    doc: { name: "STE-8", status: "submitted", doc_no: "IR-25690922-0008", warehouse: "WH-00002",
      posting_date: "2026-09-22", lines: [] } });
  await done.ready();
  await done.list_options.on_open({ name: "STE-8" });
  assert.equal(done.dialogs.at(-1).label, "trade.close");
  assert.equal(call_names(done).filter(name => name === "api_create_stock_entry").length, 0);
  console.log("PASS a submitted entry opens read-only");

  // 5) 二级页面：同一套字段，主按钮「提交」；提交后回列表。
  const full = boot({ direction: "out", route: ["new"] });
  await full.ready();
  assert.equal(full.primary.label, "trade.action.submit");
  full.values = { posting_date: "2026-09-22", warehouse: "自由填写仓",
    lines: [{ product: "PRD-00001", qty: 1 }] };
  await full.primary.action();
  await settle(full);
  const page_call = the_call(full, "api_create_stock_entry");
  assert.equal(page_call.args.submit, 1);
  assert.equal(page_call.args.direction, "out");
  assert.deepEqual(full.routes.at(-1), ["pearnly-stock-out"], "提交后回列表页");
  assert.equal(full.successes.at(-1), "trade.notice.submitted");
  console.log("PASS the full-page form submits, posts and goes back to the list");
})().catch(error => { console.error(error); process.exitCode = 1; });
