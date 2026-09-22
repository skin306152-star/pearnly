/* Existing LINE inbox, scoped and confirmed through the same purchase posting path. */
(() => {
	const p = frappe.pearnly, u = p.purchase_ui, api = "pearnly_erp_portal.integrations.api.";
	u.intakes = async ({page, go, still_current}) => {
		page.set_title(__("LINE"));
		p.breadcrumbs({label: __("Purchase Invoice"), route: p.subroute("pearnly-purchase"), trail: [{label: __("LINE"), route: ""}]});
		const rows = await p.call(api + "api_list_line_intakes", {direction: "purchase", status: "received", limit: 0});
		if (!still_current()) return () => {};
		const list = p.list({page, doctype: "ERP LINE Intake", load: async () => ({items: rows, total: rows.length}),
			on_open: row => go("intake", row.name),
			columns: [{fieldname: "name", label: __("ID")}, {fieldname: "received_at", label: __("Creation"), format: p.datetime_text},
				{fieldname: "sent_by", label: __("User")}, {fieldname: "note", label: __("Remarks")}],
			row_actions: row => [{id: "discard", label: __("Cancel"), action: () => p.dialog(__("Cancel"), [u.field("reason", "Reason for Cancellation", "Small Text")],
				__("Confirm"), async (values, dialog) => {await p.call(api + "api_discard_line_intake", {name: row.name, ...values}, "POST"); dialog.hide(); await go();})}],
		});
		await list.ready;
		return () => list.destroy();
	};
	u.get_intake = async name => {
		const intake = await p.call(api + "api_get_line_intake", {name});
		const files = await frappe.db.get_list("File", {filters: {attached_to_doctype: "ERP LINE Intake", attached_to_name: name}, fields: ["name", "file_name", "file_url"]});
		return {intake, doc: {source: "line", image_sha256: intake.file_hash,
			attachments: files.map(row => ({file: row.name, file_name: row.file_name, file_url: row.file_url, kind: "bill", intake: true}))}};
	};
})();
