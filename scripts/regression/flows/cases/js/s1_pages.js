// S1 页面：新建物料（弹窗与整页）、采购新建（手动录入）、入库 / 出库新建的明细里都没有批次 / 有效期 / 批号 / 失效日期
const banned = ["track_batch", "track_expiry", "batch_no", "expiry_date"];
const seen = () => banned.filter(name => $$("[data-fieldname='" + name + "']").length);
const pageOf = route => wait(() => $$(".page-container").find(el => el.getAttribute("data-page-route") === route), 30000);
const out = {};
out.product_dialog = await newProductFields();
frappe.set_route("pearnly-products", "new"); await pageOf("pearnly-products"); await sleep(3000);
out.product_page = {fields: $$("[data-fieldname='product_name']").length, banned: seen()};
frappe.set_route("pearnly-purchase", "new"); const purchasePage = await pageOf("pearnly-purchase"); await sleep(3000);
const manual = $$("label, .checkbox, .pearnly-entry-choice, div", purchasePage).find(el => el.children.length < 6 && /^手动录入/.test(el.textContent.trim()));
if (manual) { click(manual.querySelector("input[type=checkbox], input[type=radio]") || manual); await sleep(2500); }
out.purchase = {manual: !!manual, lines: $$("[data-fieldname='lines']").length, banned: seen()};
for (const route of ["pearnly-stock-in", "pearnly-stock-out"]) {
  frappe.set_route(route, "new"); await pageOf(route); await sleep(3000);
  out[route] = {lines: $$("[data-fieldname='lines'], [data-fieldname='qty']").length, banned: seen()};
}
const ok = Array.isArray(out.product_dialog) && !banned.some(f => out.product_dialog.includes(f)) && out.product_page.fields > 0
  && !out.product_page.banned.length && out.purchase.manual && !out.purchase.banned.length
  && out["pearnly-stock-in"].lines > 0 && !out["pearnly-stock-in"].banned.length && !out["pearnly-stock-out"].banned.length;
return {ok, ...out};
