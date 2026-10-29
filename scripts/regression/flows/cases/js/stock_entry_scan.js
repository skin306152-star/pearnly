// 新建${v.label}（整页）：选仓 → 明细第一行点「扫码」（假摄像头读码）→ 认出该认的商品 → 填数量 → 提交过账 → 该仓账面数按方向变化
const stockOf = async (product, warehouse) => {
  const rows = await call("pearnly_erp_portal.inventory.api.api_stock", {warehouse, include_zero: 1, limit: 500}, "GET");
  const row = (rows || []).find(r => r.product === product || r.name === product);
  return row ? Number(row.qty_on_hand ?? row.qty ?? 0) : 0;
};
const before = await stockOf("${v.product}", "${v.warehouse}");
const page = await wait(() => $$(".page-container").find(el => el.getAttribute("data-page-route") === "${v.route}"));
const field = name => page.querySelector('[data-fieldname="' + name + '"] input, [data-fieldname="' + name + '"] textarea');
const setInput = (el, value) => { el.focus(); el.value = value; el.dispatchEvent(new Event("input", {bubbles: true}));
  el.dispatchEvent(new Event("change", {bubbles: true})); el.blur(); };
const wh = await wait(() => field("warehouse"));
setInput(wh, "${v.warehouse}");
await sleep(1500);
const line = await wait(() => page.querySelector('[data-fieldname="lines"] [data-fieldname="product"]'));
const scan = line.querySelector(".pearnly-link-scan");
if (!scan) return {ok: false, why: "明细行没有扫码按钮"};
click(scan);
// S6：明细「内部编码」随商品带出；已提交单明细显示「品名 · 内部编码」；收货单 / 领料单编码列印 内部编码 → Express 编码 → 系统编码
const info = (await call("frappe.client.get_value", {doctype: "ERP Product", filters: {name: "${v.product}"},
  fieldname: ["internal_code", "express_code", "code", "product_name"]}, "GET")) || {};
const printCode = info.internal_code || info.express_code || info.code;
// 假摄像头一上来就读到码，弹窗可能一闪就关：等「商品填上了」或「出了找不到的提示」或弹窗一直开着读不到
const product = line.querySelector("input");
const settled = await wait(() => (product && product.value) || alerts() || null, 25000);
await sleep(1500);
const desc = page.querySelector('[data-fieldname="lines"] [data-fieldname="description"] textarea');
const stuck = $$(".modal.show").some(m => m.querySelector("video"));
const internal = page.querySelector('[data-fieldname="lines"] [data-fieldname="internal_code"] input, [data-fieldname="lines"] [data-fieldname="internal_code"] .control-value');
const filled = {product: product && product.value, description: desc && desc.value, alerts: alerts(), dialog_open: stuck,
  internal_code: internal ? (internal.value ?? internal.textContent).trim() : null};
if (!settled) return {ok: false, why: stuck ? "扫码弹窗开着 25 秒没读到码" : "点扫码后既没填商品也没提示", filled, msg: msg()};
if (${v.expect_found} === false) return {ok: !filled.product && /${v.code_re}/.test(filled.alerts), why: "未知码应提示找不到、不填", filled};
if (!filled.product) return {ok: false, why: "读到码但商品没填进明细", filled};
setInput(page.querySelector('[data-fieldname="lines"] [data-fieldname="qty"] input'), "${v.qty}");
await sleep(800);
click($$(".page-actions .primary-action, .page-actions .btn-primary").find(b => b.offsetParent));
const back = await wait(() => frappe.get_route().length === 1 && frappe.get_route()[0] === "${v.route}", 20000);
const after = await stockOf("${v.product}", "${v.warehouse}");
const expected = before + ${v.sign} * ${v.qty};
const doc = ((await call("frappe.client.get_list", {doctype: "ERP Stock Entry", filters: {direction: "${v.route}" === "pearnly-stock-in" ? "in" : "out", status: "submitted"},
  fields: ["name"], order_by: "creation desc", limit_page_length: 1}, "GET")) || [])[0];
const format = "${v.route}" === "pearnly-stock-in" ? "Pearnly Goods Receipt Note" : "Pearnly Goods Issue Slip";
const html = doc ? await (await fetch("/printview?doctype=ERP%20Stock%20Entry&name=" + encodeURIComponent(doc.name) + "&format=" + encodeURIComponent(format) + "&no_letterhead=1", {credentials: "include"})).text() : "";
// 已提交详情：从列表点最新那一行，详情在弹窗里（不按单据名直接开路由）
const listPage = $$(".page-container").find(c => c.getAttribute("data-page-route") === "${v.route}");
const firstRow = await wait(() => listPage && $$(".list-row .list-subject a, .list-row-col a, .list-row", listPage)[0], 15000);
click(firstRow);
const detail = await wait(() => { const el = $$(".modal.show").find(m => m.innerText.includes(info.product_name));
  return el ? el.innerText : null; }, 20000);
$$(".modal.show .btn-modal-close").forEach(b => b.click());
const s6 = {internal_field: filled.internal_code, expect_internal: info.internal_code || "", print_code: printCode, printed: html.includes(printCode),
  detail_shows: info.internal_code ? !!detail && detail.includes(info.product_name + " · " + info.internal_code) : true,
  old_express_code_printed: !!(info.internal_code && info.express_code && info.express_code !== info.internal_code && html.includes(info.express_code))};
const s6_ok = (filled.internal_code || "") === s6.expect_internal && s6.printed && s6.detail_shows && !s6.old_express_code_printed;
return {ok: !!back && Math.abs(after - expected) < 1e-9 && s6_ok, before, after, expected, filled, s6, entry: doc && doc.name, route: frappe.get_route(), msg: msg(), alerts: alerts()};
