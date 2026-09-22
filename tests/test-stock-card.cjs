// 收发存明细表格子的内容：走真实 stock-card.js + product.js，只断言纯函数 `ledger_grid`
// 排出来的格子（DOM 那一层只按官方类名摆，不在这里验）。
//
// 口径来自旧项目那张收发存（用户 2026-09-22 给的参考图）：分组表头 入库/出库/结存 ×
// 数量/单价/金额、一行一笔进出、末行「合计 · N 条明细」、佛历日期、金额两位小数不带币种、
// 成本未知显示 —（留空只表示"不是这一侧"）。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const PRODUCT = fs.readFileSync(
  'apps/pearnly_core/pearnly_core/public/desk/product.js', 'utf8');
const STOCK_CARD = fs.readFileSync(
  'apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/stock-card.js', 'utf8');

function boot() {
  const context = {
    frappe: {
      provide() {}, pearnly: {}, boot: {}, re_route: {}, model: {},
      ui: {}, breadcrumbs: { set_custom_breadcrumbs() {} },
      datetime: { str_to_user: value => value, get_today: () => '2026-09-22', month_start: () => '2026-09-01' },
    },
    // product.js 的 p.number/p.amount 会调这两个框架函数：这里给出确定性的等价物。
    format_number: (value, _format, decimals) => Number(value).toFixed(decimals ?? 0),
    format_currency: (value, _currency, decimals) => Number(value).toFixed(decimals ?? 2),
    window: {}, document: { body: { classList: { add() {} } }, createElement: () => ({}) },
    $: () => ({ on() {}, empty: () => ({ append() {} }), append() {}, find() { return this; } }),
    __: key => key,
  };
  vm.runInNewContext(PRODUCT, context);
  vm.runInNewContext(STOCK_CARD, context);
  return context.frappe.pearnly;
}

// 词表：断言里直接看键名，翻不翻不影响结构。
const t = key => key;
const card = (over = {}) => ({
  date_from: '2026-09-01',
  opening: { qty: 0, unit_cost: 0, value: 0 },
  closing: { qty: 0, unit_cost: null, value: null },
  rows: [],
  ...over,
});
const row = (over) => ({ date: '2026-09-22', doc_no: 'STE-1', qty: 1, unit_price: 7,
  amount: 7, bal_qty: 1, bal_unit_cost: 7, bal_value: 7, ...over });

(async () => {
  const p = boot();

  // 1) 表头：日期/单据编号 + 三个组（入库/出库/结存），第二行是每组各一遍「数量/单价/金额」。
  const head = p.ledger_grid(t, card());
  assert.deepEqual([...head.fixed_head], ['trade.date', 'trade.docNo']);
  assert.deepEqual([...head.group_head].map(([label, span]) => [label, span]),
    [['trade.groupIn', 3], ['trade.groupOut', 3], ['trade.groupBalance', 3]]);
  assert.deepEqual([...head.num_head],
    ['trade.qty', 'trade.price', 'trade.amount', 'trade.qty', 'trade.price', 'trade.amount',
      'trade.qty', 'trade.price', 'trade.amount']);
  console.log('PASS the grouped header repeats quantity/price/amount per side');

  // 2) 入库行：正数进"入库"那三列，出库三列留空；结存照后端给的值。
  const inRow = p.ledger_grid(t, card({ rows: [row({ qty: 3, unit_price: 7, amount: 21, bal_qty: 3, bal_value: 21 })] })).rows[0];
  assert.deepEqual([...inRow],
    ['22/09/2569', 'STE-1', '3', '7.00', '21.00', '', '', '', '3', '7.00', '21.00']);
  console.log('PASS an inbound movement fills the in columns and leaves out blank');

  // 3) 出库行：库里 `qty` 是负数，列头上已经写了"出库"，所以显示绝对值、进"出库"那三列。
  const outRow = p.ledger_grid(t, card({ rows: [row({ qty: -2, unit_price: 7, amount: 14, bal_qty: 1, bal_value: 7 })] })).rows[0];
  assert.deepEqual([...outRow],
    ['22/09/2569', 'STE-1', '', '', '', '2', '7.00', '14.00', '1', '7.00', '7.00']);
  console.log('PASS an outbound movement shows a positive quantity on the out side');

  // 4) 成本看不到时（服务端返回 null）显示 —，不是留空也不是 0。
  const noCost = p.ledger_grid(t, card({ rows: [row({ unit_price: null, amount: null, bal_unit_cost: null, bal_value: null })] })).rows[0];
  // 2..7 = 入库三格 + 出库三格（这一行是入库行，所以"入库"那三格有值、"出库"三格留空）。
  assert.deepEqual([...noCost].slice(2, 8), ['1', '—', '—', '', '', '']);
  assert.deepEqual([...noCost].slice(9), ['—', '—']);
  assert.equal(p.ledger_grid(t, card({ rows: [row({ doc_no: null, desc: '盘点 STC-1' })] })).rows[0][1],
    '盘点 STC-1', '没有单据号时用说明兜底');
  console.log('PASS unknown cost reads as a dash while a blank cell means the other side');

  // 5) 合计：两侧数量分别求和、金额各自求和、结存取期末；标签带明细条数。
  const totals = p.ledger_grid(t, card({
    rows: [row({ qty: 3, amount: 21 }), row({ qty: -2, amount: 14, doc_no: 'S-1' })],
    closing: { qty: 1, unit_cost: 7, value: 7 },
  })).foot;
  assert.deepEqual([...totals],
    ['trade.totalRows', null, '3', '', '21.00', '2', '', '14.00', '1', '7.00', '7.00']);
  console.log('PASS the footer sums both sides and carries the row count');

  // 6) 合计的诚实口径：任一笔金额未知，整列显示 —（不拿已知部分的和冒充）。
  const partial = p.ledger_grid(t, card({
    rows: [row({ qty: 3, amount: 21 }), row({ qty: 1, unit_price: null, amount: null })],
  })).foot;
  assert.equal(partial[4], '—');
  assert.equal(partial[2], '4', '数量照常求和（数量与金额是两回事）');
  console.log('PASS an unknown amount turns the whole money column into a dash');

  // 7) 期初：没有余额（数量与金额都是 0）就不占一行；有余额才排在第一行。
  assert.equal(p.ledger_grid(t, card()).rows.length, 0);
  const withOpening = p.ledger_grid(t, card({
    opening: { qty: 5, unit_cost: 7, value: 35 }, rows: [row()],
  })).rows;
  assert.equal(withOpening.length, 2);
  assert.deepEqual([...withOpening[0]],
    ['01/09/2569', 'trade.rowOpening', '', '', '', '', '', '', '5', '7.00', '35.00']);
  console.log('PASS the opening row only appears when there is an opening balance');
})().catch(error => { console.error(error); process.exitCode = 1; });
