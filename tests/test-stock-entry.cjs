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
  const calls = [], dialogs = [], routes = [], successes = [], editors = [];
  const dialog_values = {}, page_fields = {};
  let list_options, list_reloads = 0, primary = null, form_values = null;

  const p = {
    // 仓库/收银台的 Link 过滤来自 `transactions.js`（这几次测试只加载某几个页面脚本），
    // 所以在这里补一个同形状的桩：返回框架 Link 认的过滤对象。
    warehouse_query: () => ({filters: {}}),
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
      for (const field of fields) if (field.fieldname) by_name[field.fieldname] = {
        df: field, $wrapper: element(), $input: element(),
        get_value: () => dialog_values[field.fieldname],
        set_value: value => { dialog_values[field.fieldname] = value; },
      };
      const dialog = { title, fields, label, action, by_name, hidden: false,
        // 真家伙是 `frappe.ui.Dialog`，它一定带 `fields_dict`（页面按字段名往里挂控件）；
        // 桩少了这一层，`mount_lines` 就没处落。
        fields_dict: by_name,
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
      for (const field of fields) if (field.fieldname) by_name[field.fieldname] = {
        df: field, $wrapper: element(),
        get_value: () => page_fields[field.fieldname],
        set_value: value => { page_fields[field.fieldname] = value; },
      };
      return { fields_dict: by_name, get_values: () => form_values, set_value() {} };
    },
    trade: {
      lines: (_t, options) => ({ fieldtype: "Table", fieldname: "lines", label: "trade.lines", options }),
      // 明细细编辑器（`transactions.js` 的 `p.trade.lines_editor`）：按同一份契约给一个
      // "真实到够用"的桩 —— 记下每条、`values()` 丢掉空行（没物料也没说明的不算填过）、
      // `check` 时必填没填返回 null（真实实现也是这个口径）。
      lines_editor: (_t, options = {}) => {
        const added = [];
        const editor = {
          host: element(), options, warehouse_seen: [],
          add(values = {}) { added.push({ ...values }); return editor; },
          // 行里的仓库是表头的镜像：同步过来时记一笔，测试据此断言"表头变、行跟着变"。
          set_warehouse(name) { editor.warehouse_seen.push(name); },
          values({ check = true } = {}) {
            const kept = added.filter(row => String(row.product || "").trim() || String(row.description || "").trim());
            if (check && kept.some(row => !String(row.product || "").trim())) return null;
            return kept;
          },
        };
        editors.push(editor);
        return editor;
      },
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
    get lines() { return editors.at(-1); },
    get dialog_fields() { return dialog_values; },
    get page_fields() { return page_fields; },
    get values() { return form_values; },
    set values(values) { form_values = values; },
    get list_options() { return list_options; },
    get list_reloads() { return list_reloads; },
    async ready() { await p.stockEntry(page, key => key, direction, element()); } };
}

