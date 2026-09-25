/* Purchase view of the shared document viewer (`pearnly_core/public/desk/viewer.js`).
 * The box itself is shared with Sales and Cowork; here we only say where a page image
 * comes from: PDFs go through the purchase file service, images are read by their URL. */
(() => {
	const u = frappe.pearnly.purchase_ui;
	u.document_viewer = wrapper => frappe.pearnly.document_viewer(wrapper, {
		load: (row, page) => (/\.pdf$/i.test(row.file_name || row.file_url)
			? u.call("api_purchase_file_page", {file: row.file, page})
			: Promise.resolve({image: row.file_url, pages: 1})),
	});
})();
