/* Existing LINE inbox, scoped and confirmed through the same sales posting path. */
(() => {
	const p = frappe.pearnly, u = p.sales_ui, api = "pearnly_erp_portal.integrations.api.";
	u.intakes = async ({page, go}) => {
		page.set_title(__("LINE"));
		p.breadcrumbs({label: __("nav.sales"), route: p.subroute("pearnly-sales"), trail: [{label: __("LINE"), route: ""}]});
		let list;
		// 勾选框和官方列表一样每行都有；选中后右上角给「取消选中的 N 条」= 一次丢掉这一批草稿。
		// 单张走行内「取消」，批量这条也弹同一个原因框（一批草稿记同一个原因）—— 所以这里没用
		// `p.selection_action`：它那个口子只有 yes/no，收不到原因。
		const selection = rows => {
			if (!rows.length) {page.clear_primary_action(); return;}
			page.set_primary_action(__("Cancel {0}", [rows.length]), () => p.dialog(__("Cancel"),
				[u.field("reason", "Reason for Cancellation", "Small Text")], __("Confirm"),
				async (values, dialog) => {
					for (const row of rows) {
						await p.call(api + "api_discard_line_intake", {name: row.name, reason: values.reason}, "POST");
					}
					dialog.hide();
					p.success(__("Cancelled {0}", [rows.length]), "cancel");
					await list.reload();
				}));
		};
		list = p.list({page, doctype: "ERP LINE Intake", on_selection: selection,
			// 四列一屏放得下（用户 2026-09-23："内容太多，要滑动才能看得到"）。
			compact_columns: true,
			load: async () => {
				const items = await p.call(api + "api_list_line_intakes",
					{direction: "sales", status: "received", limit: 0});
				return {items, total: items.length};
			},
			on_open: row => go("intake", row.name),
			columns: [
				{fieldname: "name", width: 140, min_width: 120, label: __("ID")},
				{fieldname: "received_at", width: 140, min_width: 120, label: __("Creation"), format: p.datetime_text},
				{fieldname: "sent_by", width: 150, min_width: 120, label: __("User")},
				{fieldname: "note", width: 220, min_width: 150, label: __("Remarks")}],
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
