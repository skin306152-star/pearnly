// Native Desk contracts, no browser/Site/HTTP. Execute real connect.js with UI adapters.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('apps/pearnly_dms/pearnly_dms/public/desk/connect.js', 'utf8');
const clone = (value) => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));

function harness({connected = true, endpoint = true} = {}) {
  const calls = [], forms = [], dialogs = [], handlers = {}, timers = new Map();
  const buttons = new Map();
  let serial = 0, freezes = 0, error = false, pending;
  const rows = ['masters', 'customers', 'geo'].map(dataset => ({dataset, state:'idle',
    progress:{processed:0,total:null}, count:null, as_of:0, completed_at:0, error_code:''}));
  const page = {main:{empty(){}}, clear_inner_toolbar(){buttons.clear();},
    set_primary_action(){}, set_indicator(){}, add_inner_button(label, fn){buttons.set(label, fn);}};
  const wrapper = {};
  const p = {
    async texts(prefix) {assert.equal(prefix,'dms.connect.cache.');return t;},
    async call(method, args = {}, type = 'GET') {
      calls.push({method, args:clone(args), type});
      if (method.endsWith('get_connection')) return {connected, endpoint:endpoint ? {
        name:'PRIMARY',enabled:connected,username:'fake-admin',has_admin_creds:true} : null};
      if (method.endsWith('get_status')) {
        if (error) throw new Error('denied');
        if (pending) { const wait = pending; pending = null; await wait; }
        return {configured:true, datasets:clone(rows)};
      }
      if (method.endsWith('request_refresh')) return {configured:true, datasets:args.datasets.map(dataset => ({
        dataset, state:'queued', progress:{processed:0,total:null}, as_of:0, completed_at:0}))};
      throw new Error('Unexpected external connection call: ' + method);
    },
    layout(parent, fields) {
      const form = {fields, values:{}, set_value(key,value){this.values[key]=value;}};
      forms.push(form); return form;
    },
    dialog(title, fields, label, action) {
      const dialog = {title,fields,label,hidden:false,hide(){this.hidden=true;},action};
      dialogs.push(dialog); return dialog;
    },
    escape(value){return String(value).replaceAll('<','&lt;');}, success(){},
  };
  const frappe = {pearnly:p,boot:{lang:'en'},dom:{freeze(){freezes++;},unfreeze(){}},
    msgprint(value){h.messages.push(value);},confirm(){throw new Error('Unexpected connection mutation');}};
  const t = (key, ...args) => `${key}${args.length ? ':' + args.join('|') : ''}`;
  vm.runInNewContext(source, {frappe,__:text=>text,console,
    $:value=>({on(event, fn){assert.equal(value,wrapper);handlers[event]=fn;}}),
    setTimeout(fn, ms){assert.equal(ms,3000);const id=++serial;timers.set(id,fn);return id;},
    clearTimeout(id){timers.delete(id);},
  });
  const h = {calls,forms,dialogs,handlers,timers,buttons,rows,messages:[],
    get freezes(){return freezes;},setError(value){error=value;},
    delayStatus(promise){pending=promise;},start:()=>p.connect(page,t,wrapper),
    async poll(){const [id,fn]=timers.entries().next().value;timers.delete(id);await fn();},
    choose:()=>buttons.get('dms.connect.cache.read')(),
  };
  return h;
}

(async () => {
  const h = harness(); await h.start();
  assert.equal(h.forms.length,2);
  const cache = h.forms[1];
  assert.equal(cache.fields.filter(f=>f.fieldtype==='Data').length,18);
  assert(cache.fields.filter(f=>f.fieldtype==='Data').every(f=>f.read_only===1));
  assert.equal(h.timers.size,1);
  assert.equal(h.calls.filter(c=>c.type==='POST').length,0);
  h.choose();
  const initial = h.dialogs[0];
  assert.deepEqual(Array.from(initial.fields.filter(f=>f.fieldtype==='Check'),f=>[f.fieldname,f.default]),
    [['masters',1],['customers',1],['geo',1]]);
  await initial.action({},initial);
  assert.equal(h.messages.length,1);
  assert.equal(initial.hidden,false);
  await initial.action({customers:1,geo:0,endpoint:'forged'},initial);
  assert.equal(initial.hidden,true);
  assert.deepEqual(h.calls.at(-1),{method:'pearnly_dms.connections.snapshots.request_refresh',
    args:{datasets:['customers']},type:'POST'});
  assert.equal(h.freezes,0,'Manual request must not use the long-operation page freeze');
  assert.equal(cache.values.customers_state,'dms.connect.cache.state.queued');
  assert.equal(h.timers.size,1);
  console.log('PASS native fields + first all selection + explicit subset POST + no endpoint injection / freeze');

  h.rows[1] = {dataset:'customers',state:'failed',count:18,as_of:1720000000,completed_at:1720000050,
    stale:true,progress:{processed:9,total:20},error_code:'ERR_DMS_SNAPSHOT_INCOMPLETE'};
  await h.poll();
  assert.equal(cache.values.customers_progress,'9 / 20');
  assert.equal(cache.values.customers_count,'18');
  assert.equal(cache.values.customers_error_code,'ERR_DMS_SNAPSHOT_INCOMPLETE');
  assert(cache.values.customers_as_of.includes(new Date(1720000000000).toLocaleString('en')));
  assert.equal(cache.values.customers_completed_at,new Date(1720000050000).toLocaleString('en'));
  h.choose();
  assert(h.dialogs.at(-1).fields.filter(f=>f.fieldtype==='Check').every(f=>f.default===0));
  assert.equal(h.calls.at(-1).method,'pearnly_dms.connections.snapshots.get_status');
  assert.equal(h.calls.at(-1).type,'GET');
  console.log('PASS true data/completion times, retained old-cache age on failure, subsequent selective choice');

  const beforeHide = h.calls.length;
  h.handlers.hide();
  assert.equal(h.timers.size,0);
  assert.equal(h.calls.length,beforeHide);
  h.handlers.show(); await tick();
  assert.equal(h.timers.size,1);
  assert.equal(h.calls.at(-1).method,'pearnly_dms.connections.snapshots.get_status');
  let release;
  h.delayStatus(new Promise(resolve=>{release=resolve;}));
  const pending = h.poll();
  h.handlers.hide(); h.handlers.show();
  h.rows[1].state='running';
  release(); await pending;
  assert.equal(cache.values.customers_state,'dms.connect.cache.state.failed','Hidden in-flight result must not repaint');
  assert.equal(h.timers.size,1,'Rapid hide/show still resumes one poll after old request completes');
  await h.poll();
  assert.equal(cache.values.customers_state,'dms.connect.cache.state.running');
  h.setError(true); await h.poll();
  assert.equal(h.timers.size,0,'Permission/status errors stop automatic retry');
  console.log('PASS native show/hide, stale-response rejection, no parallel polling, denied poll stops');

  for (const options of [{connected:false}, {connected:false,endpoint:false}]) {
    const disabled = harness(options); await disabled.start();
    assert.equal(disabled.buttons.has('dms.connect.cache.read'),false);
    assert.equal(disabled.calls.filter(c=>c.type==='POST').length,0);
    assert.equal(disabled.timers.size,0);
    assert.equal(disabled.calls.filter(c=>c.method.endsWith('get_status')).length,0);
  }
  console.log('PASS disconnected/unconfigured cannot request external reads');
})().catch(error=>{console.error(error);process.exitCode=1;});
