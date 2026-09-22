(() => {
	const p = frappe.pearnly;
	p.purchase = async (page, t) => {
		const api = "pearnly_erp_portal.purchasing.api.", tr = await p.trade.texts();
		let list;
		const clean = values => Object.fromEntries(["supplier_name", "doc_date", "doc_no", "doc_kind", "has_vat", "notes"].map(k => [k, values[k]]));
		async function refresh() {
			await list?.reload();
			const sum = await p.call(api + "api_summary");
			page.set_indicator(t("purchase.notice.totals", sum.count, p.money(sum.grand_total),
				p.money(sum.net_payable), p.money(sum.outstanding)), Number(sum.outstanding) ? "orange" : "green");
		}
		async function editor(name) {
			const doc = name ? await p.call(api + "api_get_purchase", { name }) : {};
			const editable = (!name || doc.status === "draft") && frappe.model.can_write("Purchase Document");
			let d;
			const action = (method, extra = {}) => p.trade.act(d, async () => {
				await p.call(api + method, { name, ...extra }, "POST");
				d.hide(); await refresh(); await editor(name);
			});
			const fields = [
				{ fieldtype: "Data", fieldname: "supplier_name", label: t("purchase.field.supplier"), reqd: 1 },
				{ fieldtype: "Date", fieldname: "doc_date", label: t("purchase.field.docDate"), default: frappe.datetime.get_today(), reqd: 1 },
				{ fieldtype: "Data", fieldname: "doc_no", label: t("purchase.field.docNo") },
				{ fieldtype: "Select", fieldname: "doc_kind", label: tr("trade.kind"), options: "goods\nservice", default: "goods" },
				{ fieldtype: "Check", fieldname: "has_vat", label: tr("trade.hasVat"), default: 1 },
				p.trade.lines(tr, { readonly: !editable, data: doc.lines || [{ qty: 1, vat_rate: 7 }] }),
				{ fieldtype: "Small Text", fieldname: "notes", label: tr("trade.note") },
			];
			if (name) fields.push({ fieldtype: "Data", fieldname: "stock_state", label: tr("trade.stockState"), read_only: 1,
				default: tr(doc.status === "void" ? "trade.void" : doc.stock_posted_at ? "trade.stockPosted" : "trade.stockPending") });
			if (editable && name) fields.push({ fieldtype: "Button", fieldname: "submit", label: tr("trade.submitReceive"),
				click: () => p.trade.act(d, async () => {
					const values = d.get_values(); if (!values) return;
					await p.call(api + "api_update_purchase", { name, ...clean(values), lines: p.trade.clean(values.lines) }, "POST");
					await p.call(api + "api_submit_purchase", { name, receive_stock: 1 }, "POST");
					d.hide(); await refresh(); await editor(name);
				}) });
			if (name && doc.status !== "void" && frappe.model.can_write("Purchase Document")) {
				fields.push({ fieldtype: "Button", fieldname: "void", label: tr("trade.void"),
					click: () => frappe.confirm(tr("trade.voidConfirm"), () => action("api_void_purchase")) });
			}
			d = p.dialog(name || t("purchase.action.new"), fields.map(field => field.fieldtype === "Button" || field.read_only
				? field : { ...field, read_only: editable ? 0 : 1 }), editable ? tr("trade.save") : tr("trade.close"),
				async (values, dialog) => {
					if (!editable) { dialog.hide(); return; }
					const result = await p.call(api + (name ? "api_update_purchase" : "api_create_purchase"), {
						...clean(values), ...(name ? { name } : {}), lines: p.trade.clean(values.lines),
					}, "POST");
					dialog.hide(); await refresh(); await editor(result.name);
				});
			d.$wrapper.find(".modal-dialog").addClass("modal-xl");
			for (const field of ["supplier_name", "doc_date", "doc_no", "doc_kind", "notes", "has_vat"]) {
				if (doc[field] !== undefined) d.set_value(field, doc[field]);
			}
		}
		// 没选中行时右上角还是原来那颗"新建采购"。
		function restore_new() {
			if (frappe.model.can_create("Purchase Document")) {
				page.set_primary_action(t("purchase.action.new"), () => editor());
			} else page.clear_primary_action();
		}
		// 选中若干张**已提交**的采购单 → 右上角出现"作废选中"（服务端 `api_void_purchase`）。
		// 草稿状态直接用单据里的删除；这里是已过账那一档的撤销。
		const selection = p.selection_action({
			page, list: () => list, empty: restore_new,
			// 这一页的 `t` 是 purchase.* 命名空间，`tr` 才是 trade.*（作废那几句在 trade 里）。
			label: rows => tr("trade.action.voidCount", rows.length),
			// 已收付款的单据服务端不让直接作废（先冲掉付款），这里同样不让点，并说明原因。
			when: row => row.status === "submitted" && !(Number(row.paid_amount) > 0),
			reason: row => (row.status === "submitted"
				? tr("trade.voidPaid")
				: tr("trade.voidBlocked")),
			confirm: rows => tr("trade.voidConfirmCount", rows.length),
			run: async rows => {
				for (const row of rows) {
					await p.call(api + "api_void_purchase", { name: row.name }, "POST");
				}
			},
			success: rows => tr("trade.voidNotice", rows.length),
		});
		list = p.list({ page, doctype: "Purchase Document", selectable: true, on_selection: selection,
			on_open: row => editor(row.name),
			load: async ({ start, page_length }) => {
				const [items, total] = await Promise.all([
					p.call(api + "api_list_purchases", { start, limit: page_length }), p.call(api + "api_purchase_total"),
				]); return { items, total };
			},
			columns: [
				{ fieldname: "supplier_name", label: t("purchase.field.supplier") },
				{ fieldname: "doc_no", label: t("purchase.field.docNo") },
				{ fieldname: "doc_date", label: t("purchase.col.date") },
				{ fieldname: "status", label: t("purchase.col.status"), format: value => __(value) },
				{ fieldname: "grand_total", label: t("purchase.col.grandTotal"), format: value => p.money(value) },
				{ fieldname: "net_payable", label: t("purchase.card.netPayable"), format: value => p.money(value) },
			],
		});
		restore_new();
		p.add_refresh(page, refresh);
		await list.ready; await refresh();
	};
})();
