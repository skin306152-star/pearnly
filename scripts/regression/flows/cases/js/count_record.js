// 桌面盘点录入：给 ${v.wh_label} 建一张盘点 → 开始盘点 → 录入盘点 → 用${v.how}输入「${v.code}」→ 应出录入区（商品名、实际仓库、数量）
// → 填数量保存 → 盘点里多一条记录（polymer 10-28：手机盘点认出了商品、录入区没出来，页面误报找不到）
const made = await call("pearnly_erp_portal.inventory.api.api_create_stock_count", {title: "flows ${v.how} " + Date.now(), warehouse: "${v.warehouse}"});
frappe.set_route("pearnly-stock-count", made.name);
const page = await wait(() => $$(".page-container").find(el => el.getAttribute("data-page-route") === "pearnly-stock-count"));
await sleep(2500);
const start = byText(".page-head button", "开始盘点");
if (start) { click(start); await sleep(1000); const yes = byText(".modal.show button", "是") || byText(".modal.show .btn-primary", ""); if (yes) click(yes); await sleep(2500); }
const record = await wait(() => byText("button", "录入盘点"), 8000);
if (!record) return {ok: false, why: "没有「录入盘点」按钮", head: $$(".page-head button").map(b => b.textContent.trim())};
click(record);
const dialog = await wait(() => $$(".modal.show").find(m => m.querySelector('[data-fieldname="code"]')), 10000);
if (!dialog) return {ok: false, why: "录入弹窗没出来", msg: msg()};
const section = () => dialog.querySelector('[data-fieldname="qty"]');
if ("${v.how}" === "camera") {
  click(dialog.querySelector('[data-fieldname="camera"] button'));
} else {
  const input = dialog.querySelector('[data-fieldname="code"] input');
  input.focus(); input.value = "${v.code}"; input.dispatchEvent(new Event("input", {bubbles: true}));
  input.dispatchEvent(new Event("change", {bubbles: true}));
  input.dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", keyCode: 13, bubbles: true}));
  await sleep(600);
  if (!(section() && visible(section()))) click(dialog.querySelector('[data-fieldname="find"] button'));
}
const shown = await wait(() => section() && visible(section()), 25000);
const message = (dialog.querySelector('[data-fieldname="message"]') || {}).textContent || "";
const product_name = (dialog.querySelector('[data-fieldname="product_name"] input, [data-fieldname="product_name"] .control-value') || {}).value
  || (dialog.querySelector('[data-fieldname="product_name"] .control-value') || {}).textContent || "";
const state = {entry_shown: !!shown, message: message.trim().slice(0, 200), product_name, alerts: alerts()};
if (!shown) return {ok: false, why: "认码后录入区没出来", ...state};
if (!String(product_name).includes("${v.name}")) return {ok: false, why: "认出的商品不对", ...state};
const qty = dialog.querySelector('[data-fieldname="qty"] input');
qty.focus(); qty.value = "${v.qty}"; qty.dispatchEvent(new Event("input", {bubbles: true})); qty.dispatchEvent(new Event("change", {bubbles: true}));
await sleep(600);
click(dialog.querySelector(".modal-footer .btn-primary"));
await sleep(3500);
const entries = await call("pearnly_erp_portal.inventory.api.api_stock_count_entries", {name: made.name}, "GET");
const rows = Array.isArray(entries) ? entries : (entries && (entries.rows || entries.data)) || [];
const saved = rows.find(r => Number(r.qty) === ${v.qty});
return {ok: !!saved, ...state, saved: saved ? {product: saved.product, qty: saved.qty, warehouse: saved.warehouse} : null, entries: rows.length, msg: msg()};
