// 报表筛选的值一律交给服务端：Select 选项要用官方 {value, label}（值固定、显示跟界面语言），不能直接拿译文当值
// （10-26 G13：盘点差异报表「只看」的选项是译文，服务端按 counted / uncounted / differences 比，永远不命中，等于不筛）。
// 逐个扫各 App 的脚本报表（report/*/*.js）里 filters 的 Select：options 里出现 __() / t() 的纯字符串项就不通过。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const files = [];
for (const app of fs.readdirSync('apps')) {
  const root = path.join('apps', app, app);
  if (!fs.existsSync(root)) continue;
  for (const module of fs.readdirSync(root)) {
    const reports = path.join(root, module, 'report');
    if (!fs.existsSync(reports) || !fs.statSync(reports).isDirectory()) continue;
    for (const report of fs.readdirSync(reports)) {
      const file = path.join(reports, report, `${report}.js`);
      if (fs.existsSync(file)) files.push(file);
    }
  }
}
assert.ok(files.length > 0, 'found the script reports');
const problems = [];
for (const file of files) {
  const text = fs.readFileSync(file, 'utf8');
  // 每个 Select 筛选：从 fieldtype: "Select" 起到下一个 fieldname 之前，看 options 是不是译文字符串的数组 / 换行串
  const parts = text.split(/fieldtype:\s*["']Select["']/).slice(1);
  for (const part of parts) {
    const options = (part.match(/options:\s*(\[[\s\S]*?\]|[^,\n]+)/) || [])[1] || '';
    if (/^\[\s*(__|t)\(/.test(options) || /\]\s*\.join\(/.test(options) || /^(__|t)\(/.test(options)) {
      problems.push(`${file}: ${options.slice(0, 80)}`);
    }
  }
}
assert.deepEqual(problems, [], '报表的 Select 筛选要用 {value, label}，值固定、不随界面语言变');
console.log(`PASS ${files.length} script reports keep fixed Select filter values`);
