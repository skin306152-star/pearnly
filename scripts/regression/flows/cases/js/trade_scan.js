// 新建${v.label}（整页）：填单头 → 明细第一行点「扫码」认商品 → 数量 / 单价 / 仓库 → 提交 → 单据已提交、该仓账面数按方向变化
const stockOf = async (product, warehouse) => {
  const rows = await call("pearnly_erp_portal.inventory.api.api_stock", {warehouse, include_zero: 1, limit: 500}, "GET");
  const row = (rows || []).find(r => r.product === product || r.name === product);
  return row ? Number(row.qty_on_hand ?? row.qty ?? 0) : 0;
};
const before = await stockOf("${v.product}", "${v.warehouse}");
const page = await wait(() => $$(".page-container").find(el => el.getAttribute("data-page-route") === "${v.route}"));
const setInput = (el, value) => { if (!el) return false; el.focus(); el.value = value; el.dispatchEvent(new Event("input", {bubbles: true}));
  el.dispatchEvent(new Event("change", {bubbles: true})); el.blur(); return true; };
const field = (name, root) => (root || page).querySelector('[data-fieldname="' + name + '"] input, [data-fieldname="' + name + '"] textarea');
await wait(() => field("${v.party_field}"), 15000);
// 新建先选录入方式：采购 / 销售默认「上传识别」，这里走「手动录入」（明细才展开）
const manual = await wait(() => $$("label, .checkbox, .pearnly-entry-choice, div", page).find(el => el.children.length < 6 && /^手动录入/.test(el.textContent.trim())), 8000);
if (manual) { const box = manual.querySelector("input[type=checkbox], input[type=radio]") || manual; click(box); await sleep(1500); }
for (const [name, value] of ${v.head}) { setInput(field(name), value === "AUTO" ? "INV-" + Date.now() : value); await sleep(500); }
// 单头对方（供应商 / 客户）：先用接口建一个，再在单头组合搜索框里输名字、点下拉第一项（手工选，不改隐藏字段）
const partyName = "${v.party_label} " + Date.now().toString().slice(-6);
await call("${v.party_api}", {${v.party_arg}: partyName});
const headChoice = [...page.querySelectorAll('[data-fieldname="entry_choice"]')].find(el => !el.closest('[data-fieldname="lines"]'));
const headInput = headChoice && headChoice.querySelector("input, textarea");
if (!headInput) return {ok: false, why: "单头找不到对方选择框"};
headInput.focus(); headInput.value = partyName;
headInput.dispatchEvent(new Event("input", {bubbles: true})); headInput.dispatchEvent(new KeyboardEvent("keyup", {bubbles: true, key: "a"}));
const option = await wait(() => $$(".awesomplete li, ul[role=listbox] li", headChoice).find(li => li.textContent.includes(partyName)) || $$(".awesomplete li").find(li => li.textContent.includes(partyName)), 10000);
if (!option) return {ok: false, why: "对方下拉没出这个名字", typed: partyName};
option.dispatchEvent(new MouseEvent("mousedown", {bubbles: true})); option.click();
await sleep(1500);
const partySet = (page.querySelector('[data-fieldname="${v.party_ref}"] input') || {}).value;
// 采购 / 销售的明细：商品用组合搜索框（`bind_entry_choice`，字段 entry_choice），扫码按钮就在它右侧；仓库 / 库位在单头（多仓 P1）
setInput(field("warehouse"), "${v.warehouse}"); await sleep(1200);
const lines = await wait(() => page.querySelector('[data-fieldname="lines"]'));
const choice = await wait(() => lines.querySelector('[data-fieldname="entry_choice"]'), 10000);
const scan = choice && choice.querySelector(".pearnly-link-scan");
if (!scan) return {ok: false, why: "明细商品框没有扫码按钮", fields: [...lines.querySelectorAll("[data-fieldname]")].map(e => e.getAttribute("data-fieldname")).slice(0, 30)};
click(scan);
const desc = () => (lines.querySelector('[data-fieldname="description"] textarea, [data-fieldname="description"] input') || {}).value
  || (choice.querySelector("input, textarea") || {}).value;
const settled = await wait(() => desc() || alerts() || null, 25000);
await sleep(1500);
if (!desc()) return {ok: false, why: "扫码后商品没填进明细", alerts: alerts(), msg: msg()};
const picked = desc();
setInput(field("qty", lines), "${v.qty}"); await sleep(300);
setInput(field("unit_price", lines), "${v.price}"); await sleep(800);
const primary = $$(".page-actions .primary-action, .page-actions .btn-primary").find(b => b.offsetParent);
const label = primary && primary.textContent.trim();
click(primary);
await sleep(1500);
const yes = $$(".modal.show .btn-primary").find(b => /是|确定|提交|Yes|Submit/.test(b.textContent));
if (yes) { click(yes); }
const moved = await wait(() => frappe.get_route()[1] && frappe.get_route()[1] !== "new", 25000);
await sleep(2000);
const after = await stockOf("${v.product}", "${v.warehouse}");
const expected = before + ${v.sign} * ${v.qty};
return {ok: !!moved && Math.abs(after - expected) < 1e-9, picked, party: partySet, button: label, before, after, expected, route: frappe.get_route(), msg: msg(), alerts: alerts()};
