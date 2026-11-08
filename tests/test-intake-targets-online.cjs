// 录入「新建录入」里 Express「连接」弹窗与选账套后的提示（public/desk/targets.js）问电脑在不在线：
// `pearnly_cowork.companion.api.online` 只收 GET（@frappe.whitelist(methods=["GET"])），用 POST 调回 403，
// 框架每次都弹「没有权限」（11-08 上线验收：弹窗每 3 秒问一次就每 3 秒弹一次）。跑真实的 targets.js，框架换成离线替身。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync('apps/pearnly_cowork/pearnly_cowork/public/desk/targets.js', 'utf8');
const ONLINE = 'pearnly_cowork.companion.api.online';

function load() {
  const requests = [];
  const answer = (method) => (method === ONLINE ? {online: true, needs_update: true} : []);
  const frappe = {
    // 框架 frappe.call 不给 type 时按 POST 发；frappe.xcall(method, args, type) 同样默认 POST
    call: async ({method, args, type}) => {
      requests.push({method, args, type: (type || 'POST').toUpperCase()});
      return {message: answer(method)};
    },
    xcall: async (method, args, type) => {
      requests.push({method, args, type: (type || 'POST').toUpperCase()});
      return answer(method);
    },
    ui: {
      Dialog: class {
        constructor(options) {
          this.options = options;
          this.fields_dict = {state: {$wrapper: {html() {}}}};
          this.$wrapper = {addClass() {}};
        }
        show() {}
      },
    },
    show_alert() {},
    msgprint() {},
  };
  const p = {};
  frappe.pearnly = p;
  // 产品壳 p.call 照 public/desk/product.js：默认 GET，交给 frappe.call
  p.call = async (method, args = {}, type = 'GET') => (await frappe.call({method, args, type})).message;
  const timers = [];
  vm.runInNewContext(source, {
    frappe, __: String, console, globalThis: {}, setTimeout,
    setInterval: (fn) => { timers.push(fn); return timers.length; }, clearInterval() {},
  });
  return {targets: p.intake_targets, requests};
}

const onlineCalls = (requests) => requests.filter((request) => request.method === ONLINE);

test('the Express connect dialog asks whether a computer is online with GET', async () => {
  const {targets, requests} = load();
  await targets.helper_dialog({connection: {name: 'CONN-1'}, adapter: 'express', title: 'Express'});
  const calls = onlineCalls(requests);
  assert.ok(calls.length >= 1, 'the dialog asks once when it opens');
  for (const call of calls) assert.equal(call.type, 'GET');
});

test('choosing an Express account set asks the update note with GET and shows it', async () => {
  const {targets, requests} = load();
  const note = await targets.account_note({adapter: 'express', name: 'CONN-1'}, 'S:\\EXP69\\DN');
  assert.equal(note, 'intake.agent.state.needsUpdate');
  const calls = onlineCalls(requests);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].type, 'GET');
  assert.equal(calls[0].args.account_set_key, 'S:\\EXP69\\DN');
});

test('the target state asks online with GET', async () => {
  const {targets, requests} = load();
  await targets.state();
  for (const call of onlineCalls(requests)) assert.equal(call.type, 'GET');
});
