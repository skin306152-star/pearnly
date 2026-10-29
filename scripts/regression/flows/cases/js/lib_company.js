// 公共：打开这家公司的资料页、读资料页上的格子与按钮、打开「新建物料」弹窗（S0 / S1 用例共用）
const M = "pearnly_erp_portal.masters.api.";
const profileOf = () => call(M + "api_get_profile", {name: "${company}"}, "GET");
const openProfile = async () => {
  frappe.set_route("pearnly-company", "${company}");
  return wait(() => $$("[data-fieldname='company_type']")[0], 30000);
};
const field = name => $$(".page-container[data-page-route='pearnly-company'] [data-fieldname='" + name + "']")[0] || null;
const checkbox = name => { const el = field(name); return el ? el.querySelector("input[type=checkbox]") : null; };
const pageButton = text => $$(".page-container[data-page-route='pearnly-company'] .page-head button, .page-container[data-page-route='pearnly-company'] .page-actions button")
  .find(b => b.textContent.trim() === text) || null;
// 下拉框在外壳里是可输入的组合框：读显示的文字；选值照人操作（输字 → 点下拉项）
const comboText = name => { const el = field(name); const input = el && el.querySelector("input"); return input ? input.value.trim() : null; };
const comboPick = async (name, label) => {
  const input = field(name).querySelector("input");
  input.focus(); input.value = ""; input.dispatchEvent(new Event("input", {bubbles: true}));
  await sleep(300);
  input.value = label; input.dispatchEvent(new Event("input", {bubbles: true}));
  const item = await wait(() => [...field(name).querySelectorAll("li")].find(li => li.textContent.trim() === label), 5000);
  if (item) { item.dispatchEvent(new MouseEvent("mousedown", {bubbles: true})); item.click(); }
  await sleep(300); input.blur(); input.dispatchEvent(new Event("change", {bubbles: true}));
  await sleep(500);
  return !!item;
};
const fieldButton = name => { const el = field(name); return el ? el.querySelector("button") : null; };
const saveCompany = async () => {
  click($$(".page-container[data-page-route='pearnly-company'] .page-head .primary-action")[0]);
  return wait(() => alerts().length ? alerts() : null, 15000);
};
// 新建物料弹窗里有哪些格子（看完关掉）
const newProductFields = async () => {
  frappe.set_route("pearnly-products");
  await wait(() => $$(".page-container[data-page-route='pearnly-products'] .page-head .primary-action")[0], 30000);
  await sleep(800);
  click($$(".page-container[data-page-route='pearnly-products'] .page-head .primary-action")[0]);
  const modal = await wait(() => $$(".modal.show").find(m => m.querySelector("[data-fieldname='product_name']")), 20000);
  const names = modal ? [...modal.querySelectorAll("[data-fieldname]")].filter(visible).map(el => el.getAttribute("data-fieldname")) : null;
  if (modal) { modal.querySelector(".btn-modal-close") && modal.querySelector(".btn-modal-close").click(); await sleep(600); }
  return names;
};
const errorOf = async (method, args) => { try {
  await new Promise((resolve, reject) => frappe.call({method, args, type: "POST", error: reject, callback: resolve}).fail(reject)); return null;
} catch (e) { $$(".modal.show .btn-modal-close").forEach(b => b.click());
  const body = e && (e.responseJSON || e); return typeof body === "string" ? body : JSON.stringify(body || "error"); } };
// 普通入库一件按台商品、不给台（普通公司应收，按台公司应拒）
const stockInVehicle = () => errorOf("pearnly_erp_portal.inventory.api.api_create_stock_entry", {direction: "in", warehouse: "${seed.spare_warehouse}",
  posting_date: frappe.datetime.get_today(), submit: 1, lines: JSON.stringify([{product: "${products.vehicle}", qty: 1, unit_cost: 100, warehouse: "${seed.spare_warehouse}"}])});