const call_names = env => env.calls.map(call => call.method.split(".").pop());
const the_call = (env, name) => env.calls.filter(call => call.method.endsWith(name)).at(-1);
// 明细编辑器交回来的行（摊平成普通对象，跨 realm 好断言）。
const plain_lines = env => JSON.parse(JSON.stringify(env.lines.values()));
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
  // 用户 2026-09-23 收回"仓库自由填写"那条口径（"我说用官方原生，这是第四遍了"）：
  // 仓库改回**官方原生 Link**（下拉只列当前公司的仓，不许现场新建 —— only_select）。
  assert.equal(warehouse.fieldtype, "Link", "仓库是官方 Link 选择器，不是自由填写");
  assert.equal(warehouse.options, "ERP Warehouse");
  assert.equal(warehouse.only_select, true, "下拉里不给「新建 ERP Warehouse」，免得跳去原生表单");
  assert.equal(warehouse.reqd, 1);
  const doc_no = dialog.fields.find(field => field.fieldname === "doc_no");
  assert.equal(doc_no.default, "IR2569-000009", "新建时显示当前要发的单据号");
  assert.equal(doc_no.read_only, 1);
  assert.ok(!dialog.fields.some(field => field.fieldname === "submit"), "不再有第二颗提交按钮");
  // 明细：录入时**不是表格**（用户 2026-09-23："出库和入库界面添加物料那个位置不要用表格，
  // 拆出来"）—— 一个宿主格 + 一颗官方的「添加明细」，真正的行由 `lines_editor` 逐条建。
  assert.ok(!dialog.fields.some(field => field.fieldtype === "Table"), "录入界面不再有表格");
  assert.equal(dialog.fields.find(field => field.fieldname === "lines").fieldtype, "HTML");
  assert.equal(dialog.fields.find(field => field.fieldname === "add_line").fieldtype, "Button");
  // 仓库只有**一个源**：表头那格。行里那格是它的镜像（只读、跟着表头变）——
  // 用户 2026-09-23 先指出"仓库重复了"，接着说"去掉了后把上面那个加进去。出入库同步"。
  assert.equal(typeof env.lines.options.suggest_warehouse, "function");
  assert.deepEqual(env.lines.warehouse_seen, [""], "挂上来先按表头的现值同步一次");
  assert.equal(env.lines.options.warehouse(), "", "行里那格读的就是表头的值");
  env.lines.options.suggest_warehouse("WH-00009");
  assert.equal(env.dialog_fields.warehouse, "WH-00009", "表头空着 → 用主档默认仓建议");
  assert.equal(env.lines.warehouse_seen.at(-1), "WH-00009", "建议进表头之后，行里的镜像跟着变");
  env.lines.options.suggest_warehouse("WH-00010");
  assert.equal(env.dialog_fields.warehouse, "WH-00009", "表头已有值 → 不覆盖");
  // 用户自己改表头（框架在 Link 变值后调 `df.onchange`）：行里的镜像同步过去。
  env.dialog_fields.warehouse = "WH-00011";
  dialog.fields_dict.warehouse.df.onchange();
  assert.equal(env.lines.warehouse_seen.at(-1), "WH-00011", "表头一改 → 每一行的仓库跟着走");
  console.log("PASS the dialog shows the number, takes a free-text warehouse and submits");

  // 2) 提交：一次调用就建单 + 过账，关掉弹窗、刷新列表，**不再打开编辑器**。
  // 明细从**编辑器**里取（不再是被 `get_values()` 收走的表格）；空着的那条不算填过。
  env.values = { posting_date: "2026-09-22", warehouse: "验收仓库", reason: "n" };
  env.lines.add({ product: "PRD-00001", qty: 2 });
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
  draft.values = { posting_date: "2026-09-22", warehouse: "WH-00002" };
  assert.deepEqual(plain_lines(draft), [{ product: "PRD-00001", qty: 1 }], "草稿里的明细铺进了编辑器");
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
  full.values = { posting_date: "2026-09-22", warehouse: "自由填写仓" };
  full.lines.add({ product: "PRD-00001", qty: 1 });
  full.lines.options.suggest_warehouse("WH-00012");
  assert.equal(full.page_fields.warehouse, "WH-00012", "整页里表头空着 → 建议填上");
  full.lines.options.suggest_warehouse("WH-00013");
  assert.equal(full.page_fields.warehouse, "WH-00012", "整页里表头已有值 → 不覆盖");
  await full.primary.action();
  await settle(full);
  const page_call = the_call(full, "api_create_stock_entry");
  assert.equal(page_call.args.submit, 1);
  assert.equal(page_call.args.direction, "out");
  assert.deepEqual(full.routes.at(-1), ["pearnly-stock-out"], "提交后回列表页");
  assert.equal(full.successes.at(-1), "trade.notice.submitted");
  console.log("PASS the full-page form submits, posts and goes back to the list");
})().catch(error => { console.error(error); process.exitCode = 1; });
