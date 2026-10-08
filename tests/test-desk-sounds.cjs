// 全平台音效对齐官方 Frappe 16.33.0（用户 2026-10-08）：官方只有 `frappe/hooks.py` 里那 7 个音效，按动作分——
// 保存 click、提交 submit、取消 cancel、删除 delete、出错 error、发邮件 email（alert 注册了但前端没用）。
// 这里扫三个产品的全部 Desk 页面：只准用官方音效（外加收银台小键盘那一个），并且同一类动作用同一声。
// 另外走真实 list.js 验证批量动作把服务端结果交给提示：一条都没做成时不报成功、不响。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const OFFICIAL = new Set(['email', 'submit', 'cancel', 'delete', 'click', 'error', 'alert']);
const OURS = new Set(['numpad-touch']);  // pearnly_core/hooks.py 按官方音量补的收银台小键盘音
const root = path.join(__dirname, '..', 'apps');

function desk_files() {
  const found = [];
  const walk = dir => {
    for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) { if (entry.name !== 'node_modules') walk(full); }
      else if (entry.name.endsWith('.js') && full.includes(`${path.sep}public${path.sep}desk${path.sep}`)) found.push(full);
    }
  };
  walk(root);
  return found;
}

// 每个 `p.success(...)` 调用：消息原文 + 声音（没写是默认 submit）
function success_calls(source) {
  const calls = [];
  const pattern = /p\.success\(/g;
  let match;
  while ((match = pattern.exec(source))) {
    let depth = 1, i = match.index + match[0].length;
    while (depth && i < source.length) { depth += source[i] === '(' ? 1 : source[i] === ')' ? -1 : 0; i += 1; }
    const inner = source.slice(match.index + match[0].length, i - 1);
    const last = inner.match(/,\s*("([a-z-]+)"|null|sound)\s*$/);
    calls.push({text: inner, sound: last ? (last[2] || last[1]) : 'submit'});
  }
  return calls;
}

test('只用官方音效', () => {
  const bad = [];
  for (const file of desk_files()) {
    const source = fs.readFileSync(file, 'utf8');
    for (const [, name] of source.matchAll(/play_sound\("([a-z-]+)"\)/g)) {
      if (!OFFICIAL.has(name) && !OURS.has(name)) bad.push(`${path.relative(root, file)}: ${name}`);
    }
    for (const call of success_calls(source)) {
      if (!['null', 'sound'].includes(call.sound) && !OFFICIAL.has(call.sound)) bad.push(`${path.relative(root, file)}: ${call.sound}`);
    }
  }
  assert.deepEqual(bad, []);
});

test('同一类动作同一声：删除 delete、保存 click、归档作废 cancel', () => {
  // 按顺序取第一条命中的（「恢复已归档」是恢复，不是归档）
  const rules = [
    [/restored/, 'click'],
    [/deleted|Deleted/, 'delete'],
    [/\.saved"|"Saved"|notice\.saved|entrySaved|\.created"|notice\.added/, 'click'],
    [/archived|Cancelled|voidNotice|LINE unbound/, 'cancel'],
  ];
  const bad = [];
  for (const file of desk_files()) {
    for (const call of success_calls(fs.readFileSync(file, 'utf8'))) {
      const rule = rules.find(([pattern]) => pattern.test(call.text));
      if (rule && call.sound !== rule[1] && call.sound !== 'sound') {
        bad.push(`${path.relative(root, file)}: ${call.text.trim().slice(0, 60)} → ${call.sound}（应为 ${rule[1]}）`);
      }
    }
  }
  assert.deepEqual(bad, []);
});

test('批量动作：删除类用 delete，启用停用用 click', () => {
  const bad = [];
  for (const file of desk_files()) {
    const source = fs.readFileSync(file, 'utf8');
    for (const match of source.matchAll(/p\.selection_action\(\{([\s\S]*?)\n\t*\}\)/g)) {
      const block = match[1];
      const sound = (block.match(/sound:\s*"([a-z-]+)"/) || [])[1] || 'submit';
      const label = (block.match(/label:[^\n]*/) || [''])[0];
      const want = /[Dd]elete/.test(label) ? 'delete' : /bulkEnable|bulkDisable/.test(label) ? 'click' : null;
      if (want && sound !== want) bad.push(`${path.relative(root, file)}: ${label.trim().slice(0, 60)} → ${sound}（应为 ${want}）`);
    }
  }
  assert.deepEqual(bad, []);
});

function load_list() {
  const alerts = [], sounds = [];
  const frappe = {provide() {}, pearnly: {}, boot: {}, re_route: {}, model: {}, realtime: {on() {}, off() {}},
    ui: {Sidebar: class {}}, views: {BaseList: class {}, ListView: class {}}, datetime: {str_to_user: v => v}, breadcrumbs: {set_custom_breadcrumbs() {}},
    utils: {escape_html: v => v, play_sound: name => sounds.push(name)},
    show_alert: alert => alerts.push(alert)};
  const context = {frappe, window: {addEventListener() {}}, document: {body: {classList: {add() {}}}, addEventListener() {}},
    $: () => ({on() {}}), __: k => k, locals: {}, setTimeout, clearTimeout,
    localStorage: {getItem() { return null; }, setItem() {}, length: 0, key() {}}};
  for (const name of ['pearnly_core/pearnly_core/public/desk/product.js', 'pearnly_core/pearnly_core/public/desk/list.js']) {
    vm.runInNewContext(fs.readFileSync(path.join(root, name), 'utf8'), context);
  }
  return {p: frappe.pearnly, alerts, sounds};
}

test('批量动作把服务端结果交给提示；一条都没做成就不报成功、不响', async () => {
  const {p, alerts, sounds} = load_list();
  let action = null;
  const page = {set_primary_action: (_label, fn) => { action = fn; }, clear_primary_action() {},
    btn_primary: {prop() {}, attr() {}}};
  const rows = [{name: 'A'}, {name: 'B'}];
  let reply = {deleted: 0, skipped: 2};
  const seen = [];
  const paint = p.selection_action({page, list: () => ({get_checked_items: () => rows, clear_checked_items() {}, reload() {}}),
    label: () => 'Delete', sound: 'delete', run: async () => reply,
    success: (_rows, result) => { seen.push(result); return result.deleted ? `Deleted ${result.deleted}` : ''; }});
  const settle = () => new Promise(resolve => setTimeout(resolve, 20));
  paint();
  action();
  await settle();
  assert.equal(seen[0].skipped, 2);
  assert.equal(alerts.length, 0);
  assert.equal(sounds.length, 0);
  reply = {deleted: 1, skipped: 1};
  paint();
  action();
  await settle();
  assert.equal(alerts.length, 1);
  assert.equal(alerts[0].message, 'Deleted 1');
  assert.equal(sounds[0], 'delete');
});

test('p.success 传 null 只弹提示不出声', () => {
  const {p, alerts, sounds} = load_list();
  p.success('Found', null);
  assert.equal(alerts.length, 1);
  assert.equal(sounds.length, 0);
});
