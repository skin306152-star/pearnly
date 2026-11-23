// Exercise the real roster callbacks without deleting user-owned preview records.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('apps/pearnly_dms/pearnly_dms/public/desk/roster.js', 'utf8');
async function fixture(failAt) {
  const calls = [], notices = [];
  let options, confirm, primary;
  let selected = [{name:'operator-1',display_name:'Same name'}, {name:'operator-2',display_name:'Same name'}, {name:'operator-3',display_name:'Third'}];
  let disabled = false;
  const list = {ready:Promise.resolve(), get_checked_items:()=>selected,
    async reload() { selected=selected.filter(row=>!calls.some(call=>call.name===row.name&&call.ok)); options.on_selection(selected); }};
  let searchField, searchValue='';
  const page = {set_primary_action:(label,action)=>{primary={label,action};}, add_action_icon(){},set_title_sub(){},
    add_field(df){searchField={df,get_value:()=>searchValue};return searchField;},
    btn_primary:{prop(key,value){assert.equal(key,'disabled');disabled=value;}}};
  const p = {escape:String, add_refresh(){}, success:msg=>notices.push(msg), list:opts=>{options=opts;return list;},
    async call(method,args,type) {
      if(method==='pearnly_dms.operators.roster.api_list_operators'){calls.push({list:args});return {items:[],total:0};}
      assert.equal(method,'pearnly_dms.operators.roster.api_delete_operator');
      assert.equal(type,'POST');
      const ok=args.name!==failAt;
      calls.push({name:args.name,ok});
      if(!ok) throw {responseJSON:{message:'fixture denial'}};
      return {deleted:args.name};
    }};
  vm.runInNewContext(source,{frappe:{pearnly:p,confirm:(message,action)=>{confirm={message,action};}},__:String,console});
  await p.roster(page,(key,...args)=>[key,...args].join(':'));
  return {calls,notices,list,get searchField(){return searchField;},setSearch:v=>{searchValue=v;},get options(){return options;},get confirm(){return confirm;},get primary(){return primary;},get disabled(){return disabled;}};
}
(async()=>{
  // Search box: a page-head field whose value rides along with the list's own paging arguments.
  const searching=await fixture();
  assert.equal(searching.searchField.df.fieldname,'search');
  assert.equal(searching.searchField.df.label,'Search');
  await searching.options.load({start:0,page_length:20});
  assert.deepEqual({...searching.calls.pop().list},{start:0,page_length:20,search:undefined});
  searching.setSearch('Somchai');
  await searching.options.load({start:20,page_length:20});
  assert.deepEqual({...searching.calls.pop().list},{start:20,page_length:20,search:'Somchai'});
  const single=await fixture();
  single.options.row_actions(single.list.get_checked_items()[0]).find(a=>a.id==='delete').action(single.list.get_checked_items()[0]);
  assert.equal(single.calls.length,0,'confirmation precedes deletion');
  assert.match(single.confirm.message,/roster.confirm.delete:Same name/);
  await single.confirm.action();
  assert.deepEqual(single.calls,[{name:'operator-1',ok:true}]);

  const batch=await fixture();
  batch.options.on_selection(batch.list.get_checked_items());
  assert.equal(batch.primary.label,'roster.delete');
  batch.primary.action();
  assert.match(batch.confirm.message,/deleteSelected:3/);
  await batch.confirm.action();
  assert.deepEqual(batch.calls.map(c=>c.name),['operator-1','operator-2','operator-3']);
  assert.equal(batch.primary.label,'roster.add');
  assert.equal(batch.disabled,false);
  assert.deepEqual(batch.notices,['roster.notice.deletedCount:3']);

  const partial=await fixture('operator-2');
  partial.options.on_selection(partial.list.get_checked_items());
  partial.primary.action();
  await partial.confirm.action();
  assert.deepEqual(partial.calls,[{name:'operator-1',ok:true},{name:'operator-2',ok:false}]);
  assert.deepEqual(partial.list.get_checked_items().map(r=>r.name),['operator-2','operator-3']);
  assert.equal(partial.primary.label,'roster.delete');
  assert.equal(partial.disabled,false);
  assert.deepEqual(partial.notices,['roster.notice.deletedCount:1']);
  console.log('PASS single/bulk same API, confirmation, duplicate names, sequential deletion, partial failure preserves remaining selection');
})().catch(error=>{console.error(error);process.exitCode=1;});
