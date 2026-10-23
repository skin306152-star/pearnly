/* 任务管理（团队看板）：渲染在 `public/desk/work.js`（到用时再取，加载先后由框架决定）。 */
frappe.pearnly.register("pearnly-tasks", "erp.work.", "erp.nav.work", (page, t, wrapper) =>
	frappe.pearnly.erpWork(page, t, wrapper));
