// 推送前「要人选的项」弹窗（public/desk/targets.js 的 express_choices_ready）：费用科目的建议排在清单最前、预选第一项，
// 但只有点「保存」才记下（科目建议 B + C，2026-10-15）。跑真实的 targets.js，框架与产品壳换成离线替身，不连站点。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');

// 替身里跑出来的数组属于另一个 realm：按 JSON 比。
const same = (actual, expected) => assert.equal(JSON.stringify(actual), JSON.stringify(expected));

const source = fs.readFileSync('apps/pearnly_cowork/pearnly_cowork/public/desk/targets.js', 'utf8');

function load(view) {
  const calls = [];
  const dialogs = [];
  const p = {
    call: async (method, args, verb) => {
      calls.push({method, args, verb});
      return method.endsWith('.choices') ? view : {saved: 1};
    },
    dialog: (title, fields, label, action) => {
      const dialog = {title, fields, label, action, hidden: false, hide() { this.hidden = true; }};
      dialogs.push(dialog);
      return dialog;
    },
  };
  const frappe = {pearnly: p, show_alert() {}, msgprint() {}};
  vm.runInNewContext(source, {frappe, __: String, console, setTimeout});
  return {targets: p.intake_targets, calls, dialogs};
}

const ACCOUNTS = [{code: '1111-00', name: 'เงินสด'}, {code: '5310-01', name: 'ค่าไฟฟ้า'},
  {code: '5320-01', name: 'ค่าเครื่องเขียนแบบพิมพ์'}, {code: '5390-99', name: 'ค่าใช้จ่ายเบ็ดเตล็ด'}];
const SUGGESTED = [
  {code: '5320-01', name: 'ค่าเครื่องเขียนแบบพิมพ์', source: 'history', reason: 'Used on 3 documents'},
  {code: '5390-99', name: 'ค่าใช้จ่ายเบ็ดเตล็ด', source: 'name', reason: 'Account name matches'}];

test('suggested accounts lead the list and the first is preselected, saved only on Save', async () => {
  const view = {needed: [{kind: 'account', subject: 'purchase|0105556012345', label: 'ศรีกรุง', suggested: SUGGESTED},
    {kind: 'payment', subject: 'purchase|0105556012345', label: 'ศรีกรุง'}], options: {accounts: ACCOUNTS}};
  const {targets, calls, dialogs} = load(view);
  const ready = targets.express_choices_ready({name: 'C1'}, 'S:\\EXP69\\DN', 'H1');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(dialogs.length, 1);
  const [account, payment] = dialogs[0].fields;
  assert.equal(account.default, '5320-01');
  same(account.options.map(row => row.value), ['5320-01', '5390-99', '1111-00', '5310-01']);
  assert.equal(account.options[0].label, '5320-01 · ค่าเครื่องเขียนแบบพิมพ์');
  assert.equal(account.reqd, 1);
  assert.equal(payment.default, undefined);  // 别的项不预选
  assert.equal(calls.filter(call => call.method.endsWith('save_choices')).length, 0);  // 还没点保存：什么都不记
  await dialogs[0].action({c0: '5320-01', c1: 'cash'});
  assert.equal(await ready, true);
  const saved = calls.find(call => call.method.endsWith('save_choices'));
  assert.equal(saved.verb, 'POST');
  same(JSON.parse(saved.args.items).map(item => [item.kind, item.value]), [['account', '5320-01'], ['payment', 'cash']]);
});

test('a value chosen before stays chosen and no suggestion means no preselection', async () => {
  const view = {needed: [{kind: 'account', subject: 'S', label: 'A', current: '5310-01', suggested: SUGGESTED},
    {kind: 'account', subject: 'T', label: 'B'}], options: {accounts: ACCOUNTS}};
  const {targets, dialogs} = load(view);
  const ready = targets.express_choices_ready({name: 'C1'}, 'K', 'H1');
  await new Promise(resolve => setImmediate(resolve));
  const [first, second] = dialogs[0].fields;
  assert.equal(first.default, '5310-01');
  assert.equal(second.default, undefined);
  same(second.options.map(row => row.value), ACCOUNTS.map(row => row.code));
  dialogs[0].onhide();  // 关掉弹窗：这张票这次先不推
  assert.equal(await ready, false);
});
