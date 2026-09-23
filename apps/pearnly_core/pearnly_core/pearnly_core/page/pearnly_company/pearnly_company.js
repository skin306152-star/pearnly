/* 公司页：**页面文档在 core**（路由要每个产品都能指过来），**渲染归各自产品**
 * —— 与 `pearnly-team` → `frappe.pearnly.team` 同一套写法。
 * 产品还没实现时就落到"未实现"那一屏，而不是白页。 */
frappe.pearnly.register("pearnly-company", "company.", "nav.company", (page, t, wrapper) =>
	// 渲染函数**到用时再取**：产品那份脚本与页面脚本的加载先后由框架决定，
	// 注册时就把函数抓下来会拿到 undefined（`pearnly-team` 也是这么写的）。
	(frappe.pearnly.company || frappe.pearnly.notBuilt)(page, t, wrapper));
