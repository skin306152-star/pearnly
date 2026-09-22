// 商品表单里"条码/二维码"合一的这一格：走真实 products.js，用假的 Dialog/jQuery。
// 不碰摄像头、不连 Site —— 断言的是"码型怎么决定落哪一列"和"没动过的历史值不被清掉"。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(
  'apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/products.js', 'utf8');

function element() {
  const node = {
    classes: [], handlers: {}, appended: [], props: {},
    addClass(name) { node.classes.push(name); return node; },
    append(child) { node.appended.push(child); return node; },
    on(event, handler) { node.handlers[event] = handler; return node; },
    off() { node.handlers = {}; return node; },
    find() { return node; },
    prop(name, value) { node.props[name] = value; return node; },
    get_value() { return undefined; },
  };
  return node;
}

const NEW_FIELDS = ['product_name', 'code', 'code_value', 'unit', 'base_unit', 'category_id',
  'default_warehouse', 'default_location', 'unit_price', 'default_cost', 'min_stock',
  'track_batch', 'track_expiry', 'vat_applicable', 'image_url'];

function boot({ product = null, supported = true, can_write = true } = {}) {
  const calls = [], messages = [], scans = [], dialogs = [], editAll = [];
  const inputs = {}, controls = {}, values = {};
  let open_create, open_form, list_options;

  const p = {
    money: value => String(value),
    escape: value => String(value),
    success() {},
    call: async (method, args) => { calls.push({ method, args }); return product; },
    scan: options => { scans.push(options); return Promise.resolve(); },
    scan_supported: () => supported,
    // 弹窗左下角那颗"全屏编辑"由共享层的 `p.edit_all` 提供（核心实现用官方的
    // `dialog.add_custom_action`）。这里按同一份契约模拟：记下调用，并让测试能"点"它。
    edit_all: (dialog, label, route, carry) => {
      const entry = { label, route, values: dialog.values };
      entry.click = () => { dialog.hide(); carry(entry.values); };
      editAll.push(entry);
    },
    subroute: (root, ...parts) => "/desk/" + [root, ...parts].filter(Boolean).join("/"),
    clear_page: () => {},
    breadcrumbs: () => {},
    routed_page: ({ render }) => async () => { await render([]); },
    // 选中操作那套由共享层提供（tests/test-selection-action.cjs 单独验）；这里只要它存在。
    selection_action: () => () => {},
    list: config => {
      list_options = config;
      return { ready: Promise.resolve(), reload: async () => {}, get_checked_items: () => [],
        clear_checked_items() {} };
    },
    dialog: (title, fields, label, on_save) => {
      const by_name = {};
      for (const field of fields) {
        if (!field.fieldname) continue;
        by_name[field.fieldname] = { df: field,
          $wrapper: controls[field.fieldname] = element(),
          $input: inputs[field.fieldname] = element() };
      }
      // 故意不提供 set_secondary_action：底部再挂一颗扫码按钮会直接抛错。
      const primary = element();
      const dialog = { title, fields, label, on_save, values, by_name,
        $wrapper: { find: () => element() },
        hide() {},
        get_field: name => by_name[name],
        get_value: name => values[name],
        set_value: async (name, value) => { values[name] = value; },
        add_custom_action: (label, action) => { dialog.custom = dialog.custom || []; dialog.custom.push({ label, action }); },
        get_primary_btn: () => primary };
      dialogs.push(dialog);
      return dialog;
    },
  };
  const page = {
    add_field: () => element(),
    add_inner_button() {},
    set_primary_action: (_label, action) => { open_create = action; },
  };
  const frappe = { pearnly: p, model: { can_create: () => true, can_write: () => can_write },
    msgprint: message => messages.push(message) };
  // $ 只用来建那颗图标按钮：把 HTML 留下来给断言看。
  vm.runInNewContext(source, { frappe,
    $: html => { const node = element(); node.html = html; return node; } });
  return { p, page, calls, messages, scans, dialogs, inputs, controls, values,
    get editAll() { return editAll; },
    async ready() { await p.products(page, key => key); },
    get list_options() { return list_options; },
    get open_create() { return open_create; },
    get open_form() { return open_form; } };
}

async function open_new(options = {}) {
  const env = boot(options);
  await env.ready();
  await env.open_create();
  return { env, dialog: env.dialogs.at(-1) };
}

async function open_existing(product, options = {}) {
  const env = boot({ product, ...options });
  await env.ready();
  await env.list_options.on_open({ name: product.name });
  return { env, dialog: env.dialogs.at(-1) };
}

