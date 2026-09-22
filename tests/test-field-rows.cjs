// 表单分栏助手 `p.field_rows`：走真实 product.js，在假环境里只看它排出来的字段序列。
//
// 为什么值得单独钉：Frappe 的"栏"是**竖着堆字段**的，`Column Break` 只是换到下一栏。
// 整行控件（HTML/Table/Button/Section）如果只被一个 Column Break 收口，它会落进
// **当前栏**、跟字段并排挤在右边 —— 实测踩过两次：库存的流水弹窗、收发存二级页的表格
// 都被挤成窄窄一条（用户 2026-09-22："表格放下面"、"后面不要留那么多"）。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(
  'apps/pearnly_core/pearnly_core/public/desk/product.js', 'utf8');

function load() {
  const context = {
    frappe: {
      provide() {}, pearnly: {}, boot: {}, re_route: {}, model: {},
      ui: {}, breadcrumbs: { set_custom_breadcrumbs() {} },
      datetime: { str_to_user: value => value },
    },
    window: {}, document: { body: { classList: { add() {} } } },
    $: () => ({ on() {} }), __: key => key,
  };
  vm.runInNewContext(source, context);
  return context.frappe.pearnly;
}

const data = (name) => ({ fieldtype: 'Data', fieldname: name, label: name });
// 展开成本地数组：`fields` 来自 vm 里那个 realm，直接 `.map` 出来的数组原型不同，
// `deepStrictEqual` 会判"结构一样但不是同一个引用"。
const kinds = (fields) => [...fields].map(field =>
  field.fieldtype === 'Column Break' ? '|'
    : field.fieldtype === 'Section Break' ? '=='
      : (field.fieldname || field.fieldtype));

(async () => {
  const p = load();

  // 1) 四个字段 + 整行控件：字段两栏（每栏两个），整行控件另起一节。
  assert.deepEqual(
    kinds(p.field_rows([data('a'), data('b'), data('c'), data('d'),
      { fieldtype: 'HTML', fieldname: 'table' }])),
    ['a', 'b', '|', 'c', 'd', '==', 'table']);
  console.log('PASS a full-width control starts its own section, never a squeezed column');

  // 2) 恰好填满一栏时也要另起一节（这一条正是当初漏掉的：末尾那个 Column Break
  //    会把表格送进"下一栏"）。
  assert.deepEqual(
    kinds(p.field_rows([data('a'), data('b'), { fieldtype: 'HTML', fieldname: 'table' }])),
    ['a', 'b', '==', 'table']);
  assert.deepEqual(
    kinds(p.field_rows([data('a'), data('b'), data('c'),
      { fieldtype: 'Table', fieldname: 'lines' }])),
    ['a', 'b', '|', 'c', '==', 'lines']);
  assert.deepEqual(
    kinds(p.field_rows([data('a'), { fieldtype: 'Button', fieldname: 'go' }])),
    ['a', '==', 'go']);
  console.log('PASS a filled row is closed with a section break when a full-width control follows');

  // 3) 全是普通字段时仍然只是分栏（不额外插节，免得凭空多出分隔线）。
  assert.deepEqual(
    kinds(p.field_rows([data('a'), data('b'), data('c'), data('d')])),
    ['a', 'b', '|', 'c', 'd', '|']);
  assert.deepEqual(kinds(p.field_rows([data('a')])), ['a']);
  console.log('PASS plain fields keep the plain column layout');

  // 4) 三栏/四栏也一样；且**整行控件前面一定有节**（这条不变量是防回归的关键）。
  const wide = p.field_rows([data('a'), data('b'), data('c'), data('d'), data('e'), data('f'),
    data('g'), { fieldtype: 'HTML', fieldname: 'table' }], 4);
  assert.deepEqual(kinds(wide), ['a', 'b', 'c', 'd', '|', 'e', 'f', 'g', '==', 'table']);
  const html = { fieldtype: 'HTML', fieldname: 't' };
  assert.deepEqual(kinds(p.field_rows([data('a'), data('b'), data('c'), html])),
    ['a', 'b', '|', 'c', '==', 't']);
  assert.deepEqual(kinds(p.field_rows([data('a'), data('b'), data('c'), data('d'), html])),
    ['a', 'b', '|', 'c', 'd', '==', 't']);
  assert.deepEqual(kinds(p.field_rows([data('a'), data('b'), data('c'), data('d'), data('e'), html])),
    ['a', 'b', '|', 'c', 'd', '|', 'e', '==', 't']);
  assert.deepEqual(kinds(p.field_rows([data('a'), html])), ['a', '==', 't']);
  assert.deepEqual(kinds(p.field_rows([html])), ['t']);
  console.log('PASS every full-width control is always preceded by a section break');
})().catch(error => { console.error(error); process.exitCode = 1; });
