// 收银台整班：五种码在收银台查码都认对商品 → 开班（备用金 500）→ 卖一张（编码商品 ×2 + 二维码商品 ×1，现金）→ 退一件编码商品
// → 当班汇总笔数 / 应有现金对 → 实点等于应有交班差额 0 → 收银台仓的结存按「卖 2 退 1」少 1（新开户无期初时允许负数）
const P = "pearnly_erp_portal.cashier.api.";
const want = {code: "${products.code}", barcode: "${products.barcode}", qr: "${products.qr}", internal: "${products.internal}", express: "${products.express}"};
const codes = {code: "${codes.code}", barcode: "${codes.barcode}", qr: "${codes.qr}", internal: "${codes.internal}", express: "${codes.express}"};
const terminal = ((await call(P + "api_list_terminals", {}, "GET")) || [])[0];
const terminal_name = terminal && (terminal.name || terminal);
const found = {};
for (const key of Object.keys(codes)) {
  const row = await call(P + "api_counter_lookup", {value: codes[key], terminal: terminal_name}, "GET");
  found[key] = row && row.name;
}
const lookups_ok = Object.keys(want).every(key => found[key] === want[key]);
const before = (await call(P + "api_counter_lookup", {value: codes.code, terminal: terminal_name}, "GET")).qty_on_hand;
const current = await call(P + "api_current_shift", {terminal: terminal_name}, "GET");
if (current && current.shift) await call(P + "api_close_shift", {name: current.shift.name, counted_cash: current.expected_cash});
const shift = (await call(P + "api_open_shift", {terminal: terminal_name, opening_float: 500})).name;
const uuid = () => crypto.randomUUID();
const sale = await call(P + "api_create_sale", {terminal: terminal_name, shift, client_uuid: uuid(),
  lines: [{product: want.code, qty: 2, unit_price: 100}, {product: want.qr, qty: 1, unit_price: 100}],
  payments: [{method: "cash", amount: 300}]});
const detail = await call(P + "api_get_sale", {name: sale.name}, "GET");
const line = (detail.lines || []).find(row => row.product === want.code);
const refund = await call(P + "api_refund_sale", {name: sale.name, client_uuid: uuid(), reason: "ชำรุด",
  lines: [{original_line: line.name, qty: 1}], payments: [{method: "cash", amount: line.line_total / line.qty}]});
const summary = await call(P + "api_shift_summary", {name: shift}, "GET");
const closed = await call(P + "api_close_shift", {name: shift, counted: {cash: summary.expected_cash}});
const after = (await call(P + "api_counter_lookup", {value: codes.code, terminal: terminal_name}, "GET")).qty_on_hand;
const shift_doc = (await call("frappe.client.get_list", {doctype: "ERP Shift", filters: {name: shift}, fields: ["status", "counted_cash", "expected_cash"]}, "GET"))[0];
const checks = {lookups_ok, sale_total: Number(detail.grand_total ?? detail.total), refund: !!(refund && refund.name),
  sales_count: summary.sales_count, refund_count: summary.refund_count, expected_cash: Number(summary.expected_cash),
  shift: shift_doc, stock_delta: Math.round((after - before) * 1000) / 1000};
const ok = lookups_ok && checks.sale_total === 300 && checks.refund && summary.sales_count === 1 && summary.refund_count === 1
  && checks.expected_cash === 700 && shift_doc && shift_doc.status === "closed" && Number(shift_doc.counted_cash) === 700
  && checks.stock_delta === -1;
return {ok, found, checks, sale: sale.name, refund: refund && refund.name, shift};
