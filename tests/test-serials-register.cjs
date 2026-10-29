// 按台登记（serials.js）：扫车身二维码填进下一格空的号码；留空一行时保存要提示台数不符（10-29 实测缺陷：
// `String(register ? row.serial_no : row.serial || "")` 在登记模式下把空行变成 "undefined"，扫码结果丢了、保存没反应）。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const SOURCE = fs.readFileSync(path.join(__dirname, "..",
  "apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/serials.js"), "utf8");

function boot({ tracking = true } = {}) {
  const dialogs = [], messages = [], sounds = [], scans = [];
  class Dialog {
    constructor(options) {
      this.options = options;
      const data = options.fields[0].data;
      this.fields_dict = { units: { grid: { get_data: () => data, refresh() {} } } };
      dialogs.push(this);
    }
    set_secondary_action_label() {}
    set_secondary_action(action) { this.secondary = action; }
    show() {}
    hide() { this.hidden = true; }
  }
  const p = {
    call: async () => ({ name: "CAR", product_name: "Car", track_serial: 1, serial_kind: "vehicle" }),
    unit_tracking: () => tracking,
    scan_supported: () => true,
    scan: (options) => scans.push(options),
    scan_result_sound: (ok) => sounds.push(ok),
  };
  const context = {
    frappe: { pearnly: p, ui: { Dialog }, msgprint: (message) => messages.push(message) },
    __: (text) => text,
  };
  vm.createContext(context);
  vm.runInContext(SOURCE, context);
  return { p, dialogs, messages, sounds, scans };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

test("登记：扫码填进第一格空的号码", async () => {
  const env = boot();
  const pending = env.p.serials.collect([{ product: "CAR", qty: 2 }], { mode: "register" });
  await settle();
  const dialog = env.dialogs[0];
  dialog.secondary();
  await env.scans[0].on_result("MR0LAB00000000001");
  const rows = dialog.fields_dict.units.grid.get_data();
  assert.equal(rows[0].serial_no, "MR0LAB00000000001");
  assert.equal(rows[1].serial_no, undefined);
  assert.deepEqual(env.sounds, [true]);
  await env.scans[0].on_result("MR0LAB00000000002");
  assert.equal(rows[1].serial_no, "MR0LAB00000000002");
  dialog.options.primary_action({ units: rows });
  const lines = await pending;
  assert.deepEqual(Array.from(lines[0].serials, (row) => row.serial_no), ["MR0LAB00000000001", "MR0LAB00000000002"]);
});

test("登记：留一行空着保存 → 提示台数不符、不交回", async () => {
  const env = boot();
  env.p.serials.collect([{ product: "CAR", qty: 2 }], { mode: "register" });
  await settle();
  const dialog = env.dialogs[0];
  dialog.options.primary_action({ units: [{ serial_no: "MR0LAB00000000001" }, {}] });
  assert.deepEqual(env.messages, ["The number of units does not match the quantity"]);
  assert.notEqual(dialog.hidden, true);
});

test("普通公司不弹登记", async () => {
  const env = boot({ tracking: false });
  const lines = await env.p.serials.collect([{ product: "CAR", qty: 2 }], { mode: "register" });
  assert.equal(env.dialogs.length, 0);
  assert.equal(lines[0].serials, undefined);
});
