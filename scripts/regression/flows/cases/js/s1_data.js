// S1 批次 / 有效期不再写入：① 商品导入模板没有这两列，带着旧表这两列导入照建、不写标志 ② 旧页面带 track_batch 建商品照建、不写
// ③ 采购行带批号 / 失效日期照建单、两项不落库 ④ 入库行带批号照过账、不落库（库存照加）⑤ 商品详情不再回这两项
const S = "pearnly_erp_portal.products.service.";
const out = {};
const sheet = exec_out || {};
out.template_clean = Array.isArray(sheet.headers) && !sheet.headers.some(h => /批|效期|batch|expir|lot/i.test(h));
const blob = await (await fetch(sheet.url, {credentials: "include"})).blob();
const form = new FormData();
form.append("file", blob, "flows-product-sheet-" + Date.now() + ".xlsx"); form.append("is_private", "1");
const up = await (await fetch("/api/method/upload_file", {method: "POST", body: form, credentials: "include",
  headers: {"X-Frappe-CSRF-Token": frappe.csrf_token, "Accept": "application/json"}})).json();
const imported = await call(S + "api_import_products", {file_url: up.message && up.message.file_url});
const flags = async name => (await call("frappe.client.get_value", {doctype: "ERP Product", filters: {name}, fieldname: ["track_batch", "track_expiry", "origin"]}, "GET")) || {};
const importedName = ((await call("frappe.client.get_list", {doctype: "ERP Product", filters: {product_name: sheet.name}, fields: ["name"]}, "GET")) || [])[0];
out.import = {created: imported && imported.created, product: importedName && importedName.name, flags: importedName && await flags(importedName.name)};
const made = await call(S + "api_create_product", {product_name: "แล็บล็อต " + Date.now(), unit: "ชิ้น", track_batch: 1, track_expiry: 1});
out.create = {name: made && made.name, flags: made && await flags(made.name)};
const detail = await call(S + "api_get_product", {name: made.name}, "GET");
out.detail_keys = Object.keys(detail || {}).filter(k => /batch|expiry/.test(k));
const purchase = await call("pearnly_erp_portal.purchasing.api.api_post_purchase", {request_id: crypto.randomUUID(), payload: JSON.stringify({
  company: "${company}", doc_kind: "purchase_invoice", supplier_name: "ผู้ขายล็อต", doc_no: "LOT-" + Date.now(), doc_date: frappe.datetime.get_today(),
  currency: "THB", fx_rate: 1, has_vat: 1, price_mode: "exclusive", update_stock: 1,
  lines: [{item_type: "goods", product: made.name, description: "ล็อต", qty: 2, unit: "ชิ้น", unit_price: 10, vat_rate: 7,
    warehouse: "${seed.spare_warehouse}", location: "", batch_no: "LOT-9", expiry_date: "2027-12-31"}]})});
const plines = await call("frappe.client.get_list", {doctype: "Purchase Line", parent: "Purchase Document", filters: {parent: purchase.name}, fields: ["batch_no", "expiry_date", "qty"]}, "GET");
out.purchase = {name: purchase.name, lines: plines};
const entry = await call("pearnly_erp_portal.inventory.api.api_create_stock_entry", {direction: "in", warehouse: "${seed.spare_warehouse}",
  posting_date: frappe.datetime.get_today(), submit: 1, lines: JSON.stringify([{product: made.name, qty: 1, unit_cost: 10, warehouse: "${seed.spare_warehouse}", batch_no: "LOT-7"}])});
const slines = await call("frappe.client.get_list", {doctype: "ERP Stock Entry Line", parent: "ERP Stock Entry", filters: {parent: entry.name}, fields: ["batch_no", "qty"]}, "GET");
const stock = await call("pearnly_erp_portal.inventory.api.api_stock", {warehouse: "${seed.spare_warehouse}", include_zero: 1, limit: 500}, "GET");
const row = (stock || []).find(r => r.product === made.name || r.name === made.name);
out.stock_entry = {name: entry.name, lines: slines, qty_on_hand: row && Number(row.qty_on_hand ?? row.qty)};
const ok = out.template_clean && out.import.created === 1 && out.import.flags && !Number(out.import.flags.track_batch) && !Number(out.import.flags.track_expiry)
  && out.import.flags.origin === "import"
  && !Number(out.create.flags.track_batch) && !Number(out.create.flags.track_expiry) && out.detail_keys.length === 0
  && plines.length === 1 && !plines[0].batch_no && !plines[0].expiry_date
  && slines.length === 1 && !slines[0].batch_no && out.stock_entry.qty_on_hand === 3;
return {ok, ...out};
