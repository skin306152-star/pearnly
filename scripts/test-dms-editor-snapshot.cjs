#!/usr/bin/env node
// Offline: real TypeScript helpers and compiled Vue setup; no browser, HTTP or Site.
// Requires the task frontend's locked npm ci dependencies, never shared node_modules.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const { test } = require('node:test');
process.env.TZ = 'UTC';
const root = path.resolve(__dirname, '..');
const req = createRequire(path.join(root, 'frontend/package.json'));
const ts = req('typescript');
const vue = req('vue');
const { parse, compileScript } = req('@vue/compiler-sfc');
const source = fs.readFileSync(path.join(root, 'frontend/src/pages/dms/EditorPage.vue'), 'utf8');
const rows = fs.readFileSync(path.join(root, 'apps/pearnly_dms/pearnly_dms/i18n/messages.tsv'), 'utf8')
  .trim().split('\n').slice(1).map(line => line.split('\t'));
const table = Object.fromEntries(rows.filter(row => row[4]).map(([en, zh, th, ja, key]) => [key, { en, zh, th, ja }]));
class ApiError extends Error { constructor(message, status) { super(message); this.status = status; } }
function evaluate(code, imports) {
  const module = { exports: {} };
  const js = ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  vm.runInNewContext(js, {
    module, exports: module.exports,
    require(name) { assert.ok(name in imports, `Unexpected import: ${name}`); return imports[name]; },
    fetch() { throw new Error('HTTP forbidden'); },
  });
  return module.exports;
}
const helpers = evaluate(fs.readFileSync(path.join(root, 'frontend/src/lib/dms-editor.ts'), 'utf8'), {
  './api': { call() { throw new Error('API forbidden'); }, setCsrfToken() { throw new Error('API forbidden'); } },
});
function setup() {
  const locale = vue.ref('en');
  const t = key => table[key]?.[locale.value] || key;
  const module = evaluate(compileScript(parse(source).descriptor, { id: 'snapshot-offline' }).content, {
    vue: { ...vue, onMounted() {} }, 'vue-router': { useRoute: () => ({ query: { draft: 'synthetic', editor: 'customer' } }) },
    'frappe-ui': {}, '../../components/LineLayout.vue': {}, '../../lib/api': { ApiError },
    '../../lib/dms-editor': helpers,
    '../../lib/i18n': { createTexts(keys, prefix) {
      assert.equal(prefix, 'dms.'); assert.equal(keys.length, 0);
      return { t, tf: t, locale, load: async () => {}, setLocale: value => { locale.value = value; } };
    } },
  });
  return { page: module.default.setup({}, { expose() {} }), locale, t };
}
const stamp = Date.parse('2026-09-24T01:23:00Z') / 1000;
const info = { as_of: stamp, generation: 'generation-test', stale: false,
  geo_as_of: stamp - 86400, geo_stale: true };
test('actual source timestamp, not current time; four languages and no raw placeholder', () => {
  for (const lang of ['en', 'zh', 'th', 'ja']) {
    const text = helpers.snapshotLabel(info, lang, key => table[key][lang]);
    assert.ok(!text.includes('{time}'));
    assert.ok(text.includes('01:23'));
    assert.ok(text.includes(lang === 'th' ? '2569' : '2026'));
    assert.notEqual(text, helpers.snapshotLabel({ ...info, stale: true }, lang, key => table[key][lang]));
  }
});
test('missing, invalid, or out-of-range timestamps never become now', () => {
  for (const as_of of [undefined, null, 0, -1, NaN, Infinity, '123', 1e30]) {
    assert.equal(helpers.snapshotLabel({ ...info, as_of }, 'en', key => table[key].en), '');
    assert.equal(helpers.addressSnapshotLabel({ ...info, geo_as_of: as_of }, 'en', key => table[key].en), '');
  }
  assert.equal(helpers.snapshotLabel(undefined, 'en', () => { throw new Error('No timestamp'); }), '');
});
test('business and daily-address timestamps/staleness remain independent in Vue setup', () => {
  const { page, locale } = setup();
  page.adopt({ form: { customer: {}, answers: {}, payments: [], files: {}, advisor: {} },
    masters: {}, geo: {}, manual_banks: [], snapshot: info });
  assert.match(page.snapshotNote.value, /Data as of.*09\/24\/2026/);
  assert.match(page.addressSnapshotNote.value, /Address sync is delayed.*09\/23\/2026/);
  locale.value = 'zh';
  assert.match(page.snapshotNote.value, /资料时间/);
  assert.match(page.addressSnapshotNote.value, /地址同步有延迟/);
  page.adopt({ form: { customer: {} }, masters: {}, geo: {} });
  assert.equal(page.snapshotNote.value, ''); assert.equal(page.addressSnapshotNote.value, '');
});
test('five service error codes resolve to readable product translations in all languages', () => {
  const { page, locale } = setup();
  for (const suffix of ['PENDING', 'STALE', 'INCOMPLETE', 'SCOPE_CHANGED', 'REQUEST_LIMIT']) {
    const key = `dms.err.ERR_DMS_SNAPSHOT_${suffix}`;
    assert.equal(helpers.snapshotErrorKey(key), key);
    for (const language of ['en', 'zh', 'th', 'ja']) {
      locale.value = language;
      assert.equal(page.explain(new ApiError(key, 503)), table[key][language]);
      assert.notEqual(page.explain(new ApiError(key, 503)), key);
    }
  }
  assert.equal(helpers.snapshotErrorKey('dms.err.PRIVATE_UNEXPECTED'), '');
  assert.equal(page.explain(new ApiError('unknown', 409)), 'dms.edit.expired');
});
test('visible native text binds both timestamps and query notes are declared in all languages', () => {
  assert.match(source, /<p v-if="snapshotNote"[^>]*role="status">\{\{ snapshotNote \}\}<\/p>/);
  assert.match(source, /<p v-if="addressSnapshotNote"[^>]*role="status">\{\{ addressSnapshotNote \}\}<\/p>/);
  for (const key of ['dms.card.query.CARD_NOTE', 'dms.card.query.LBL_READ_AT', 'dms.card.query.TOP_NOTE_PREFIX',
    'dms.snapshot.geo_as_of', 'dms.snapshot.geo_stale']) {
    for (const language of ['en', 'zh', 'th', 'ja']) assert.ok(table[key][language]);
  }
  assert.match(table['dms.card.query.CARD_NOTE'].en, /5 minutes.*not draft revenue/);
});
