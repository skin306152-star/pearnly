// ERP 单据：排队中、此刻在等 Express 关闭（有人开着这本账套；服务端读的时候算的只读字段，2026-11-12）。
// 网页上原来只显示「排队中」，看不出在等什么：状态不变，列表 Express 一栏的悬浮说明与单据 Express 弹窗的「原因」
// 显示现成那句 `express.error.ACCOUNT_BUSY_LOCKED`。跑真实的 transactions.js，框架换成离线替身，不连站点。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const desk = path.join(__dirname, '../apps/pearnly_erp_portal/pearnly_erp_portal/public/desk');
const read = name => fs.readFileSync(path.join(desk, name), 'utf8');
const transactions = read('transactions.js');
const KEY = 'express.error.ACCOUNT_BUSY_LOCKED';
const SENTENCE = 'Express 正在使用此账套，关闭后将自动写入';
const __ = key => (key === KEY ? SENTENCE : key);

function boot(info) {
  const dialogs = [];
  const p = {
    call: async () => info,
    dialog: (title, fields, label, action) => { dialogs.push({title, fields, label, action}); return {}; },
    success() {},
  };
  const chain = {on() { return chain; }};
  vm.runInNewContext(transactions, {frappe: {pearnly: p, boot: {}, realtime: null, datetime: {}}, __,
    $: () => chain, document: {}, window: {}, flt: Number, URLSearchParams, console});
  return {p, dialogs};
}

test('在等 Express 关闭的行给出现成那句，别的行不给', () => {
  const {p} = boot({});
  assert.equal(p.express_waiting({ledger_state: 'queued', ledger_waiting: 'express_open'}), SENTENCE);
  assert.equal(p.express_waiting({ledger_state: 'queued'}), '');
  assert.equal(p.express_waiting({ledger_state: 'queued', ledger_waiting: 'other'}), '');
  // 单已经不是排队中（写进去了、停下）：不再说在等
  assert.equal(p.express_waiting({ledger_state: 'held', ledger_waiting: 'express_open'}), '');
  assert.equal(p.express_waiting(null), '');
});

test('单据的 Express 弹窗：状态仍是「排队中」，「原因」一栏是那一句；不在等的排队中没有原因', async () => {
  const base = {state: 'queued', docnum: '', reason: '', needed: [], can_answer: false, can_retry: false};
  const fields = async info => {
    const {p, dialogs} = boot(info);
    await p.express('Purchase Document', 'PUR-1');
    return Object.fromEntries(dialogs[0].fields.map(field => [field.fieldname, field]));
  };
  const waiting = await fields({...base, waiting: 'express_open'});
  assert.equal(waiting.state.default, 'Queued');
  assert.equal(waiting.reason.fieldtype, 'Small Text');   // 失败原因用的同一栏（官方只读控件）
  assert.equal(waiting.reason.default, SENTENCE);
  assert.equal((await fields({...base, waiting: ''})).reason, undefined);
  assert.equal((await fields(base)).reason, undefined);
  // 已写进 Express、又在 ERP 作废的出入库单：作废那一件在等
  const voiding = await fields({...base, state: 'written', docnum: 'OU1', void: 'queued', waiting: 'express_open'});
  assert.equal(voiding.reason.default, SENTENCE);
  // 停下的单照旧显示它自己的原因
  const held = await fields({...base, state: 'held', reason: '[PERIOD_LOCKED] closed', waiting: ''});
  assert.equal(held.reason.default, 'express.reason.periodLocked');
});

test('四个列表：Express 一栏的悬浮说明用这一句（列表接口为这一页补字段，不另发请求）', () => {
  for (const name of ['purchase.js', 'sales.js', 'stock-entry.js', 'pos-receipts.js']) {
    const source = read(name);
    const start = source.indexOf('fieldname: "ledger_docnum"');
    assert.notEqual(start, -1, name);
    const column = source.slice(start, source.indexOf('}] : [])', start));
    assert.match(column, /title: \(value, row\) => p\.express_waiting\(row\)/, name);
    // 不新增词条：页面不直接写词条键，只经 transactions.js 的同一个函数
    assert.equal(/ACCOUNT_BUSY_LOCKED/.test(source), false, name);
  }
  assert.equal(transactions.match(/express\.error\.ACCOUNT_BUSY_LOCKED/g).length, 1);
  // 共用列表的悬浮说明：列给了 `title(value, row)` 就用它，回空串照旧是格子里的字
  const list = fs.readFileSync(path.join(__dirname, '../apps/pearnly_core/pearnly_core/public/desk/list.js'), 'utf8');
  assert.match(list, /typeof column\.title === "function" \? column\.title\(value, row\) \|\| text : text/);
});
