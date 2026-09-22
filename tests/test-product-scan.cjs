// Exercise the actual product Dialog callback with decoder metadata. No camera or Site.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/products.js','utf8');

(async () => {
  let create, dialog, scan;
  const messages = [], lookups = [], values = {barcode:'old-barcode',qr_payload:'old-QR'};
  const inputs = {};
  const p = {
    list: () => ({ready:Promise.resolve()}),
    scan_supported: () => true,
    scan: options => { scan = options; },
    call: async (method,args) => { lookups.push({method,args}); return null; },
    dialog: (_title, fields) => {
      for (const field of fields.filter(f => f.fieldname)) {
        inputs[field.fieldname] = { off() { return this; }, on(_events,handler) { this.handler=handler; return this; } };
      }
      dialog = {
        $wrapper:{find:()=>({addClass(){}})},
        set_secondary_action_label(label) {this.label=label;},
        set_secondary_action(action) {this.scan=action;},
        get_field: name => ({$input:inputs[name]}),
        set_value: async (name,value) => { values[name]=value; },
      };
      return dialog;
    },
  };
  const page = { add_field: () => ({get_value(){}}), add_inner_button(){},
    set_primary_action: (_label,action) => {create=action;} };
  vm.runInNewContext(source,{frappe:{pearnly:p,model:{can_create:()=>true},msgprint: message=>messages.push(message)}});
  await p.products(page,key=>key); await create();
  assert.equal(dialog.label,'product.scan.auto');
  dialog.scan(); assert.equal(scan.kind,'all');
  await scan.on_result('8850000009912',{kind:'qr',format:'qr_code'});
  assert.equal(values.qr_payload,'8850000009912'); assert.equal(values.barcode,'old-barcode');
  await scan.on_result('00123456',{kind:'barcode',format:'ean_8'});
  assert.equal(values.barcode,'00123456'); assert.equal(values.qr_payload,'8850000009912');
  await scan.on_result(' QR  inner space ',{kind:'qr',format:'qr_code'});
  assert.equal(values.qr_payload,' QR  inner space ');
  assert.equal(lookups.length,3,'Both detected formats retain duplicate-product checking');
  assert.equal(lookups[0].args.value,'8850000009912');
  await scan.on_result('123',{kind:null});
  assert.equal(messages[0],'product.scan.unknownType'); assert.equal(values.barcode,'00123456');
  for (const name of ['barcode','qr_payload']) {
    let prevented=false,stopped=false;
    inputs[name].handler({key:'Enter',preventDefault(){prevented=true;},stopPropagation(){stopped=true;}});
    assert.ok(prevented && stopped,'Scanner Enter cannot submit the product');
  }
  console.log('PASS product scan routes by format, keeps the other field, checks duplicates and blocks scanner Enter');
})().catch(error=>{console.error(error);process.exitCode=1;});
