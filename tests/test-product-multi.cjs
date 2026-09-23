// 商品新建的**二级页面**（一次建多个）：走真实 products.js，用假的 FieldGroup/Grid/DIALOG。
// 断言的是这一页自己的规则 —— 空行不发请求、条码/二维码按形状落列、"没填价"不能变成
// "设过价为 ฿0"、建成功的行从表里去掉、失败的行留下可以改。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(
  'apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/products.js', 'utf8');

function element() {
  const node = {
    classes: [], handlers: {}, appended: [], html: '',
    addClass(name) { node.classes.push(name); return node; },
    append(child) { node.appended.push(child); return node; },
    on(event, handler) { node.handlers[event] = handler; return node; },
    off() { node.handlers = {}; return node; },
    find() { return node; },
    empty() { return node; },
    prop() { return node; },
    attr() { return node; },
    get_value() { return undefined; },
  };
  return node;
}

// 弹窗里那套字段（去掉只读自动生成的 code），二级页面的列要与它对得上。
const DIALOG_FIELDS = ['product_name', 'code', 'code_value', 'unit', 'base_unit', 'category_id',
  'default_warehouse', 'default_location', 'unit_price', 'default_cost', 'min_stock',
  'track_batch', 'track_expiry', 'vat_applicable', 'image_url'];

function boot({ create = async () => ({ name: 'P-1' }) } = {}) {
  const calls = [], messages = [], routes = [], successes = [], scans = [], sounds = [];
  const rows = [];
  const customButtons = element();
  let save, tableField, grid;

  const p = {
    // 仓库/收银台的 Link 过滤来自 `transactions.js`（这几次测试只加载某几个页面脚本），
    // 所以在这里补一个同形状的桩：返回框架 Link 认的过滤对象。
    warehouse_query: () => ({filters: {}}),
    escape: value => String(value ?? ''),
    money: value => String(value),
    success: message => successes.push(message),
    // 二级页面进来先要一次**编码预览**（服务端不占号），再谈建。
    call: async (method, args) => {
      calls.push({ method, args });
      if (String(method).endsWith('api_next_product_code')) return { code: 'P-000042' };
      // 扫码查重要显式给"没查到"：桩的默认回值是"建成功了"（`{name: 'P-1'}`），拿它当查重
      // 结果会把每一个新码都当成别人的（这一条同时决定扫码后放哪一声）。
      if (String(method).endsWith('api_find_product')) return null;
      return create(method, args);
    },
    scan: options => { scans.push(options); return Promise.resolve(); },
    scan_supported: () => true,
    // 结果那一声由 `scan.js` 的 `p.scan_result_sound` 负责（成功 `true` / 失败 `false`）。
    scan_result_sound: ok => { sounds.push(ok); },
    selection_action: () => () => {},
    list: () => ({ ready: Promise.resolve(), reload: async () => {}, get_checked_items: () => [], clear_checked_items() {} }),
    dialog: () => ({ hide() {}, get_field: () => ({ $wrapper: element(), $input: element() }), set_value: async () => {} }),
    edit_all: () => {},
    subroute: (root, ...parts) => '/desk/' + [root, ...parts].filter(Boolean).join('/'),
    clear_page: () => {},
    breadcrumbs: () => {},
    redirect_up: (...parts) => { routes.push(parts); },
    // 这个测试只关心二级页面，所以进来就直接把它当成"当前是 /new"。
    routed_page: ({ render }) => async () => { await render(['new']); },
    layout: (_parent, fields) => {
      tableField = fields.find(field => field.fieldtype === 'Table');
      rows.splice(0, rows.length, ...(tableField.data || []));
      // 与真实 Grid 一致：`get_data()` 读的就是 `df.data`，所以页面把 `df.data` 换成
      // "没建成功的那几行"之后，再读出来就是那几行。
      grid = {
        df: { data: rows },
        wrapper: { find: selector => (selector === '.grid-custom-buttons' ? customButtons : element()) },
        refresh() {},
        add_new_row() { grid.df.data.push({ idx: grid.df.data.length + 1, __islocal: true, vat_applicable: 1 }); },
        get_data() { return grid.df.data; },
      };
      return { fields_dict: { products: { grid } },
        get_value: name => (name === 'products' ? grid.get_data() : undefined),
        get_values: () => ({}), set_value: async () => {} };
    },
  };
  const page = {
    add_field: () => element(),
    add_inner_button() {},
    add_refresh() { return element(); },
    clear_primary_action() {},
    set_primary_action: (_label, action) => { if (action) save = action; },
  };
  const frappe = {
    pearnly: p,
    model: { can_create: () => true, can_write: () => true },
    msgprint: message => messages.push(message),
    set_route: (...route) => routes.push(route),
    get_route: () => ['pearnly-products', 'new'],
    datetime: { get_today: () => '2026-09-22' },
  };
  vm.runInNewContext(source, {
    frappe,
    __: key => key,
    $: html => { const node = element(); node.html = html; return node; },
  });
  return { calls, messages, routes, successes, scans, sounds, customButtons,
    get rows() { return grid.df.data; },
    get tableField() { return tableField; },
    async ready() { await p.products(page, key => key); },
    // 页面那颗主按钮是 `() => void save_all()`（原生 Page 的动作本来就不接 promise），
    // 所以这里点完之后让微任务跑干净，再断言 —— 不然断言会跑在保存完成之前。
    async save() {
      save();
      let seen = -1;
      for (let i = 0; i < 500; i += 1) {
        await Promise.resolve();
        const now = calls.length + messages.length + routes.length + successes.length;
        if (now === seen && i > 10) return;
        seen = now;
      }
    } };
}

