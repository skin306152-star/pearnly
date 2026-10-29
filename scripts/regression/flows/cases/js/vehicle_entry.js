// 按台管理的车${v.label}（整页）：明细选这台车型、数量 1 → 提交 → 弹出「${v.dialog}」→ 点弹窗的「扫码」读车身二维码（= 车架号全号）
// → 号码填进表格 → 保存 → 过账 → 这台车的状态 / 所在仓按方向变化（车辆按台口径：扫二维码 = 车架号）
const lookupUnit = async () => (await call("pearnly_erp_portal.inventory.serials.api_lookup", {code: "${v.chassis}", product: "${products.vehicle}"}, "GET")) || [];
const before = await lookupUnit();
const page = await wait(() => $$(".page-container").find(el => el.getAttribute("data-page-route") === "${v.route}"));
const setInput = (el, value) => { el.focus(); el.value = value; el.dispatchEvent(new Event("input", {bubbles: true}));
  el.dispatchEvent(new Event("change", {bubbles: true})); el.blur(); };
setInput(await wait(() => page.querySelector('[data-fieldname="warehouse"] input')), "${v.warehouse}");
await sleep(1200);
const productInput = await wait(() => page.querySelector('[data-fieldname="lines"] [data-fieldname="product"] input'));
setInput(productInput, "${products.vehicle}");
const filled = await wait(() => (page.querySelector('[data-fieldname="lines"] [data-fieldname="description"] textarea') || {}).value, 10000);
if (!filled) return {ok: false, why: "选车型后说明没带出", product: productInput.value};
const qty = page.querySelector('[data-fieldname="lines"] [data-fieldname="qty"] input');
setInput(qty, "1");
if ("${v.cost}") setInput(page.querySelector('[data-fieldname="lines"] [data-fieldname="unit_cost"] input'), "${v.cost}");
await sleep(800);
click($$(".page-actions .primary-action, .page-actions .btn-primary").find(b => b.offsetParent));
const dialog = await wait(() => $$(".modal.show").find(m => m.querySelector('[data-fieldname="units"]')), 15000);
if (!dialog) return {ok: false, why: "提交后没弹出按台登记 / 选台弹窗", msg: msg(), alerts: alerts()};
const title = (dialog.querySelector(".modal-title") || {}).textContent || "";
const scanBtn = [...dialog.querySelectorAll(".modal-footer button")].find(b => /扫码|Scan|สแกน/.test(b.textContent));
if (!scanBtn) return {ok: false, why: "按台弹窗没有扫码按钮", title, buttons: [...dialog.querySelectorAll(".modal-footer button")].map(b => b.textContent.trim())};
click(scanBtn);
const cells = () => [...dialog.querySelectorAll('.grid-body .grid-row .static-area, .grid-body .grid-row input')].map(e => (e.value || e.textContent || "").trim()).filter(Boolean);
const got = await wait(() => cells().some(text => /${v.match}/.test(text)), 25000);
if (!got) return {ok: false, why: "扫车身二维码后号码没填进表格", title, grid: dialog.querySelector('[data-fieldname="units"]').innerText.slice(0, 300), alerts: alerts()};
click(dialog.querySelector(".modal-footer .btn-primary"));
const back = await wait(() => frappe.get_route().length === 1 && frappe.get_route()[0] === "${v.route}", 20000);
await sleep(1000);
const after = await lookupUnit();
const unit = (after || [])[0] || {};
return {ok: !!back && unit.status === "${v.status}" && (!"${v.expect_wh}" || unit.warehouse === "${v.expect_wh}"),
  title, before: before.map(u => u.status), unit: {status: unit.status, warehouse: unit.warehouse, serial_no: unit.serial_no},
  route: frappe.get_route(), msg: msg(), alerts: alerts()};
