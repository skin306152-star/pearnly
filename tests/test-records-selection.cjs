// Execute the actual page callbacks; no writes to user-owned preview records.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('apps/pearnly_dms/pearnly_dms/public/desk/records.js', 'utf8');
async function fixture(fail = false) {
  let options, primary, confirmation, selected = [], disabled = false;
  const calls = [], notices = [];
  const list = {ready:Promise.resolve(), get_checked_items:()=>selected,
    async reload() {
      selected = selected.filter(row=>!calls.some(call=>call.names.includes(row.name)));
      options.on_selection(selected);
    }};
  const page = {add_field:()=>({get_value:()=>''}),
    set_primary_action:(label,action)=>{primary={label,action};},
    clear_primary_action:()=>{primary=null;},
    btn_primary:{prop(key,value){assert.equal(key,'disabled');disabled=value;}}};
  const p = {escape:String, add_refresh(){}, success:msg=>notices.push(msg),
    list:opts=>{options=opts;return list;},
    async call(method,args,type) {
      assert.equal(method,'pearnly_dms.records.manage.delete_records');
      assert.equal(type,'POST');
      if(fail) throw {responseJSON:{message:'fixture denial'}};
      calls.push({names:Array.from(args.names)});
      return {deleted:args.names.length};
    }};
  vm.runInNewContext(source,{frappe:{pearnly:p,confirm:(message,action,reject)=>{
    confirmation={message,action,reject};return confirmation;
  }},__:String,console});
  await p.records(page,(key,...args)=>[key,...args].join(':'));
  return {calls,notices,list,
    select(names){selected=names.map(name=>({name}));options.on_selection(selected);},
    get primary(){return primary;},get confirmation(){return confirmation;},get disabled(){return disabled;}};
}
(async()=>{
  const cancelled=await fixture();
  cancelled.select([]);assert.equal(cancelled.primary,null);
  cancelled.select(['log-1']);assert.equal(cancelled.primary.label,'Delete');
  cancelled.primary.action();assert.equal(cancelled.disabled,true);
  assert.equal(cancelled.calls.length,0,'no deletion before confirmation');
  cancelled.confirmation.onhide();assert.equal(cancelled.disabled,false);
  assert.equal(cancelled.calls.length,0,'X/Escape does not delete');

  const single=await fixture();single.select(['log-1']);single.primary.action();
  assert.match(single.confirmation.message,/confirmDelete:1/);
  await single.confirmation.action();
  assert.deepEqual(single.calls,[{names:['log-1']}]);assert.equal(single.primary,null);

  const batch=await fixture();batch.select(['log-1','log-2','log-1']);batch.primary.action();
  assert.match(batch.confirmation.message,/confirmDelete:2/);
  batch.select(['log-3']); // confirmation is bound to the originally selected IDs
  await batch.confirmation.action();
  assert.deepEqual(batch.calls,[{names:['log-1','log-2']}]);
  assert.deepEqual(batch.list.get_checked_items(),[{name:'log-3'}]);
  assert.deepEqual(batch.notices,['connect.records.deletedCount:2']);assert.equal(batch.disabled,false);

  const failed=await fixture(true);failed.select(['log-1','log-2']);failed.primary.action();
  await failed.confirmation.action();
  assert.deepEqual(failed.list.get_checked_items(),[{name:'log-1'},{name:'log-2'}]);
  assert.equal(failed.primary.label,'Delete');assert.equal(failed.disabled,false);
  assert.equal(failed.notices.length,0);
  failed.select([]);assert.equal(failed.primary,null);
  console.log('PASS selection action, cancel, single/batch deletion, ID snapshot and failure preserves selection');
})().catch(error=>{console.error(error);process.exitCode=1;});
