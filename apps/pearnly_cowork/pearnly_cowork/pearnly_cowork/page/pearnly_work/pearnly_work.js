/* 工作协调（团队看板）：渲染在 `public/desk/work.js`（到用时再取，加载先后由框架决定）。 */
frappe.pearnly.register("pearnly-work", "cowork.work.", "cowork.nav.work", (page, t, wrapper) =>
	frappe.pearnly.coworkWork(page, t, wrapper));
