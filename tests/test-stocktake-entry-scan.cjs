// 盘点录入：扫码命中之后那一段（不需要相机硬件）。
// 10-28 polymer：扫码命中、这个仓账上没有、连续盘点——录入框必须出来、商品名和实际仓库带对；
// 没认出来（按台管理商品没登记、服务端不认、查找出错）不能让同一个码被镜头反复读、反复查，
// 出错时说"查找失败"，不冒充"找不到"。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ERP = 'apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/stocktake-entry.js';
// Cowork 那份盘点录入是同一套读码与失败处理（没有按台管理），一起守住。
const COWORK = 'apps/pearnly_cowork/pearnly_cowork/public/desk/stocktake-entry.js';
const flush = () => new Promise(resolve => setImmediate(resolve));

// 极简 jQuery：只够这一页用（文本、显隐、事件）。
function $(html) {
  const node = { html: String(html || ''), _text: '', children: [], handlers: {} };
  const api = {
    node, text(value) { if (value === undefined) return [node._text, ...node.children.map(c => c.node ? c.text() : '')].join(''); node._text = String(value); return api; },
    empty() { node.children = []; node._text = ''; return api; }, append(child) { node.children.push(child); return api; },
    appendTo(parent) { parent?.append?.(api); return api; }, addClass() { return api; }, attr() { return api; },
    on(name, fn) { node.handlers[name] = fn; return api; }, off() { return api; }, one() { return api; },
    find() { return api; }, get() { return {}; }, toggle(show) { node.visible = show; return api; },
    val(value) { if (value === undefined) return node.value ?? ''; node.value = value; return api; }, trigger() { return api; },
  };
  return api;
}

function fixture({ lookups, file = ERP }) {
  const source = fs.readFileSync(file, 'utf8');
  const calls = [], sounds = [], errors = [], resumes = [], reports = [], alerts = [], camera = { starts: 0, stops: 0 };
  let results = [...lookups], reader, action;
  const values = {}, fields = {}, focused = [];
  // 每一格：框架控件的几样（录入框是原生 Link：输入框事件、下拉、筛选说明）
  const control = df => { const listeners = {}; const $input = $(); $input.trigger = name => { if (name === 'focus') focused.push(df.fieldname); return $input; };
    return { df: { ...df }, $wrapper: $(), $input, listeners, input: { addEventListener(type, fn) { (listeners[type] ||= []).push(fn); } },
      merge_duplicates: rows => rows }; };
  const dialog = {
    fields_dict: fields, $wrapper: $(), onhide: null,
    get_value: name => values[name], async set_value(name, value) { values[name] = value; },
    set_df_property(name, prop, value) { (fields[name] ||= control({ fieldname: name })).df[prop] = value; },
    get_primary_btn: () => $(), set_secondary_action() {}, set_secondary_action_label() {}, hide() { dialog.onhide?.(); },
  };
  const p = {
    dialog(title, list, label, primary) { for (const df of list) fields[df.fieldname] = control(df); action = primary; return dialog; },
    warehouse_query: () => () => ({}), location_query: () => () => ({}), scan_stage: () => '',
    async call(method, args) { calls.push([method, args]); const next = results.shift(); if (next instanceof Error || next?.status) throw next; return next; },
    async pick_product() { return null; }, scan_result_sound: ok => sounds.push(ok), success() {},
    scan_liff_first: () => false, number: value => String(value),
    scan_reader({ on_result }) { camera.starts += 1; reader = { on_result, start: async () => {}, pause() {}, resume: code => resumes.push(code ?? ''), stop() { camera.stops += 1; }, has_torch: () => false, torch() {} }; return reader; },
  };
  const frappe = { pearnly: p, msgprint() {}, show_alert: options => alerts.push(options.message), DataTable: class {} };
  const window = { pearnlyReportError: (error, kind) => reports.push([error, kind]) };
  vm.runInNewContext(source, { frappe, $, window, __: text => text, crypto: { randomUUID: () => 'id-1' },
    console: { error: error => errors.push(error), log() {} } });
  return {
    // 打开后点框内扫码图标开扫码器（11-02 起扫码器平时收起）
    async open(options = {}) { await p.stockCountEntry({ count: 'STC-00011', t: key => key, continuous: true, ...options }); await flush();
      if (options.camera !== false) { fields.code.df.scan(); await flush(); } },
    scan: async code => { await reader.on_result(code); await flush(); },
    // 扫码枪 / 手打的码：框里填上回车
    find: async code => { fields.code.$input.val(code);
      for (const fn of fields.code.listeners.keydown || []) fn({ key: 'Enter', isComposing: false, preventDefault() {}, stopImmediatePropagation() {} });
      await flush(); },
    // 点「继续」（原生主按钮的动作）
    proceed: async qty => { fields.qty.$input.val(String(qty)); await action({ qty, warehouse: values.warehouse, location: '', note: '' }); await flush(); },
    values, fields, calls, sounds, errors, resumes, reports, alerts, camera, p, focused,
    message: () => fields.message.$wrapper.text(),
    shown: () => fields.entry_section.df.hidden === false,
  };
}

