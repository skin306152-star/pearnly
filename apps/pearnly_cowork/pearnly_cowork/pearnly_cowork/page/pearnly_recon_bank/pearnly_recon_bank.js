/* 对账中心 · bank：渲染在 `public/desk/recon.js`（到用时再取，加载先后由框架决定）。 */
frappe.pearnly.register("pearnly-recon-bank", "cowork.recon.", "cowork.nav.reconBank", (page, t, wrapper) =>
	frappe.pearnly.coworkRecon("bank")(page, t, wrapper));