const creates = env => env.calls.filter(call => call.method.endsWith('api_create_product'));
const plain = value => JSON.parse(JSON.stringify(value));

const row = (product_name, extra = {}) => ({ product_name, vat_applicable: 1,
  unit_price: 10, ...extra });

(async () => {
  // 1) 列与弹窗同一套字段（`code` 除外：编码由服务端发）。
  const env = boot();
  await env.ready();
  const names = [...env.tableField.fields.map(field => field.fieldname)];
  assert.deepEqual(names.slice().sort(), DIALOG_FIELDS.filter(name => name !== 'code').sort(),
    '二级页面的字段要和弹窗一样，只少那个只读的自动编码');
  assert.equal(env.tableField.in_place_edit, true, '原生 Grid 要在格子里直接编辑');
  assert.equal(env.customButtons.appended.length, 1, '扫码入口挂在 Grid 自己的插槽里');
  assert.match(env.customButtons.appended[0].html, /pearnly-scan-icon/);
  console.log('PASS the multi-row page mirrors the dialog fields and keeps the native grid');

  // 2) 空行不发请求；条码/二维码按形状落列；建成功后回到列表。
  env.rows.splice(0, env.rows.length,
    row('多行甲', { code_value: '8850000009913', unit: '瓶', unit_price: 12.5 }),
    row('多行乙', { code_value: '二维码-多行-乙', unit_price: 3 }),
    { vat_applicable: 1 });
  await env.save();
  const payloads = creates(env).map(call => plain(call.args));
  assert.equal(payloads.length, 2, '空行不该发请求');
  assert.equal(payloads[0].barcode, '8850000009913');
  assert.equal(payloads[0].qr_payload, '');
  assert.equal(payloads[0].unit_price, 12.5);
  assert.equal(payloads[1].qr_payload, '二维码-多行-乙');
  assert.equal(payloads[1].barcode, '');
  assert.ok(!('code_value' in payloads[0]), '界面那一格的名字不能发给服务端');
  assert.equal(env.successes.at(-1), 'product.notice.savedCount');
  assert.deepEqual(env.routes.at(-1), ['pearnly-products'], '建完回列表');
  console.log('PASS only filled rows are sent, codes land by shape, and it goes back to the list');

  // 3) "没填价"不能变成"设过价为 ฿0"：Grid 的数字格读不出空（空就是 0），所以 0 视为未设。
  const zero = boot();
  await zero.ready();
  zero.rows.splice(0, zero.rows.length, row('零元', { unit_price: 0, default_cost: 0 }));
  await zero.save();
  const zeroPayload = plain(creates(zero)[0].args);
  assert.equal(zeroPayload.unit_price, null);
  assert.equal(zeroPayload.default_cost, null);
  console.log('PASS a blank price stays "not set" instead of becoming a real zero');

  // 4) 全空：说清楚"没有要保存的行"，一个请求都不发。
  const empty = boot();
  await empty.ready();
  empty.rows.splice(0, empty.rows.length, { vat_applicable: 1 });
  await empty.save();
  assert.equal(creates(empty).length, 0);
  assert.equal(empty.messages.at(-1).message, 'product.notice.nothingToSave');
  assert.equal(empty.routes.length, 0, '没建东西就别跳走');

  // 5) 部分失败：失败那一行留在表里、不跳走，成功的也不重复建。
  const partial = boot({ create: async (method, args) => {
    if (args.product_name === '坏行') throw new Error('server said no');
    return { name: 'P-9' };
  } });
  await partial.ready();
  partial.rows.splice(0, partial.rows.length, row('好行'), row('坏行'));
  await partial.save();
  assert.equal(creates(partial).length, 2);
  assert.equal(partial.rows.length, 1, '只剩失败那一行');
  assert.equal(partial.rows[0].product_name, '坏行');
  assert.equal(partial.messages.at(-1).message, 'product.notice.partial');
  assert.equal(partial.routes.length, 0, '有失败就别跳走');
  console.log('PASS a failed row stays editable and nothing navigates away');

  // 6) 扫码 = 新加一行并把码写进那一行（码型跟着解码器给的结果）。
  assert.equal(typeof env.customButtons.appended[0].handlers.click, 'function');
  env.customButtons.appended[0].handlers.click();
  const scan = env.scans.at(-1);
  assert.equal(scan.kind, 'all');
  const before = env.rows.length;
  await scan.on_result('QR-扫描-1', { kind: 'qr' });
  assert.equal(env.rows.length, before + 1);
  assert.equal(env.rows.at(-1).code_value, 'QR-扫描-1');
  assert.equal(env.rows.at(-1).__scanned, 'qr');
  assert.deepEqual(env.sounds, [true], '扫完放一声成功音（码是新的时候）');
  console.log('PASS scanning adds a row and remembers the scanned code kind');
})().catch(error => { console.error(error); process.exitCode = 1; });
