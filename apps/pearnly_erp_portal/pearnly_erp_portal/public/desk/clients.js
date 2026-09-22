/* 客户主档：与供应商同一套形状（原生列表 + 一个弹窗），字段换成买方那几样。
 *
 * 用户 2026-09-23 口径同供应商："单独做一个供应商和客户的菜单，和销售采购联动上就好了"。
 *
 * **和销售联动**：销售单据上的"客户"是指向这张表的原生 Link
 * （`ERP Sales Document.customer`）；这里建完，开销售单时就能选到，
 * 选了之后客户名/类型/地址由销售页自动回填（`sales-fields.js` 的 onchange）。
 * 收银也一样（客户表在收银岗位上是只读可选的）。
 */
(() => {
	const p = frappe.pearnly;

	p.clients = async (page, t, wrapper) => {
		const api = "pearnly_erp_portal.masters.api.";
		const can_write = frappe.model.can_write("ERP Customer");
		// 与供应商页同一形状的本地简写。
		const f = (fieldname, label, fieldtype = "Data", options = {}) =>
			({fieldname, label: __(label), fieldtype, ...options});
		let list;

		const party_label = value => ({company: t("client.party.company"), person: t("client.party.person"),
			foreign: t("client.party.foreign"), anonymous: t("client.party.anonymous")})[value] || value || "";

		const columns = () => [
			{ fieldname: "customer_name", label: t("client.col.name") },
			{ fieldname: "party_type", label: t("client.col.type"), format: party_label },
			{ fieldname: "tax_id", label: t("client.col.taxId") },
			{ fieldname: "contact_phone", label: t("client.col.phone") },
			{ fieldname: "address", label: t("client.col.address") },
			{ fieldname: "is_active", label: t("client.col.status"),
				format: value => (Number(value) ? "" : t("client.inactive")) },
		];

		// 一个弹窗干两件事：新建、改（`name` 不为空就是改）。
		function editor(row = {}) {
			const editable = can_write;
			const d = p.dialog(row.name || t("client.new"), [
				f("customer_name", "client.field.name", "Data", {reqd: 1, default: row.customer_name || ""}),
				f("party_type", "client.field.type", "Select", {
					options: [{value: "company", label: t("client.party.company")},
						{value: "person", label: t("client.party.person")},
						{value: "foreign", label: t("client.party.foreign")},
						{value: "anonymous", label: t("client.party.anonymous")}],
					default: row.party_type || "company"}),
				f("contact_phone", "client.field.phone", "Data", {default: row.contact_phone || ""}),
				f("tax_id", "client.field.taxId", "Data", {default: row.tax_id || ""}),
				f("address", "client.field.address", "Small Text", {default: row.address || ""}),
				f("note", "client.field.note", "Small Text", {default: row.note || ""}),
				...(row.name ? [f("is_active", "client.field.active", "Check", {default: Number(row.is_active) ? 1 : 0})] : []),
			].map(field => ({...field, read_only: field.read_only ?? !editable})),
			editable ? (row.name ? t("client.save") : t("client.create")) : __("Close"),
			async (values, dialog) => {
				if (!editable) {dialog.hide(); return;}
				const method = row.name ? "api_update_customer" : "api_create_customer";
				await p.call(api + method, row.name ? {name: row.name, ...values} : values, "POST");
				dialog.hide();
				await list?.reload();
			});
			return d;
		}

		page.set_title(t("client.title"));
		p.breadcrumbs({label: t("client.title"), route: p.subroute("pearnly-clients")});
		const search = page.add_field({fieldname: "q", label: __("Search"), fieldtype: "Data",
			change: () => void list?.reload()});
		list = p.list({page, doctype: "ERP Customer",
			on_open: row => editor(row),
			columns: columns(),
			load: async () => {
				const rows = await p.call(api + "api_list_customers",
					{q: search.get_value() || undefined, include_inactive: 1, limit: 0});
				return {items: rows, total: rows.length};
			},
			empty_action: can_write ? {label: t("client.new"), action: () => editor()} : null,
		});
		if (can_write) page.add_inner_button(t("client.new"), () => editor());
		p.add_refresh(page, () => void list.reload());
		await list.ready;
	};
})();
