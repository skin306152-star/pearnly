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
// 假摄像头一上来就读到码，弹窗可能一闪就关：等「商品填上了」或「出了找不到的提示」或弹窗一直开着读不到
const product = line.querySelector("input");
const settled = await wait(() => (product && product.value) || alerts() || null, 25000);
await sleep(1500);
const desc = page.querySelector('[data-fieldname="lines"] [data-fieldname="description"] textarea');
const stuck = $$(".modal.show").some(m => m.querySelector("video"));
const filled = {product: product && product.value, description: desc && desc.value, alerts: alerts(), dialog_open: stuck};
if (!settled) return {ok: false, why: stuck ? "扫码弹窗开着 25 秒没读到码" : "点扫码后既没填商品也没提示", filled, msg: msg()};
if (${v.expect_found} === false) return {ok: !filled.product && /${v.code_re}/.test(filled.alerts), why: "未知码应提示找不到、不填", filled};
if (!filled.product) return {ok: false, why: "读到码但商品没填进明细", filled};
setInput(page.querySelector('[data-fieldname="lines"] [data-fieldname="qty"] input'), "${v.qty}");
await sleep(800);
click($$(".page-actions .primary-action, .page-actions .btn-primary").find(b => b.offsetParent));
const back = await wait(() => frappe.get_route().length === 1 && frappe.get_route()[0] === "${v.route}", 20000);
const after = await stockOf("${v.product}", "${v.warehouse}");
const expected = before + ${v.sign} * ${v.qty};
return {ok: !!back && Math.abs(after - expected) < 1e-9, before, after, expected, filled, route: frappe.get_route(), msg: msg(), alerts: alerts()};
