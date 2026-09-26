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
    __: text => text,
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
async function issueScenario({saved, serverError = false}) {
  const events = [], buttons = new Map();
  const context = vm.createContext({
    __: text => text,
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
  const frm = {doc: {name: 'new-pearnly-issue-x', status: 'draft', __islocal: 1}, page: {set_indicator() {}},
    is_new: () => !!frm.doc.__islocal, is_dirty: () => !!frm.doc.__islocal,
    add_custom_button: (label, fn) => buttons.set(label, fn),
    save: async () => {events.push('save'); if (saved) frm.doc = {name: 'ISS-1', status: 'draft'}},
    reload_doc: async () => events.push('reload')};
  context.render_provision(frm);
  context.start_provision = () => events.push('provision');
  await buttons.get('Issue and provision')();
  await new Promise(resolve => setImmediate(resolve));
  const issued = events.filter(x => x.endsWith('api_issue_from_doc'));
  assert.equal(issued.length, saved ? 1 : 0, 'Only a saved issue may be issued');
  assert(!events.some(x => x.includes('[object Object]')), 'No [object Object] message');
  if (!saved || serverError) assert(!events.some(x => x.startsWith('msgprint:')), 'Frappe already showed it');
  assert(events.includes('unfreeze'));
}
(async () => {
  for (const status of ['failed', 'partial', 'pending', 'running', 'done']) await scenario(status);
  await scenario('failed', true);
  await scenario('failed', true, true);
  await issueScenario({saved: false});
  await issueScenario({saved: true});
  await issueScenario({saved: true, serverError: true});
  console.log('PASS 10 Desk action scenarios; no automatic retry, no issue before save');
})().catch(error => {console.error(error); process.exitCode = 1});
