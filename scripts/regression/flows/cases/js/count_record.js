// 桌面盘点录入：给 ${v.wh_label} 建一张盘点 → 详情页点「开始盘点」（连续盘点弹窗）→ 用${v.how}输入「${v.code}」
// → 应出录入区（物料名称、实际仓库、本次实盘数量）且认对商品 → 填数量「保存并下一项」→ 盘点里多一条记录
// （polymer 10-28：手机盘点认出了商品、录入区没出来、误报找不到；桌面同一个录入组件 `stocktake-entry.js`）
const made = await call("pearnly_erp_portal.inventory.api.api_create_stock_count", {title: "flows ${v.how} " + Date.now(), warehouse: "${v.warehouse}"});
frappe.set_route("pearnly-stock-count", made.name);
await wait(() => $$(".page-container").find(el => el.getAttribute("data-page-route") === "pearnly-stock-count"));
await sleep(2500);
const start = await wait(() => byText(".page-head button", "开始盘点"), 10000);
if (!start) return {ok: false, why: "没有「开始盘点」按钮", head: $$(".page-head button").map(b => b.textContent.trim())};
click(start);
const dialog = await wait(() => $$(".modal.show").find(m => m.querySelector('[data-fieldname="code"]')), 10000);
if (!dialog) return {ok: false, why: "开始盘点后录入弹窗没出来", msg: msg()};
const qtyField = () => dialog.querySelector('.frappe-control[data-fieldname="qty"]');
if ("${v.how}" === "camera") {
  click(dialog.querySelector('[data-fieldname="camera"] button'));
} else {
  const input = dialog.querySelector('[data-fieldname="code"] input, [data-fieldname="code"] textarea');
  input.focus(); input.value = "${v.code}"; input.dispatchEvent(new Event("input", {bubbles: true}));
  input.dispatchEvent(new Event("change", {bubbles: true}));
  await sleep(300);
  click(dialog.querySelector('[data-fieldname="find"] button'));
}
const shown = await wait(() => qtyField() && visible(qtyField()), 25000);
const text = name => { const el = dialog.querySelector('[data-fieldname="' + name + '"]'); if (!el) return "";
  const input = el.querySelector("input, textarea"); return (input && input.value) || (el.querySelector(".control-value") || el).innerText.trim(); };
const state = {entry_shown: !!shown, product_name: text("product_name"), warehouse: text("warehouse"),
  product_code: text("product_code"), message: text("message").slice(0, 200), alerts: alerts()};
if (!shown) return {ok: false, why: "认码后录入区没出来", ...state};
if (!state.product_name.includes("${v.name}")) return {ok: false, why: "认出的商品不对", ...state};
const qty = qtyField().querySelector("input");
if (!qty) return {ok: false, why: "数量输入框找不到", ...state, html: qtyField().outerHTML.slice(0, 300)};
qty.focus(); qty.value = "${v.qty}"; qty.dispatchEvent(new Event("input", {bubbles: true})); qty.dispatchEvent(new Event("change", {bubbles: true}));
await sleep(600);
click(dialog.querySelector(".modal-footer .btn-primary"));
await sleep(3500);
const entries = await call("pearnly_erp_portal.inventory.api.api_stock_count_entries", {count: made.name}, "GET");
const rows = Array.isArray(entries) ? entries : (entries && (entries.rows || entries.data)) || [];
const saved = rows.find(r => Number(r.qty) === ${v.qty});
// S2：录入框「编码」栏显示扫到那件的编码（内部编码，没有就系统编码；与 LINE 手机页同一个）
const info = saved ? (await call("frappe.client.get_value", {doctype: "ERP Product", filters: {name: saved.product}, fieldname: ["internal_code", "code"]}, "GET")) || {} : {};
const code_shown = (info.internal_code || info.code || "") === state.product_code && !!state.product_code;
return {ok: !!saved && code_shown, code_shown, expect_code: info.internal_code || info.code, ...state, saved: saved ? {product: saved.product, qty: saved.qty, warehouse: saved.warehouse} : null,
  entries: rows.length, msg: msg(), after: alerts()};
