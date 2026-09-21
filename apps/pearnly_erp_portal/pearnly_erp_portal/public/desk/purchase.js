(() => {
	const p = frappe.pearnly;
	// 采购记录是**一列单据**：用原生列表（与商品/库存/盘点同一个渲染器、同一套分页），
	// 四个汇总数放页面指示器，不再自绘"卡片 + 表格"。
	p.purchase = async (page, t) => {
		const api = "pearnly_erp_portal.purchase.";
		let list;

		async function refresh() {
			await list?.reload();
			const sum = await p.call(api + "api_summary");
			// 服务端的金额是字符串（Decimal 转出来的），判断要用 Number，别拿字符串当布尔。
			page.set_indicator(
				t("purchase.notice.totals", sum.count, p.money(sum.grand_total),
					p.money(sum.net_payable), p.money(sum.outstanding)),
				Number(sum.outstanding) ? "orange" : "green",
			);
		}

		list = p.list({
			page,
			doctype: "Purchase Document",
			load: async ({ start, page_length }) => {
				// 列表框架给的是 {start, page_length}；服务端的参数名是 start / limit。
				const [items, total] = await Promise.all([
					p.call(api + "api_list_purchases", { start, limit: page_length }),
					p.call(api + "api_purchase_total", {}),
				]);
				return { items, total };
			},
			columns: [
				{ fieldname: "supplier_name", label: t("purchase.field.supplier") },
				{ fieldname: "doc_no", label: t("purchase.field.docNo") },
				{ fieldname: "doc_date", label: t("purchase.col.date") },
				{ fieldname: "status", label: t("purchase.col.status"),
					format: (value) => __(value) },
				{ fieldname: "grand_total", label: t("purchase.col.grandTotal"),
					format: (value, row) => p.money(value, row.currency) },
				{ fieldname: "net_payable", label: t("purchase.card.netPayable"),
					format: (value, row) => p.money(value, row.currency) },
			],
		});
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
		await list.ready;
		await refresh();
	};
})();