const polymer = (extra = {}) => ({
  product: { name: 'PRD-00010', product_name: 'HLY-002/W Oak 2.8m', base_unit: 'เส้น', unit: 'เส้น', serial_kind: 'vehicle', code: 'P-000002' },
  rows: [], elsewhere: [], recorded: [], stock_token: 't', warehouse: 'WH-00001', warehouse_name: '1000', track_serial: false, unit: null, units: [], ...extra });

(async () => {
  // 扫码命中、这个仓账上没有（rows 空）、连续盘点：录入框出来，商品名、单位、实际仓库带对，只查一次
  {
    const f = fixture({ lookups: [polymer()] });
    await f.open();
    await f.scan('TC001167');
    assert.equal(f.calls.length, 1);
    assert.deepEqual({ ...f.calls[0][1] }, { count: 'STC-00011', code: 'TC001167', product: undefined });
    assert.equal(f.shown(), true, '录入框要出来');
    assert.equal(f.values.product_name, 'HLY-002/W Oak 2.8m');
    assert.equal(f.values.unit, 'เส้น');
    // 实际仓库是官方 Link：放仓库编号，不放名称（名称会让实际仓位取选项报「未找到仓库」，11-02 真机）
    assert.equal(f.values.warehouse, 'WH-00001');
    assert.deepEqual(f.sounds, [true]);
    assert.deepEqual(f.errors, []);
  }
  // 相机扫到系统没有的码（岳父 11-01）：服务端回 not_found 不弹提示框；停相机、收起画面，「新建物料」出来，
  // 用的是扫到的码（相机扫的不在编码栏里）；建好接着认出这件，记完下一件再开相机
  {
    const f = fixture({ lookups: [{ not_found: true }, polymer(), { name: 'E-1' }] });
    await f.open();
    await f.scan('8859NEW');
    assert.equal(f.camera.stops, 1, '查不到要停相机');
    assert.equal(f.fields.video.df.hidden, true, '相机画面收起');
    assert.equal(f.fields.new_product.df.hidden, 0, '新建物料要出来');
    assert.equal(f.message(), 'No product matches this code');
    assert.deepEqual(f.alerts, ['No product matches this code']);
    assert.deepEqual(f.resumes, [], '相机已停，不再跳过续扫');
    assert.deepEqual(f.sounds, [false]);
    // 点了原地弹物料页同一个新建弹窗（`link_editors["ERP Product"]`，用户 11-01）：扫到的码预填；编码预览与保存走盘点录入权的接口
    let editor;
    f.p.link_editors = { 'ERP Product': async context => { editor = context; return {}; } };
    await f.fields.new_product.df.click();
    await flush();
    assert.equal(editor.code, '8859NEW', '新建用扫到的码');
    f.p.call = async (method, args) => { f.calls.push([method, args]);
      return method.endsWith('create_product') ? { name: 'PRD-NEW', created: true } : method.endsWith('next_product_code') ? { code: 'P-000099' } : polymer(); };
    assert.deepEqual({ ...await editor.preview() }, { code: 'P-000099' });
    assert.equal(f.calls.at(-1)[0], 'pearnly_erp_portal.inventory.api.api_stock_count_next_product_code');
    assert.deepEqual({ ...f.calls.at(-1)[1] }, { count: 'STC-00011' });
    // 弹窗交来的是物料页那份载荷（编码是预览、码按码型落在条码 / 二维码）：盘点把码交给服务端认码型，系统编码由服务端发
    const saved = await editor.submit({ product_name: 'New one', code: 'P-000099', barcode: '8859NEW', qr_payload: '', unit: 'ชิ้น',
      unit_price: 10, serial_kind: 'vehicle' }, { track_serial: 1 });
    assert.equal(f.calls.at(-1)[0], 'pearnly_erp_portal.inventory.api.api_stock_count_create_product');
    assert.deepEqual({ ...f.calls.at(-1)[1] }, { product_name: 'New one', code: '8859NEW', unit: 'ชิ้น', unit_price: 10,
      serial_kind: 'vehicle', count: 'STC-00011', track_serial: 1 });
    await editor.on_saved({ ...saved, code_value: '8859NEW' });
    await flush();
    assert.equal(f.fields.new_product.df.hidden, 1, '建好收起新建物料');
    assert.equal(f.shown(), true, '建好接着认出这件');
    assert.equal(f.calls.at(-1)[1].code, '8859NEW');
  }
  // 查到下一件时清掉上一件的提示（验收 10-29：查不到 / 已保存那句一直留着）；编码栏带出这件的编码（S2）
  {
    const f = fixture({ lookups: [Object.assign(new Error('not found'), { status: 417 }), polymer()] });
    await f.open();
    await f.scan('NOPE-1');
    assert.equal(f.message(), 'stock.workbench.lookupFailed');
    await f.scan('TC001167');
    assert.equal(f.message(), '', '上一件的提示要清掉');
    assert.equal(f.values.product_code, 'P-000002');
  }
  // 按台管理、这个仓一台都没登记：说清是哪件商品、为什么录不了；读码器跳过这个码（不反复查）
  {
    const f = fixture({ lookups: [polymer({ track_serial: true, units: [] })] });
    await f.open();
    await f.scan('TC001167');
    assert.equal(f.shown(), false);
    assert.match(f.message(), /HLY-002\/W Oak 2\.8m/);
    assert.match(f.message(), /Count products tracked by unit one unit at a time/);
    assert.match(f.message(), /No units of this product are registered in this warehouse/);
    assert.deepEqual(f.resumes, ['TC001167']);
    assert.deepEqual(f.sounds, [false]);
    assert.equal(f.calls.length, 1);
    assert.deepEqual(f.alerts, [f.message()], '相机开着时弹窗那行在画面下面：再用原生浮动提示说一遍');
  }
  // 按台管理、这个仓有登记的台：只提示逐台扫，不说"没登记"
  {
    const f = fixture({ lookups: [polymer({ track_serial: true, units: [{ name: 'SN-1', serial_no: 'A1' }] })] });
    await f.open();
    await f.scan('TC001167');
    assert.doesNotMatch(f.message(), /No units of this product/);
    assert.deepEqual(f.resumes, ['TC001167']);
  }
  // 服务端明确不认（417）：说"找不到"；读码器跳过这个码；手动点查找照样再查
  {
    const f = fixture({ lookups: [{ status: 417, responseJSON: { exc_type: 'ValidationError' } }, polymer()] });
    await f.open();
    await f.scan('NOPE-1');
    assert.equal(f.message(), 'stock.workbench.lookupFailed');
    assert.deepEqual(f.resumes, ['NOPE-1']);
    assert.deepEqual(f.errors, []);
    await f.find('TC001167');
    assert.equal(f.calls.length, 2, '手动查找不受跳过影响');
    assert.equal(f.shown(), true);
  }
  // 服务端出错（500）：说"查找失败"，不冒充"找不到"，错误记下来
  {
    const f = fixture({ lookups: [{ status: 500, responseJSON: { exc_type: 'AttributeError' } }] });
    await f.open();
    await f.scan('TC001167');
    assert.equal(f.message(), 'stock.workbench.lookupError');
    assert.equal(f.errors.length, 1);
    assert.equal(f.reports.length, 0, '服务端的错不走前端报错');
    assert.deepEqual(f.alerts, ['stock.workbench.lookupError']);
    assert.deepEqual(f.resumes, ['TC001167']);
  }
  // 这页自己的脚本错（返回缺了 rows）：同样是"查找失败"，错误记下来
  {
    const broken = polymer(); delete broken.rows;
    const f = fixture({ lookups: [broken] });
    await f.open();
    await f.scan('TC001167');
    assert.equal(f.message(), 'stock.workbench.lookupError');
    assert.ok(f.errors[0] instanceof Error || String(f.errors[0]).includes('TypeError'));
    assert.equal(f.reports.length, 1, '前端自己的错交给 window.pearnlyReportError');
    assert.equal(f.reports[0][1], 'stocktake-lookup');
    assert.deepEqual(f.resumes, ['TC001167']);
  }
  // Cowork：服务端不认 → 找不到；服务端出错 → 查找失败；两种都跳过这个码
  {
    const f = fixture({ file: COWORK, lookups: [{ status: 417 }] });
    await f.open();
    await f.scan('NOPE-1');
    assert.equal(f.message(), 'cowork.stock.workbench.lookupFailed');
    assert.deepEqual(f.resumes, ['NOPE-1']);
  }
  {
    const f = fixture({ file: COWORK, lookups: [{ status: 502 }] });
    await f.open();
    await f.scan('TC001167');
    assert.equal(f.message(), 'cowork.stock.workbench.lookupError');
    assert.equal(f.errors.length, 1);
    assert.deepEqual(f.resumes, ['TC001167']);
  }
  // 读到物料：扫码器关掉、出字段；点「继续」后扫码器自动再开，先跳过刚扫的码（镜头还对着它时不重复记，11-02）
  {
    const f = fixture({ lookups: [polymer(), { entry: 'E-1' }] });
    await f.open();
    await f.scan('TC001167');
    assert.equal(f.camera.stops, 1, '读到物料扫码器就关');
    assert.equal(f.fields.video.df.hidden, true);
    assert.equal(f.shown(), true);
    await f.proceed(2);
    assert.equal(f.calls[1][0], 'pearnly_erp_portal.inventory.api.api_stock_count_add_entry');
    assert.equal(f.camera.starts, 2, '扫码来的那件：继续后自动再开扫码器');
    assert.deepEqual(f.resumes, ['TC001167'], '再开时先跳过刚扫的码');
  }
  // 打字 / 扫码枪回车来的那件：继续后回到框里、不开相机
  {
    const f = fixture({ lookups: [polymer(), { entry: 'E-1' }] });
    await f.open({ camera: false });
    await f.find('TC001167');
    assert.equal(f.camera.starts, 0);
    assert.equal(f.shown(), true);
    await f.proceed(1);
    assert.equal(f.camera.starts, 0, '打字来的那件：继续后不开相机');
    assert.equal(f.focused.at(-1), 'code', '回到框里');
  }
  // 扫码器开着时再点框内图标、或点「关闭」：关掉、回到框里
  {
    const f = fixture({ lookups: [] });
    await f.open();
    assert.equal(f.camera.starts, 1);
    f.fields.code.df.scan();
    await flush();
    assert.equal(f.camera.stops, 1);
    assert.equal(f.focused.at(-1), 'code');
    f.fields.code.df.scan();
    await flush();
    f.fields.close_camera.df.click();
    await flush();
    assert.equal(f.camera.stops, 2);
  }
  console.log('stocktake entry scan: ok');
})().catch(error => { console.error(error); process.exit(1); });
