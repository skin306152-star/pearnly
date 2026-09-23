(() => {
	const p = frappe.pearnly;
	p.intake = async (page, t) => {
		const api = "pearnly_cowork.intake.";
		let table,
			form,
			status = "",
			direction = "";
		const states = [
			"draft",
			"recognized",
			"confirmed",
			"queued",
			"pushed",
			"failed",
			"rejected",
		];
		const options = (values, prefix) =>
			values.map((value) => ({ value, label: t(prefix + value) }));
		async function refresh() {
			const [rows, sum] = await Promise.all([
				p.call(api + "api_list_intake", { status, direction, limit: 50 }),
				p.call(api + "api_summary"),
			]);
			table?.destroy();
			page.main.empty();
			form = p.layout(page.main, [
				{ fieldtype: "Section Break", label: t("intake.title") },
				{
					fieldtype: "Int",
					fieldname: "total",
					label: __("Total"),
					read_only: 1,
					default: sum.total,
				},
				{ fieldtype: "Column Break" },
				{
					fieldtype: "Data",
					fieldname: "purchase",
					label: t("intake.dir.purchase"),
					read_only: 1,
					default: p.money(sum.purchase_confirmed),
				},
				{ fieldtype: "Column Break" },
				{
					fieldtype: "Data",
					fieldname: "sales",
					label: t("intake.dir.sales"),
					read_only: 1,
					default: p.money(sum.sales_confirmed),
				},
				{ fieldtype: "Section Break" },
				{
					fieldtype: "Select",
					fieldname: "status",
					label: t("intake.col.status"),
					default: status,
					options: [
						{ value: "", label: __("All") },
						...options(states, "intake.status."),
					],
					change() {
						status = form.get_value("status");
						void refresh();
					},
				},
				{ fieldtype: "Column Break" },
				{
					fieldtype: "Select",
					fieldname: "direction",
					label: t("intake.col.direction"),
					default: direction,
					options: [
						{ value: "", label: __("All") },
						...options(["purchase", "sales"], "intake.dir."),
					],
					change() {
						direction = form.get_value("direction");
						void refresh();
					},
				},
				{ fieldtype: "Section Break" },
				{ fieldtype: "HTML", fieldname: "rows" },
			]);
			const parent = form.fields_dict.rows.$wrapper;
			const nameColumn = {
				name: t("intake.col.name"),
				format: (value) =>
					`<a href="#" data-intake="${p.escape(value)}">${p.escape(value)}</a>`,
			};
			table = await p.table(
				parent,
				[
					nameColumn,
					t("intake.col.direction"),
					t("intake.col.counterparty"),
					t("intake.field.docNo"),
					t("intake.col.date"),
					t("intake.col.status"),
					t("intake.col.grandTotal"),
				],
				rows.map((r) => [
					r.name,
					t("intake.dir." + r.direction),
					r.counterparty_name,
					r.doc_no,
					r.doc_date,
					t("intake.status." + r.status),
					p.money(r.grand_total, r.currency),
				]),
			);
			parent.on("click", "[data-intake]", (event) => {
				event.preventDefault();
				const row = rows.find((r) => r.name === event.currentTarget.dataset.intake);
				// Queueing only changes status today; no implemented delivery consumer.
				const action = frappe.model.can_write("Intake Record") && ["draft", "recognized"].includes(row.status)
					? "confirm" : null;
				if (!action) {
					frappe.msgprint(p.escape(t("intake.status." + row.status)));
					return;
				}
				frappe.confirm(
					p.escape(t("intake.action." + action) + " · " + row.name),
					async () => {
						const method = "api_confirm_intake";
						const result = await p.call(
							api + method,
							{
								name: row.name,
							},
							"POST",
						);
						p.success(
							t(
								"intake.notice.moved",
								row.name,
								t("intake.status." + result.status),
							),
						);
						await refresh();
					},
				);
			});
		}
		if (frappe.model.can_create("Intake Record")) page.set_primary_action(t("intake.action.new"), () =>
			p.dialog(
				t("intake.action.new"),
				[
					{
						fieldtype: "Select",
						fieldname: "direction",
						label: t("intake.col.direction"),
						options: options(["purchase", "sales"], "intake.dir."),
						default: "purchase",
						reqd: 1,
					},
					{
						fieldtype: "Data",
						fieldname: "counterparty_name",
						label: t("intake.col.counterparty"),
						reqd: 1,
					},
					{ fieldtype: "Data", fieldname: "doc_no", label: t("intake.field.docNo") },
					{
						fieldtype: "Date",
						fieldname: "doc_date",
						label: t("intake.field.docDate"),
						default: frappe.datetime.get_today(),
						reqd: 1,
					},
					{
						fieldtype: "Float",
						precision: 2,
						fieldname: "net_amount",
						label: t("intake.field.netAmount"),
						default: 0,
					},
					{
						fieldtype: "Float",
						precision: 2,
						fieldname: "vat_amount",
						label: t("intake.field.vatAmount"),
						default: 0,
					},
				],
				t("intake.save"),
				async (values, d) => {
					const result = await p.call(
						api + "api_create_intake",
						{ ...values, source: "upload" },
						"POST",
					);
					d.hide();
					p.success(t("intake.notice.created", result.name || result), "click");
					await refresh();
				},
			),
		);
		p.add_refresh(page, refresh);
		await refresh();
	};
})();
