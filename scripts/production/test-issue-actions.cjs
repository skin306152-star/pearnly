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
    save: async () => {events.push('save'); if (failSave) throw Error('save failed')},
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
(async () => {
  for (const status of ['failed', 'partial', 'pending', 'running', 'done']) await scenario(status);
  await scenario('failed', true);
  await scenario('failed', true, true);
  console.log('PASS 7 Desk retry scenarios; no automatic retry or new issue');
})().catch(error => {console.error(error); process.exitCode = 1});
