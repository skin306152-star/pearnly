(() => {
	const p = frappe.pearnly;
	p.stockEntry = async (page, t, direction) => {
		const api = "pearnly_erp_portal.inventory.api.", canWrite = frappe.model.can_write("ERP Stock Entry");
		let list;
		async function editor(name) {
			const doc = name ? await p.call(api + "api_get_stock_entry", { name }) : {};
			const editable = canWrite && (!name || doc.status === "draft");
			let d;
			const clean = values => ({ posting_date: values.posting_date, doc_no: values.doc_no,
				reason: values.reason, lines: p.trade.clean(values.lines) });
			const fields = [
				{ fieldtype: "Date", fieldname: "posting_date", label: t("trade.date"), default: doc.posting_date || frappe.datetime.get_today(), reqd: 1 },
				{ fieldtype: "Data", fieldname: "doc_no", label: t("trade.docNo"), default: doc.doc_no },
				p.trade.lines(t, { stock: true, cost: true, readonly: !editable, data: doc.lines || [{ qty: 1 }] }),
				{ fieldtype: "Small Text", fieldname: "reason", label: t("trade.note"), default: doc.reason },
			].map(field => ({ ...field, read_only: editable ? 0 : 1 }));
			if (name && editable) fields.push({ fieldtype: "Button", fieldname: "submit", label: t("trade.submit"),
				click: () => p.trade.act(d, async () => {
					const values = d.get_values(); if (!values) return;
					await p.call(api + "api_update_stock_entry", { name, ...clean(values) }, "POST");
					await p.call(api + "api_submit_stock_entry", { name }, "POST");
					d.hide(); await list.reload(); await editor(name);
				}) });
			if (doc.status === "submitted" && canWrite) fields.push({ fieldtype: "Button", fieldname: "void", label: t("trade.void"),
				click: () => frappe.confirm(t("trade.voidConfirm"), () => p.trade.act(d, async () => {
					await p.call(api + "api_void_stock_entry", { name }, "POST");
					d.hide(); await list.reload(); await editor(name);
				})) });
			d = p.dialog(name || t(direction === "in" ? "trade.newIn" : "trade.newOut"), fields,
				editable ? t("trade.save") : t("trade.close"), async (values, dialog) => {
					if (!editable) { dialog.hide(); return; }
					const result = await p.call(api + (name ? "api_update_stock_entry" : "api_create_stock_entry"), {
						...(name ? { name } : { direction }), ...clean(values),
					}, "POST");
					dialog.hide(); await list.reload(); await editor(result.name);
				});
			d.$wrapper.find(".modal-dialog").addClass("modal-xl");
		}
		// 没选中行时右上角还是原来那颗"新建入库/出库"。
		function restore_new() {
			if (canWrite) {
				page.set_primary_action(t(direction === "in" ? "trade.newIn" : "trade.newOut"),
					() => editor());
			} else page.clear_primary_action();
		}
		// 选中若干张**已过账**的单据 → 右上角出现"作废选中"。库存流水不改不删，作废是追加
		// 反向流水（服务端 `api_void_stock_entry` 同一条路径），所以这里给的不是删除。
		const selection = p.selection_action({
			page, list: () => list, empty: restore_new,
			label: rows => t("trade.action.voidCount", rows.length),
			when: row => row.status === "submitted",
			reason: () => t("trade.voidBlocked"),
			confirm: rows => t("trade.voidConfirmCount", rows.length),
			run: async rows => {
				for (const row of rows) {
					await p.call(api + "api_void_stock_entry", { name: row.name }, "POST");
				}
			},
			success: rows => t("trade.voidNotice", rows.length),
		});
		list = p.list({ page, doctype: "ERP Stock Entry", selectable: true, on_selection: selection,
			on_open: row => editor(row.name),
			load: p.trade.load(api, "api_list_stock_entries", "api_stock_entry_total", () => ({ direction })),
			columns: [
				{ fieldname: "name", label: t("trade.docNo") },
				{ fieldname: "posting_date", label: t("trade.date") },
				{ fieldname: "doc_no", label: t("trade.reference") },
				{ fieldname: "status", label: t("trade.status"), format: value => __(value) },
				{ fieldname: "reason", label: t("trade.note") },
			],
		});
		restore_new();
		p.add_refresh(page, () => list.reload());
		await list.ready;
	};
})();
