/* 集成页：**页面文档在 core**（路由每个产品都能指过来），**渲染归产品**
 * —— 与 `pearnly-warehouses` → `frappe.pearnly.warehouses` 同一套写法。
 * 产品还没实现时就落到"未实现"那一屏，而不是白页。 */
frappe.pearnly.register("pearnly-integrations", "shell.", "nav.integrations", (page, t, wrapper) =>
	(frappe.pearnly.integrations || frappe.pearnly.notBuilt)(page, t, wrapper));
