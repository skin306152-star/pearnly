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
    'frappe-ui': {}, '../../components/LineLayout.vue': {}, './ReviewPage.vue': {}, '../../lib/api': { ApiError },
    '../../lib/dms-editor': helpers,
    // 吞错闸门的统一出错回传（frontend/src/lib/caught.ts）：沙箱里没有站点页面，回传是空操作，原来的处理照样调用
    '../../lib/caught': { reportCaught() {}, withReport: (fallback) => (error) => (typeof fallback === 'function' ? fallback(error) : fallback) },
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
test('the edit page no longer shows snapshot timestamps', () => {
  // 2026-09-25 操作员："资料时间／地址资料时间"不要出现在编辑页。契约变了，
  // 但严格性不变：时间仍然随草稿返回（下一项断言），只是页面不再渲染它。
  const { page } = setup();
  page.adopt({ form: { customer: {}, answers: {}, payments: [], files: {}, advisor: {} },
    masters: {}, geo: {}, manual_banks: [], snapshot: info });
  for (const key of ['snapshotNote', 'addressSnapshotNote']) {
    assert.equal(page[key], undefined, `${key} must be gone from the page`);
  }
});

test('the draft still carries both source timestamps for whoever needs them', () => {
  const { page } = setup();
  page.adopt({ form: { customer: {} }, masters: {}, geo: {}, snapshot: info });
  assert.equal(page.snapshot.value.as_of, info.as_of);
  assert.equal(page.snapshot.value.geo_as_of, info.geo_as_of);
  assert.equal(page.snapshot.value.geo_stale, true);
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
test('no timestamp line is bound into the edit page, and query notes are declared in all languages', () => {
  assert.doesNotMatch(source, /snapshotNote|addressSnapshotNote/);
  // dms.card.query.CARD_NOTE 没有任何调用处，10-18 存量文案整改时删掉（docs/work/2026-10-17-copy-stock-review.md 第 7 节）
  for (const key of ['dms.card.query.LBL_READ_AT', 'dms.card.query.TOP_NOTE_PREFIX',
    'dms.snapshot.geo_as_of', 'dms.snapshot.geo_stale']) {
    for (const language of ['en', 'zh', 'th', 'ja']) assert.ok(table[key][language]);
  }
  assert.equal(table['dms.card.query.CARD_NOTE'], undefined);
});

// 2026-10-30 真机：保存被拒时提示在表单末尾、手机上看不见，第一次点保存像没反应。
const editorPy = fs.readFileSync(path.join(root, 'apps/pearnly_dms/pearnly_dms/editor/booking_edit.py'), 'utf8')
  + fs.readFileSync(path.join(root, 'apps/pearnly_dms/pearnly_dms/editor/customer_edit.py'), 'utf8');
const SAVE_FIELD_CODES = {
  people_id: 'people_id', name: 'name', phone: 'phone', birthday: 'birthday_be', house_no: 'house_no',
  province_id: 'province_id', district_id: 'district_id', subdistrict_id: 'subdistrict_id',
  zipcode_id: 'zipcode_id', delivery: 'delivery_date_be', regis_name: 'regis_name',
};
test('the save error is shown inside the sticky action bar, not at the end of the form', () => {
  const template = parse(source).descriptor.template.content;
  const bar = template.slice(template.indexOf('class="pn-editor-actions"'));
  assert.ok(bar.indexOf('<ErrorMessage') > 0 && bar.indexOf('<ErrorMessage') < bar.indexOf('</div>'));
  assert.equal((template.match(/<ErrorMessage/g) || []).length, 1);
  assert.match(source, /\.pn-editor-actions \{[^}]*position: sticky/);
});
test('every field the server validates on save leads to that field on the page', () => {
  const { page } = setup();
  for (const [code, field] of Object.entries(SAVE_FIELD_CODES)) {
    // 服务端确实会回这个码（逐项校验或 f-string 拼出来的那几个地址栏）
    assert.ok(editorPy.includes(`dms_booking.invalid_${code}`) || ['house_no', 'province_id', 'district_id',
      'subdistrict_id', 'zipcode_id'].includes(code), code);
    assert.equal(page.fieldOf(new ApiError(`dms_booking.invalid_${code}`, 417)), field, code);
    assert.ok(source.includes(`fieldClass('${field}')`), `${field} must be marked in the template`);
  }
  for (const code of ['payment_required', 'invalid_payment', 'invalid_amount', 'payment_detail_required',
    'duplicate_payment', 'invalid_bank']) {
    assert.equal(page.fieldOf(new ApiError(`dms_booking.${code}`, 417)), 'payments', code);
  }
  for (const code of ['slip_required', 'slip_without_transfer']) {
    assert.equal(page.fieldOf(new ApiError(`dms_booking.${code}`, 417)), 'files', code);
  }
  assert.ok(source.includes("fieldClass('payments')") && source.includes("fieldClass('files')"));
});
test('errors that belong to no single field mark nothing, and the message still shows', () => {
  const { page } = setup();
  for (const code of ['dms_booking.invalid_master', 'dms_booking.expired', 'dms_booking.preview_send_failed',
    'dms_booking.invalid_payload', 'unknown']) {
    assert.equal(page.fieldOf(new ApiError(code, 417)), '', code);
    assert.ok(page.explain(new ApiError(code, 417)));
  }
});
test('only the failed field carries the invalid mark', () => {
  const { page } = setup();
  page.invalidField.value = 'delivery_date_be';
  assert.deepEqual([...page.fieldClass('delivery_date_be')], ['pn-field-delivery_date_be', 'pn-editor-invalid']);
  assert.deepEqual([...page.fieldClass('phone')], ['pn-field-phone', '']);
});
