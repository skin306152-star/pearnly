// Exercise native Desk action wiring without a browser, Site or external writes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../apps/pearnly_control/pearnly_control/pearnly_control/doctype/pearnly_issue/pearnly_issue.js'), 'utf8');

async function scenario(status, dirty = false, failSave = false) {
  const events = [], buttons = new Map();
  const state = {request: 'PR-existing', status, counts: {failed: status === 'failed' || status === 'partial' ? 1 : 0}};
  const context = vm.createContext({
    __: text => text, setTimeout, document: {activeElement: null},
    frappe: {boot: {}, ui: {form: {on() {}}},
      dom: {freeze() {events.push('freeze')}, unfreeze() {events.push('unfreeze')}},
      msgprint() {events.push('error')},
      call: async ({method, args}) => {
        events.push(method);
        if (method.endsWith('api_retry_failed')) assert.equal(args.request, 'PR-existing');
        return {message: state};
      }},
  });
  vm.runInContext(source, context);
  context.open_progress = () => events.push('progress');
  const frm = {doc: {name: 'ISS-existing', status: 'issued'}, is_new: () => false,
    is_dirty: () => dirty, page: {set_indicator() {}},
    add_custom_button: (label, fn) => buttons.set(label, fn),
    save: async () => {events.push('save'); if (failSave) throw Error('save failed'); dirty = false},
    reload_doc: async () => events.push('reload')};
  context.render_provision(frm);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(buttons.has('Retry provisioning'), ['failed', 'partial'].includes(status));
  assert.equal(events.filter(x => x.endsWith('api_retry_failed')).length, 0, 'Rendering must never retry');
  if (buttons.has('Retry provisioning')) {
    await buttons.get('Retry provisioning')();
    assert.equal(events.filter(x => x.endsWith('api_retry_failed')).length, failSave ? 0 : 1);
    assert(!events.some(x => x.includes('api_issue') || x.endsWith('api_plan')), 'Never issue another account');
    if (dirty && !failSave) assert(events.indexOf('save') < events.findIndex(x => x.endsWith('api_retry_failed')));
    assert.equal(events.at(-1), 'unfreeze');
  }
}
// 发号并开通：保存没落地（Frappe 失败也 resolve）就不能拿临时单号发号；
// 服务端报错 Frappe 已按原文弹过，不再补一个 "[object Object]"。
async function issueScenario({saved, serverError = false, typing = false}) {
  const events = [], buttons = new Map();
  // 最后一格还在输入（ISS-2026-0294）：Frappe 在失焦的 change 回调里才把值写进 frm.doc。
  const typed = {tagName: 'INPUT', blur() {
    events.push('blur');
    Promise.resolve().then(() => { frm.doc.apps[0].line_channel_access_token = 'TOKEN' });
  }};
  const context = vm.createContext({
    __: text => text, setTimeout, document: {activeElement: typing ? typed : null},
    frappe: {boot: {}, ui: {form: {on() {}}}, show_alert() {},
      dom: {freeze() {events.push('freeze')}, unfreeze() {events.push('unfreeze')}},
      msgprint(m) {events.push('msgprint:' + m.message)},
      call: async ({method}) => {
        events.push(method);
        if (serverError) throw {responseJSON: {exc_type: 'ValidationError'}};
        return {message: {issue: 'ISS-1', tenant: 'T-1'}};
      }},
  });
  vm.runInContext(source, context);
  const frm = {doc: {name: 'new-pearnly-issue-x', status: 'draft', __islocal: 1, apps: [{}]}, page: {set_indicator() {}},
    is_new: () => !!frm.doc.__islocal, is_dirty: () => !!frm.doc.__islocal,
    add_custom_button: (label, fn) => buttons.set(label, fn),
    save: async () => {
      events.push('save:' + (frm.doc.apps[0].line_channel_access_token || ''));
      if (saved) frm.doc = {name: 'ISS-1', status: 'draft', apps: frm.doc.apps};
    },
    reload_doc: async () => events.push('reload')};
  context.render_provision(frm);
  context.start_provision = () => events.push('provision');
  await buttons.get('Issue and provision')();
  // 有正在输入的格子时，保存前会等 100ms 让 change 回调跑完（同 Frappe 主按钮）。
  await new Promise(resolve => setTimeout(resolve, typing ? 150 : 0));
  await new Promise(resolve => setImmediate(resolve));
  const issued = events.filter(x => x.endsWith('api_issue_from_doc'));
  assert.equal(issued.length, saved ? 1 : 0, 'Only a saved issue may be issued');
  assert(!events.some(x => x.includes('[object Object]')), 'No [object Object] message');
  if (!saved || serverError) assert(!events.some(x => x.startsWith('msgprint:')), 'Frappe already showed it');
  assert(events.includes('unfreeze'));
  if (typing) assert.deepEqual(events.slice(events.indexOf('blur'), events.indexOf('blur') + 2), ['blur', 'save:TOKEN'],
    'The value still being typed is committed before saving');
}
(async () => {
  for (const status of ['failed', 'partial', 'pending', 'running', 'done']) await scenario(status);
  await scenario('failed', true);
  await scenario('failed', true, true);
  await issueScenario({saved: false});
  await issueScenario({saved: true});
  await issueScenario({saved: true, serverError: true});
  await issueScenario({saved: true, typing: true});
  console.log('PASS 11 Desk action scenarios; no automatic retry, no issue before save, typed value saved');
})().catch(error => {console.error(error); process.exitCode = 1});
