(() => {
	const p = frappe.pearnly;
	p.sales = async (page, t) => {
		const api = "pearnly_erp_portal.sales.api.", canWrite = frappe.model.can_write("ERP Sales Document");
		let list;
		const keys = ["buyer_name", "doc_date", "doc_kind", "has_vat", "vat_rate", "party_type", "tax_id", "address",
			"branch_type", "branch_no", "payment_status", "payment_method", "payment_date", "note"];
		async function editor(name) {
			const doc = name ? await p.call(api + "api_get_sale_document", { name }) : {};
			const editable = canWrite && (!name || ["draft", "pending_approval"].includes(doc.status));
			let d;
			const clean = values => ({ ...Object.fromEntries(keys.map(key => [key, values[key]])), lines: p.trade.clean(values.lines) });
			const fields = [
				{ fieldtype: "Data", fieldname: "buyer_name", label: t("trade.buyer"), reqd: 1 },
				{ fieldtype: "Date", fieldname: "doc_date", label: t("trade.date"), default: frappe.datetime.get_today(), reqd: 1 },
				{ fieldtype: "Select", fieldname: "doc_kind", label: t("trade.kind"), options: "quotation\ntax_invoice\ntax_invoice_simple\ntax_invoice_receipt\nreceipt", default: "tax_invoice_simple", read_only: !!name },
				{ fieldtype: "Select", fieldname: "party_type", label: t("trade.buyerType"), options: "person\ncompany", default: "person" },
				{ fieldtype: "Data", fieldname: "tax_id", label: t("trade.taxId") },
				{ fieldtype: "Small Text", fieldname: "address", label: t("trade.address") },
				{ fieldtype: "Select", fieldname: "branch_type", label: t("trade.branchType"), options: "\nhq\nbranch", default: "hq" },
				{ fieldtype: "Data", fieldname: "branch_no", label: t("trade.branchNo") },
				{ fieldtype: "Check", fieldname: "has_vat", label: t("trade.hasVat"), default: 0 },
				{ fieldtype: "Percent", fieldname: "vat_rate", label: t("trade.vat"), default: 7 },
				p.trade.lines(t, { readonly: !editable, data: doc.lines || [{ qty: 1 }] }),
				{ fieldtype: "Select", fieldname: "payment_status", label: t("trade.paymentState"), options: "unpaid\npaid", default: "unpaid" },
				{ fieldtype: "Data", fieldname: "payment_method", label: t("trade.method") },
				{ fieldtype: "Date", fieldname: "payment_date", label: t("trade.paymentDate") },
				{ fieldtype: "Small Text", fieldname: "note", label: t("trade.note") },
			].map(field => ({ ...field, read_only: field.read_only || !editable ? 1 : 0,
				default: doc[field.fieldname] ?? field.default }));
			if (name) fields.push({ fieldtype: "Data", fieldname: "stock_state", label: t("trade.stockState"), read_only: 1,
				default: t(doc.status === "void" ? "trade.void" : doc.stock_posted_at ? "trade.stockPosted" : "trade.stockPending") });
			if (editable && name) fields.push({ fieldtype: "Button", fieldname: "issue", label: t("trade.issue"), click: () => p.trade.act(d, async () => {
				const values = d.get_values(); if (!values) return;
				const changes = clean(values); delete changes.doc_kind;
				await p.call(api + "api_update_sale_document", { name, ...changes }, "POST");
				await p.call(api + "api_issue_sale_document", { name }, "POST");
				d.hide(); await list.reload(); await editor(name);
			}) });
			if (doc.status === "issued" && canWrite) fields.push({ fieldtype: "Button", fieldname: "void", label: t("trade.void"),
				click: () => frappe.confirm(t("trade.voidConfirm"), () => p.trade.act(d, async () => {
					await p.call(api + "api_void_sale_document", { name }, "POST"); d.hide(); await list.reload(); await editor(name);
				})) });
			d = p.dialog(doc.doc_no || name || t("trade.newSale"), fields, editable ? t("trade.save") : t("trade.close"), async (values, dialog) => {
				if (!editable) { dialog.hide(); return; }
				const changes = clean(values); if (name) delete changes.doc_kind;
				const result = await p.call(api + (name ? "api_update_sale_document" : "api_create_sale_document"), { ...changes, ...(name ? { name } : {}) }, "POST");
				dialog.hide(); await list.reload(); await editor(result.name);
			});
			d.$wrapper.find(".modal-dialog").addClass("modal-xl");
		}
		list = p.list({ page, doctype: "ERP Sales Document", on_open: row => editor(row.name),
			load: p.trade.load(api, "api_list_sales", "api_sale_document_total"),
			columns: [
				{ fieldname: "buyer_name", label: t("trade.buyer") },
				{ fieldname: "doc_no", label: t("trade.docNo") },
				{ fieldname: "doc_date", label: t("trade.date") },
				{ fieldname: "doc_kind", label: t("trade.kind"), format: value => __(value) },
				{ fieldname: "status", label: t("trade.status"), format: value => __(value) },
				{ fieldname: "grand_total", label: t("trade.total"), format: value => p.money(value) },
			],
		});
		if (canWrite) page.set_primary_action(t("trade.newSale"), () => editor());
		p.add_refresh(page, () => list.reload()); await list.ready;
	};
})();
