/* 对账中心 · income：渲染在 `public/desk/recon.js`（到用时再取，加载先后由框架决定）。 */
frappe.pearnly.register("pearnly-recon-income", "cowork.recon.", "cowork.nav.reconIncome", (page, t, wrapper) =>
	frappe.pearnly.coworkRecon("income")(page, t, wrapper));
