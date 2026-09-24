/* ERPNext POS components are loaded once by hooks.py; this is the native Page entry. */
frappe.provide("frappe.pearnly");
frappe.provide("erpnext.PointOfSale");

(() => {
	const p = frappe.pearnly;
	// The former ?pearnly_pos_next=1 bookmark uses the same default route now.
	p.cashier = (page) => p.pos_next_route(page);
})();
