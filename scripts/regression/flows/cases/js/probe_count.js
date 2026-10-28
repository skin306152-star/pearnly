// 探页面：新开户空仓建一张盘点，打开详情，列出页头按钮与页面里的按钮
const made = await call("pearnly_erp_portal.inventory.api.api_create_stock_count", {title: "probe " + Date.now(), warehouse: "${warehouses.คลังสาขา 1}"});
frappe.set_route("pearnly-stock-count", made.name);
await sleep(4000);
const page = $$(".page-container").find(el => el.getAttribute("data-page-route") === "pearnly-stock-count");
return {ok: true, made, head: $$(".page-head button, .page-head a.dropdown-item").map(b => b.textContent.trim()).filter(Boolean),
  buttons: page ? $$("button", page).map(b => b.textContent.trim()).filter(Boolean).slice(0, 40) : [], route: frappe.get_route()};
