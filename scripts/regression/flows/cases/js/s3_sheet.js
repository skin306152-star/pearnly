// Excel 上传（exec count-sheet）：带品名的未知码（第 2 行）当场建并记 4；没品名的未知码（第 3 行）列成问题行「码找不到」
const {count} = s3();
const sheet = exec_out || {};
const blob = await (await fetch(sheet.url, {credentials: "include"})).blob();
const form = new FormData();
form.append("file", blob, "flows-count-sheet-" + Date.now() + ".xlsx"); form.append("is_private", "1");
const up = await (await fetch("/api/method/upload_file", {method: "POST", body: form, credentials: "include",
  headers: {"X-Frappe-CSRF-Token": frappe.csrf_token, "Accept": "application/json"}})).json();
const result = await quietCall(C + "api_stock_count_import", {name: count, file_url: up.message && up.message.file_url});
const items = await call(C + "api_stock_count_items", {count, limit: 200}, "GET");
const list = Array.isArray(items) ? items : (items && (items.rows || items.data)) || [];
const made = list.find(r => r.barcode === sheet.named || (r.product_name || "") === sheet.name);
const problems = (result && result.problems) || [];
s3save({sheet});
return {ok: !!made && Number(made.counted_qty) === 4 && problems.some(p => Number(p.row) === 3 && p.reason === "code_not_found"),
  made: made && {product: made.product, counted_qty: made.counted_qty, row_origin: made.row_origin}, problems: problems.slice(0, 3), result: result && result.error};