const saved = (env, method) => {
  const call = env.calls.filter(entry => entry.method.endsWith(method)).at(-1);
  assert.ok(call, `expected a call to ${method}`);
  return call.args;
};
// 服务端参数对象是在 vm 那个 realm 里造的：比之前先摊平成本地对象。
const plain = value => JSON.parse(JSON.stringify(value));

(async () => {
  // 1) 表单形状：一格里放条码/二维码，字段既不重复也不再一行一个 Section。
  const { env, dialog } = await open_new();
  // spread 一次：字段数组来自 vm 里的另一个 realm，直接 deepEqual 会比原型。
  const names = [...dialog.fields.filter(field => field.fieldname)
    .map(field => field.fieldname)];
  assert.deepEqual(names, NEW_FIELDS);
  assert.ok(!names.includes('barcode') && !names.includes('qr_payload'),
    '库里两列都保留，但表单上只有合一的一格');
  const code_field = dialog.fields.find(field => field.fieldname === 'code_value');
  assert.equal(code_field.label, 'product.field.codeValue');
  assert.equal(code_field.length, 500, '合并后这一格要能收下服务端上限内的长二维码');
  assert.equal(dialog.fields.filter(field => field.fieldtype === 'Section Break').length, 2,
    '整页只有两个 Section：一行一个 Section 是之前留白的来源');
  assert.equal(dialog.fields.filter(field => field.fieldtype === 'Column Break').length, 2);
  assert.equal(typeof dialog.set_secondary_action, 'undefined');
  console.log('PASS one merged code field, two native sections, no footer scan button');

  // 1b) 新建弹窗左下角那颗"全屏编辑"：官方 QuickEntryForm 用的同一颗 custom action，
  //     点它去二级页面（商品新建支持一次多行）。
  assert.equal(env.editAll.length, 1, '新建弹窗要挂一颗全屏编辑');
  assert.equal(env.editAll[0].label, 'product.action.fullPage');
  assert.equal(env.editAll[0].route, '/desk/pearnly-products/new');
  console.log('PASS the quick dialog offers full-page editing on the second-level route');

  // 2) 扫码入口是那一格里的 SVG 图标按钮。
  const control = env.controls.code_value;
  assert.deepEqual(control.classes, ['pearnly-code-input']);
  assert.equal(control.appended.length, 1);
  const icon = control.appended[0];
  assert.match(icon.html, /^<button type="button" class="pearnly-scan-icon"/);
  assert.match(icon.html, /<svg/);
  assert.match(icon.html, /aria-label="product\.action\.scan"/);
  assert.equal(typeof icon.handlers.click, 'function');
  console.log('PASS scan entry is the inline SVG icon inside the field');

  // 3) 二维码（哪怕内容全是数字）落二维码那一列，并当场查重。
  icon.handlers.click();
  const scan = env.scans.at(-1);
  assert.equal(scan.kind, 'all');
  await scan.on_result('8850000009912', { format: 'qr_code', kind: 'qr' });
  assert.equal(dialog.values.code_value, '8850000009912');
  assert.deepEqual(plain(env.calls.at(-1)), {
    method: 'pearnly_erp_portal.products.service.api_find_product',
    args: { value: '8850000009912', exclude: '' } });
  await dialog.on_save({ ...dialog.values, product_name: '新品' }, dialog);
  let payload = saved(env, 'api_create_product');
  assert.equal(payload.qr_payload, '8850000009912');
  assert.equal(payload.barcode, '');
  assert.ok(!('code_value' in payload), '界面那一格的名字不能发给服务端');
  assert.equal(payload.product_name, '新品');

  // 4) 条码落条码那一列，另一列清空。
  const second = await open_new();
  second.env.controls.code_value.appended[0].handlers.click();
  await second.env.scans.at(-1).on_result('00123456', { format: 'ean_8', kind: 'barcode' });
  await second.dialog.on_save({ ...second.dialog.values, product_name: 'B' }, second.dialog);
  payload = saved(second.env, 'api_create_product');
  assert.equal(payload.barcode, '00123456');
  assert.equal(payload.qr_payload, '');
  console.log('PASS scan kind decides the column: numeric QR stays QR, barcode keeps leading zeros');

  // 5) 手输（扫码枪只有文字）按内容判断：短 ASCII 归条码，长串/非 ASCII 归二维码。
  for (const [typed, column] of [['8850000009912', 'barcode'], ['00123456', 'barcode'],
    ['https://shop.example/p/1', 'qr_payload'], ['盘点 QR 001', 'qr_payload'],
    ['A'.repeat(120), 'qr_payload']]) {
    const fifth = await open_new();
    await fifth.dialog.on_save({ ...fifth.dialog.values, code_value: typed, product_name: 'T' },
      fifth.dialog);
    const sent = saved(fifth.env, 'api_create_product');
    assert.equal(sent[column], typed, `${typed.slice(0, 24)} should land in ${column}`);
    assert.equal(sent[column === 'barcode' ? 'qr_payload' : 'barcode'], '');
  }
  console.log('PASS typed values route by shape and never exceed the barcode length limit');

  // 6) 老数据：只动别的字段时，原来那一列原样回写、另一列不碰（历史双写值不丢）。
  const legacy = { name: 'P-1', product_name: '旧品', barcode: null, qr_payload: 'QR-OLD',
    price_set: 0, cost_set: 0, unit: '瓶', base_unit: '瓶' };
  const third = await open_existing(legacy);
  assert.equal(third.dialog.fields.find(field => field.fieldname === 'code_value').default,
    'QR-OLD');
  await third.dialog.on_save({ code_value: 'QR-OLD', product_name: '旧品', unit_price: '' },
    third.dialog);
  payload = saved(third.env, 'api_update_product');
  assert.equal(payload.name, 'P-1');
  assert.equal(payload.qr_payload, 'QR-OLD');
  assert.equal(payload.unit_price, null);
  assert.ok(!('barcode' in payload), '没动这一格就不该回写另一列');

  // 7) 老数据改了这一格：落新的一列，旧的那一列显式清空。
  const fourth = await open_existing(legacy);
  await fourth.dialog.on_save({ code_value: '8850000009912', product_name: '旧品' },
    fourth.dialog);
  payload = saved(fourth.env, 'api_update_product');
  assert.equal(payload.barcode, '8850000009912');
  assert.equal(payload.qr_payload, '');

  const sixth = await open_existing({ ...legacy, barcode: 'BC-1', qr_payload: null });
  await sixth.dialog.on_save({ code_value: 'https://shop.example/qr', product_name: '旧品' },
    sixth.dialog);
  payload = saved(sixth.env, 'api_update_product');
  assert.equal(payload.qr_payload, 'https://shop.example/qr');
  assert.equal(payload.barcode, '');
  console.log('PASS untouched legacy values survive; edits clear the stale other column');

  // 8) 输入框里的扫码枪回车不会把表单存了。
  let prevented = false, stopped = false;
  env.inputs.code_value.handlers['keydown keypress']({ key: 'Enter',
    preventDefault() { prevented = true; }, stopPropagation() { stopped = true; } });
  assert.ok(prevented && stopped);
  console.log('PASS scanner Enter cannot submit the product form');

  // 9) 浏览器没有摄像头接口时不装作能扫。
  const nod = await open_new({ supported: false });
  nod.env.controls.code_value.appended[0].handlers.click();
  assert.deepEqual(nod.env.messages, ['product.scan.unsupported']);
  assert.equal(nod.env.scans.length, 0);

  // 10) 扫到别人已经占了的码：当场说，并给"去改那一条"的出路。
  const taken = await open_new({ product: { name: 'P-9', product_name: '别人的品', code: 'P-9' } });
  taken.env.controls.code_value.appended[0].handlers.click();
  await taken.env.scans.at(-1).on_result('8850000009912', { format: 'ean_13', kind: 'barcode' });
  assert.equal(taken.env.messages[0].title, 'product.scan.taken');
  assert.equal(taken.env.messages[0].primary_action.label, 'product.scan.open');
  console.log('PASS unsupported camera falls back to typing; taken codes warn in place');

  // 11) 只读的岗位不挂扫码图标（扫了也存不了）。
  const readonly = await open_existing(legacy, { can_write: false });
  assert.deepEqual(readonly.env.controls.code_value.classes, []);
  assert.equal(readonly.env.controls.code_value.appended.length, 0);
  assert.equal(readonly.dialog.get_primary_btn().props.disabled, true);

  // 12) 列表那一列：名字跟表单一样，值在条码与二维码之间取有值的那个。
  const column = env.list_options.columns.find(entry => entry.fieldname === 'barcode');
  assert.equal(column.label, 'product.col.codeValue');
  assert.equal(column.format(undefined, { barcode: '', qr_payload: 'QR-1' }), 'QR-1');
  assert.equal(column.format(undefined, { barcode: 'BC-1', qr_payload: 'QR-1' }), 'BC-1');
  assert.equal(column.format(undefined, {}), '');
  console.log('PASS read-only forms skip the icon; list column is Barcode / QR code');
})().catch(error => { console.error(error); process.exitCode = 1; });
