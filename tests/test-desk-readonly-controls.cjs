// 只读的原生 Autocomplete 不建输入框、没有选项列表，框架的取值校验照样去读它，给它赋值就报错、打断整页
// （2026-10-26 打印窗口发现：已提交 / 作废的采购、销售单详情页里库位格是只读 Autocomplete，明细赋值时报错，
// 之后的 Express 按钮、收款、取消菜单都出不来）。
// 守两层：① 共用层给原生 Autocomplete 加了只读不核的保护（select.js，用例在 test-native-desk.cjs）；
// ② 各处字段定义：能变成只读的格按能不能改选控件（只读时用原生只读文字），这里逐处扫产品 Desk 脚本，
// 新增的 Autocomplete 格要么按「能不能改」选控件（条件里有 editable / readonly / read_only / is_new），要么带 ignore_validation，
// 要么是始终可编辑的对话框、登记在下面并写明原因。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ALWAYS_EDITABLE = {
  'apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/stock-opening.js#location': '期初录入行：建好后才按需锁格（控件那时已建好选项列表）',
  'apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/stocktake-entry.js#location': '盘点录入对话框，始终可编辑',
  'apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/transactions.js#location': '出入库单编辑器的明细行（只读的明细表走 lines(readonly)，那里按 readonly 选控件）',
  'apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/transactions.js#to_location': '调拨单编辑器明细行的调入仓位（同上一条；只读的明细表这一列是 Data）',
  'apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/transactions.js#pick_': 'Express 要人选的对话框，始终可编辑',
  'apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/company-menu.js#company': '切换公司的搜索对话框，始终可编辑',
  // 字段名来自上面的共用 base（c0、c1…），扫不到字段名，按文件登记
  'apps/pearnly_cowork/pearnly_cowork/public/desk/targets.js#?': 'Cowork 推送前要人选的对话框，始终可编辑',
};

function walk(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (!['node_modules', 'dist', 'build'].includes(entry.name)) walk(full, out);
    } else if (entry.name.endsWith('.js')) out.push(full);
  }
  return out;
}

const roots = fs.readdirSync('apps').flatMap(app => {
  const dir = path.join('apps', app, app, 'public');
  return fs.existsSync(dir) ? walk(dir) : [];
});
const problems = [];
const used = new Set();
let seen = 0;
for (const file of roots) {
  const lines = fs.readFileSync(file, 'utf8').split('\n');
  lines.forEach((raw, index) => {
    const line = raw.replace(/\/\/.*$/, '');
    if (!/["']Autocomplete["']/.test(line)) return;
    seen += 1;
    if (/ignore_validation\s*:\s*true/.test(line)) return;
    const ternary = line.match(/([^?:,{(]*)\?\s*["']Autocomplete["']/);
    if (ternary && /editable|readonly|read_only|is_new/.test(ternary[1])) return;
    const near = lines.slice(index, index + 4).join(' ');
    const named = near.match(/fieldname\s*:\s*["'`]([a-z_]+)/);
    const key = `${file.split(path.sep).join('/')}#${named ? named[1] : '?'}`;
    if (ALWAYS_EDITABLE[key]) { used.add(key); return; }
    problems.push(`${file}:${index + 1} ${key}`);
  });
}
assert.ok(seen > 0, 'scanned the product Desk scripts');
assert.deepEqual(problems, [], 'Autocomplete 格可能只读：按能不能改选控件（只读用原生只读文字）、带 ignore_validation，或登记为始终可编辑并写明原因');
assert.deepEqual(Object.keys(ALWAYS_EDITABLE).filter(key => !used.has(key)), [], '登记表里有已经不存在的格，删掉');
const select = fs.readFileSync('apps/pearnly_core/pearnly_core/public/desk/select.js', 'utf8');
assert.match(select, /frappe\.ui\.form\.ControlAutocomplete = ProductAutocomplete/, '共用层的只读保护还在');
console.log(`PASS ${seen} Autocomplete fields pick a read-only control when they can be read-only`);
