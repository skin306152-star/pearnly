// 桌面盘点录入框（库存岗位账号）：输一个系统没有的码 → 出现「新建物料」→ 填品名、单位提交 → 录入框认出新商品、编码栏是系统编码 → 记 3
const code = s3().codes.desk;
const count = await newCount("desk");
s3save({count});
frappe.set_route("pearnly-stock-count", count);
await wait(() => $$(".page-container").find(el => el.getAttribute("data-page-route") === "pearnly-stock-count"));
await sleep(2500);
click(await wait(() => byText(".page-head button", "开始盘点"), 10000));
const dialog = await wait(() => $$(".modal.show").find(m => m.querySelector('[data-fieldname="code"]')), 10000);
if (!dialog) return {ok: false, why: "录入弹窗没出来"};
const input = dialog.querySelector('[data-fieldname="code"] input, [data-fieldname="code"] textarea');
input.focus(); input.value = code; input.dispatchEvent(new Event("input", {bubbles: true})); input.dispatchEvent(new Event("change", {bubbles: true}));
await sleep(300);
click(dialog.querySelector('[data-fieldname="find"] button'));
const offer = await wait(() => { const b = dialog.querySelector('[data-fieldname="new_product"] button'); return b && visible(b) ? b : null; }, 15000);
if (!offer) return {ok: false, why: "认不到的码没出「新建物料」", alerts: alerts(), msg: msg()};
// 认不到时服务端那句「未找到…」是原生提示框：照人先关掉它再点「新建物料」
if (frappe.msg_dialog && frappe.msg_dialog.display) { frappe.msg_dialog.hide(); await sleep(600); }
click(offer);
const form = await wait(() => $$(".modal.show").find(m => m !== dialog && m.querySelector('[data-fieldname="product_name"]') && m.querySelector('[data-fieldname="default_cost"]')), 10000);
if (!form) return {ok: false, why: "新建物料表单没出来"};
const set = (name, value) => { const el = form.querySelector('[data-fieldname="' + name + '"] input, [data-fieldname="' + name + '"] textarea');
  el.focus(); el.value = value; el.dispatchEvent(new Event("input", {bubbles: true})); el.dispatchEvent(new Event("change", {bubbles: true})); el.blur(); };
const name = "แล็บนับเจอ " + code;
set("product_name", name); set("unit", "ชิ้น");
await sleep(500);
click(form.querySelector(".modal-footer .btn-primary"));
const shownCode = await wait(() => { const el = dialog.querySelector('[data-fieldname="product_code"] input, [data-fieldname="product_code"] .control-value');
  const v = el && (el.value || el.textContent || "").trim(); return v || null; }, 20000);
const productName = (dialog.querySelector('[data-fieldname="product_name"] input, [data-fieldname="product_name"] .control-value') || {});
// 建好后录入区会按新商品重画一遍：等它稳定再填数量，填完确认值还在
await sleep(1500);
const qtyField = dialog.querySelector('.frappe-control[data-fieldname="qty"] input');
if (!qtyField) return {ok: false, why: "建好后录入区没出来", shownCode};
qtyField.focus(); qtyField.value = "3"; qtyField.dispatchEvent(new Event("input", {bubbles: true})); qtyField.dispatchEvent(new Event("change", {bubbles: true}));
await sleep(800);
const qtyKept = (dialog.querySelector('.frappe-control[data-fieldname="qty"] input') || {}).value;
click(dialog.querySelector(".modal-footer .btn-primary"));
await sleep(3500);
const entries = await call(C + "api_stock_count_entries", {count}, "GET");
const rows = Array.isArray(entries) ? entries : (entries && (entries.rows || entries.data)) || [];
s3save({desk_name: name});
return {ok: !!shownCode && /^P-/.test(shownCode) && rows.some(r => Number(r.qty) === 3), count, shownCode, qty_kept: qtyKept, msg: msg(),
  product_name: productName.value || productName.textContent, entries: rows.length};
