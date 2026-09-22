(() => {
	const p = frappe.pearnly;
	p.stockEntry = async (page, t, direction) => {
		const api = "pearnly_erp_portal.inventory.api.", canWrite = frappe.model.can_write("ERP Stock Entry");
		let list;
		async function editor(name) {
			const doc = name ? await p.call(api + "api_get_stock_entry", { name }) : {};
			const editable = canWrite && (!name || doc.status === "draft");
			// 出库不填单价：旧线上出库单是按**结存成本**计价的（`apply_issue_costs`），
			// 单价那一格在界面上只读、由服务端在过账时算，所以这里不给人填。
			const priced = direction === "in";
			let d;
			// `doc_no` 不进请求体：单据号由服务端按入库 IR / 出库 IS 发，界面上只读。
			const clean = values => ({ posting_date: values.posting_date,
				reason: values.reason, lines: p.trade.clean(values.lines) });
			const fields = [
				// 字段顺序与旧线上入库/出库单据一致：单据号、日期、仓库，然后明细与说明。
				{ fieldtype: "Data", fieldname: "doc_no", label: t("trade.docNo"), read_only: 1,
					default: doc.doc_no, description: doc.doc_no ? "" : t("stock.field.docNoAuto") },
				{ fieldtype: "Date", fieldname: "posting_date", label: t("trade.date"), default: doc.posting_date || frappe.datetime.get_today(), reqd: 1 },
				{ fieldtype: "Link", fieldname: "warehouse", label: t("trade.warehouse"), options: "ERP Warehouse",
					default: doc.warehouse, reqd: 1 },
				p.trade.lines(t, { stock: true, cost: priced, readonly: !editable || !priced,
					data: doc.lines || [{ qty: 1 }] }),
				{ fieldtype: "Small Text", fieldname: "reason", label: t("trade.note"), default: doc.reason },
			].map(field => ({ ...field, read_only: editable ? 0 : 1 }));
			fields.find(field => field.fieldname === "doc_no").read_only = 1;
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
				// 首列是**单据编号**（系统发的 IR/IS 号）：旧线上列表也是拿它当行标题，
				// 内部单据名只在点开单据时用，不占一列。
				// 老单据（编号是后来才发的）没有号，落回内部单据名，别留一列空白。
				{ fieldname: "doc_no", label: t("trade.docNo"),
					format: (_value, row) => row.doc_no || row.name },
				{ fieldname: "posting_date", label: t("trade.date") },
				{ fieldname: "warehouse_name", label: t("trade.warehouse") },
				{ fieldname: "status", label: t("trade.status"), format: value => __(value) },
				{ fieldname: "reason", label: t("trade.note") },
			],
		});
		restore_new();
		p.add_refresh(page, () => list.reload());
		await list.ready;
	};
})();
