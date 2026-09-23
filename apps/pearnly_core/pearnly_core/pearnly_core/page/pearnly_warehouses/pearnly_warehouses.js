/* 仓库页：**页面文档在 core**（路由每个产品都能指过来），**渲染归 ERP**
 * —— 与 `pearnly-company` → `frappe.pearnly.company` 同一套写法。
 * 产品还没实现时就落到"未实现"那一屏，而不是白页。 */
frappe.pearnly.register("pearnly-warehouses", "company.", "nav.warehouses", (page, t, wrapper) =>
	(frappe.pearnly.warehouses || frappe.pearnly.notBuilt)(page, t, wrapper));
