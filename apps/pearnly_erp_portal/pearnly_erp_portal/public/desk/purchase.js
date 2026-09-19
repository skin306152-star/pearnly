(() => {
	const p = frappe.pearnly;
	p.purchase = async (page, t) => {
		const api = "pearnly_erp_portal.purchase.";
		let table;
		async function refresh() {
			const [rows, sum] = await Promise.all([
				p.call(api + "api_list_purchases", { limit: 50 }),
				p.call(api + "api_summary"),
			]);
			table?.destroy();
			page.main.empty();
			const fields = [];
			for (const [name, key] of [
				["count", "purchase.card.count"],
				["grand_total", "purchase.col.grandTotal"],
				["net_payable", "purchase.card.netPayable"],
				["outstanding", "purchase.card.outstanding"],
			]) {
				if (fields.length) fields.push({ fieldtype: "Column Break" });
				fields.push({
					fieldtype: "Data",
					fieldname: name,
					label: t(key),
					read_only: 1,
					default: name === "count" ? sum[name] : format_currency(sum[name], "THB"),
				});
			}
			fields.push({ fieldtype: "Section Break" }, { fieldtype: "HTML", fieldname: "rows" });
			const form = p.layout(page.main, fields);
			table = await p.table(
				form.fields_dict.rows.$wrapper,
				[
					t("purchase.col.name"),
					t("purchase.field.supplier"),
					t("purchase.field.docNo"),
					t("purchase.col.date"),
					t("purchase.col.status"),
					t("purchase.col.grandTotal"),
					t("purchase.card.netPayable"),
				],
				rows.map((r) => [
					r.name,
					r.supplier_name,
					r.doc_no,
					r.doc_date,
					__(r.status),
					format_currency(r.grand_total, r.currency),
					format_currency(r.net_payable, r.currency),
				]),
			);
		}
		if (frappe.model.can_create("Purchase Document")) page.set_primary_action(t("purchase.action.new"), () => {
			const d = p.dialog(
				t("purchase.action.new"),
				[
					{
						fieldtype: "Data",
						fieldname: "supplier_name",
						label: t("purchase.field.supplier"),
						reqd: 1,
					},
					{
						fieldtype: "Date",
						fieldname: "doc_date",
						label: t("purchase.field.docDate"),
						default: frappe.datetime.get_today(),
						reqd: 1,
					},
					{ fieldtype: "Data", fieldname: "doc_no", label: t("purchase.field.docNo") },
					{
						fieldtype: "Table",
						fieldname: "lines",
						label: t("purchase.field.lines"),
						reqd: 1,
						in_place_edit: true,
						data: [{ description: "", qty: 1, unit_price: 0, vat_rate: 7 }],
						fields: [
							{
								fieldtype: "Data",
								fieldname: "description",
								label: t("purchase.placeholder.description"),
								in_list_view: 1,
								reqd: 1,
							},
							{
								fieldtype: "Float",
								fieldname: "qty",
								label: t("purchase.placeholder.qty"),
								in_list_view: 1,
								reqd: 1,
								default: 1,
							},
							{
								fieldtype: "Float",
								fieldname: "unit_price",
								label: t("purchase.placeholder.unitPrice"),
								precision: 2,
								in_list_view: 1,
								default: 0,
							},
							{
								fieldtype: "Float",
								fieldname: "vat_rate",
								label: t("purchase.placeholder.vatRate"),
								precision: 2,
								in_list_view: 1,
								default: 7,
							},
						],
					},
					{
						fieldtype: "HTML",
						options: `<p class="text-muted">${p.escape(t("purchase.hint.serverTotal"))}</p>`,
					},
				],
				t("purchase.action.save"),
				async (values, dialog) => {
					const lines = values.lines.map(
						({ description, qty, unit_price, vat_rate }) => ({
							description,
							qty,
							unit_price,
							vat_rate,
						}),
					);
					const result = await p.call(
						api + "api_create_purchase",
						{ ...values, lines: JSON.stringify(lines) },
						"POST",
					);
					dialog.hide();
					p.success(t("purchase.notice.created", result.name));
					await refresh();
				},
			);
			d.$wrapper.find(".modal-dialog").addClass("modal-lg");
		});
		p.add_refresh(page, refresh);
		await refresh();
	};
})();
