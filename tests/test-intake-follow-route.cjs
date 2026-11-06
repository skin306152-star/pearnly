// 录入工作台编辑页：左边换到同一份原件的另一张票时，地址跟着换成那张票（2026-11-06）。
// 用户从 /desk/pearnly-intake/new/<第 8 张> 打开，原件翻到第 1 页，左边按设计换成第 1 张（缺第 2 页、没明细没合计），
// 地址却还停在第 8 张，被当成「第 8 张明细和税费全空」。跑真实的 intake-upload.js，框架换成离线替身，不连站点。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const file = path.join(__dirname, '../apps/pearnly_cowork/pearnly_cowork/public/desk/intake-upload.js');
const source = fs.readFileSync(file, 'utf8');
const EIGHTH = 'CWH-ea82ff5dbc55f69e0c35dfa94c4469c3ca35e094b3419260';
const FIRST = 'CWH-a63387bc8b60a6cf336b24b57400fd7f4c0c2cd1199f1a54';

function load(pathname) {
  const calls = [];
  const location = {pathname, search: '', hash: ''};
  const history = {state: {kept: 1}, replaceState(state, title, url) { calls.push({state, url}); location.pathname = url; }};
  const router = {current_route: ['pearnly-intake', 'new', EIGHTH], current_sub_path: `pearnly-intake/new/${EIGHTH}`};
  const p = {subroute: (root, ...parts) => '/desk/' + [root, ...parts].map(encodeURIComponent).join('/')};
  vm.runInNewContext(source, {frappe: {pearnly: p, router}, window: {history, location}, __: String,
    $: () => ({on() {}}), document: {},
    crypto: {randomUUID: () => 'x'}, URLSearchParams});
  return {p, calls, router, location, history};
}

test('换到第 1 张：地址、框架当前路由都换成第 1 张，不多一条后退记录', () => {
  const {p, calls, router, location, history} = load(`/desk/pearnly-intake/new/${EIGHTH}`);
  assert.equal(p.intake_follow_route(FIRST, {router, history, location}), true);
  assert.deepEqual(calls, [{state: {kept: 1}, url: `/desk/pearnly-intake/new/${FIRST}`}]);
  assert.deepEqual(Array.from(router.current_route), ['pearnly-intake', 'new', FIRST]);
  assert.equal(router.current_sub_path, `pearnly-intake/new/${FIRST}`);
});

test('还是同一张、或没有票号：地址不动', () => {
  const {p, calls, router, location, history} = load(`/desk/pearnly-intake/new/${EIGHTH}`);
  assert.equal(p.intake_follow_route(EIGHTH, {router, history, location}), false);
  assert.equal(p.intake_follow_route('', {router, history, location}), false);
  assert.deepEqual(calls, []);
});

test('换票（翻原件页、下拉选发票都走 open_record）时，从地址打开的编辑页调用它；新建上传的一批不调用', () => {
  const body = source.slice(source.indexOf('const open_record = async'), source.indexOf('const pick_invoice'));
  assert.match(body, /fill\(current\);\s*\/\/[^\n]*\n\s*if \(name && still_current\(\)\) p\.intake_follow_route\(current\.name\);/);
  // 翻原件页换票走的就是 open_record（from_viewer）
  assert.match(source, /on_page: \(row, number\) => \{[\s\S]*?open_record\(entry\.record, \{from_viewer: true\}\)/);
});
