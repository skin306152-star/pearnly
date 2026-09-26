/* 库存盘点页：页面文档在 core，渲染归产品（Cowork 的 `frappe.pearnly.coworkStocktake`）——
 * 与 `pearnly-team` 同一套写法。渲染函数到用时再取：产品脚本与页面脚本的加载先后由框架决定。 */
frappe.pearnly.register("pearnly-stocktake", "shell.", "nav.stocktake", (page, t, wrapper) =>
	(frappe.pearnly.coworkStocktake || frappe.pearnly.notBuilt)(page, t, wrapper));
