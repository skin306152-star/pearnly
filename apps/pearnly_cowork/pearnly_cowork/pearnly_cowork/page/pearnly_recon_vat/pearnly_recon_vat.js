/* 对账中心 · vat：渲染在 `public/desk/recon.js`（到用时再取，加载先后由框架决定）。 */
frappe.pearnly.register("pearnly-recon-vat", "cowork.recon.", "cowork.nav.reconVat", (page, t, wrapper) =>
	frappe.pearnly.coworkRecon("vat")(page, t, wrapper));
