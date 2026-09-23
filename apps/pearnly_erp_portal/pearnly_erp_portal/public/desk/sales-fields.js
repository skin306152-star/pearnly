/* Sales business fields composed only from native Frappe controls. */
(() => {
	const p = frappe.pearnly, u = p.sales_ui = {};
	u.api = "pearnly_erp_portal.sales.api.";
	u.call = (method, args = {}, write = false) => p.call(u.api + method, args, write ? "POST" : "GET");
	u.labels = {
		receipt: __("Receipt"), quotation: __("Quotation"),
		draft: __("Draft"), submitted: __("Submitted"), void: __("Cancelled"),
		unpaid: __("Unpaid"), partial: __("Partly Paid"), paid: __("Paid"),
		cash: __("Cash"), transfer: __("Bank Transfer"), promptpay: __("PromptPay"), other: __("Other"),
		company: __("Company"), person: __("Person"), foreign: __("Foreigner"), anonymous: __("Anonymous"),
		bill: __("trade.groupDoc"), payment_proof: __("Payment Receipt"),
		web: __("Manual"), manual: __("Manual"), ocr: __("OCR"), line: __("LINE"),
		pos: __("Cashier"),
	};
	u.label = value => u.labels[value] || value || "";
	u.options = values => values.map(value => ({ value, label: u.label(value) }));
	u.field = (fieldname, label, fieldtype = "Data", options = {}) => ({fieldname, label: __(label), fieldtype,
		...(fieldtype === "Link" ? {only_select: true, no_create: true, formatter: value => p.escape(value || "")} : {}),
		...(fieldtype === "Select" ? {formatter: value => p.escape(u.label(value))} : {}), ...options});
	u.section = (label, options = {}) => ({fieldtype: "Section Break", label: __(label), ...options});
	u.column = () => ({fieldtype: "Column Break"});
	u.set = (form, name, changes) => {
		const field = form.fields_dict[name];
		if (!field) return;
		Object.assign(field.df, changes); field.refresh();
	};
	u.line_keys = ["product", "description", "qty", "unit", "unit_price", "discount",
		"discount_pct", "vat_applicable", "warehouse", "location"];
	u.clean_lines = lines => (lines || []).filter(row => row.description || row.product)
		.map(row => Object.fromEntries(u.line_keys.filter(key => row[key] !== undefined).map(key => [key, row[key]])));
	// Each item is a native collapsible section + FieldGroup. No generic Grid menus,
	// column configuration, row import or framework document navigation is exposed.
	u.line_fields = (state, line, change) => {
		const f = u.field, col = u.column;
		return [
			u.section(""),
			f("product", "Item", "Link", {options: "ERP Product", hidden: !state.editable, get_query: () => ({filters: {is_active: 1}}),
				async onchange() {
					const name = line.form?.get_value("product"); if (!name || !state.editable || !line.ready) return;
					const item = await p.call("pearnly_erp_portal.products.service.api_get_product", {name});
					if (name !== line.form.get_value("product") || state.disposed) return;
					// 售价取主数据里"设过的"那个价：没设过就不替商家宣布免费（沿用商品页口径）。
					await line.form.set_values({description: item.product_name, unit: item.base_unit || item.unit,
						unit_price: item.price_set ? item.unit_price : 0,
						vat_applicable: item.vat_applicable ? 1 : 0,
						warehouse: item.default_warehouse || "", location: item.default_location || ""});
				}}),
			// 「物料名称」只在没选物料时出现（选了就被带出来、Link 显示的就是主档名字）——
			// 同一个口径见 `purchase-fields.js`（用户 2026-09-23："还有重复的物料字段…"）。
			f("description", "Item Name", "Data", {reqd: 1, depends_on: "eval:!doc.product"}),
			f("qty", "Quantity", "Float", {reqd: 1, default: 1, precision: 6}),
			f("unit", "UOM"), f("unit_price", "Rate", "Currency", {default: 0}), col(),
			f("discount", "Discount Amount", "Currency", {default: 0}),
			f("vat_applicable", "VAT Applies", "Check", {default: 1}),
			f("line_total", "Amount", "Currency", {read_only: 1}),
			u.section("Additional Information", {collapsible: 1, collapsed: 1}),
			// 仓库：官方原生 Link，只列当前公司的仓库（不是自由填写）。
			f("warehouse", "Warehouse", "Link",
				{options: "ERP Warehouse", get_query: p.warehouse_query()}),
			f("location", "Location"),
			...(state.editable ? [u.section(""), f("remove", "Delete", "Button", {click: () => state.remove_line(line)})] : []),
		].map(field => ({...field,
			read_only: ["Button", "Section Break", "Column Break"].includes(field.fieldtype) ? field.read_only : !state.editable || field.read_only,
			onchange: async function() {await field.onchange?.call(this); if (!field.read_only) change();},
			...(field.fieldtype === "Currency" ? {formatter: value => p.money(value)} : {})}));
	};
	u.fields = (state, change) => {
		const {doc, editable} = state, f = u.field, col = u.column;
		return [
			u.section("Attachments", {fieldname: "documents_section"}),
			f("attachment_kind", "Attachment Type", "Select", {options: u.options(["bill", "payment_proof"]), default: "bill", read_only: 0,
				hidden: state.doc.status === "void" || !state.can_write}),
			f("upload", "Attach", "Button", {read_only: 0, hidden: state.doc.status === "void" || !state.can_write, click: () => state.upload?.()}),
			f("recognise", "OCR", "Button", {hidden: !editable || !state.defaults.capabilities.ocr, click: () => state.recognise?.()}),
			f("document_preview", "Preview", "HTML"),
			f("gallery", "Attachments", "Attachment Gallery", {read_only: 1}),
			f("attachment_list", "Attachments", "HTML"),
			u.section("Details", {fieldname: "information_section"}),
			f("system_no", "trade.systemNo", "Data", {read_only: 1}),
			f("doc_date", "Date", "Date", {reqd: 1}),
			f("doc_kind", "Document Type", "Select", {options: u.options(["receipt", "quotation"]), reqd: 1}), col(),
			f("is_paid", "Is Paid", "Check", {hidden: !editable || doc.doc_kind === "quotation"}),
			f("payment_status", "Collection Status", "Select", {hidden: editable, read_only: 1, options: u.options(["unpaid", "partial", "paid"])}),
			f("paid_amount", "Received Amount", "Currency", {hidden: editable, read_only: 1}),
			f("payment_note", "Collection Remarks", "Small Text", {hidden: editable, read_only: 1}),
			f("payment_method", "Collection Method", "Select", {options: u.options(["cash", "transfer", "promptpay", "other"]),
				depends_on: "eval:doc.is_paid && doc.doc_kind === 'receipt'"}),
			f("payment_date", "Collection Date", "Date", {depends_on: "eval:doc.is_paid && doc.doc_kind === 'receipt'"}),
			u.section("trade.buyer"),
			f("customer", "trade.buyer", "Link", {options: "ERP Customer", hidden: !editable, get_query: () => ({filters: {is_active: 1}}),
				async onchange() {
					const name = state.form?.get_value("customer"); if (!name || !editable || !state.ready) return;
					const {message: row} = await frappe.db.get_value("ERP Customer", name,
						["customer_name", "party_type", "address"]);
					if (name !== state.form.get_value("customer") || state.disposed) return;
					await state.form.set_values({buyer_name: row.customer_name, party_type: row.party_type, address: row.address});
				}}),
			// 客户名称由上面那条 Link 带出来（客户主档的 `title_field` 就是 `customer_name`，
			// 所以那条 Link 显示的就是名字），界面上不再摆第二个「客户」输入框
			// （用户 2026-09-23："销售记录里面…两个客户字段"）。值照旧随单据提交，
			// 服务端也会从主档补（`sales/entry.py::_buyer`：`name = name or doc.customer_name`）。
			f("buyer_name", "Customer Name", "Data", {hidden: 1}),
			f("party_type", "trade.buyerType", "Select", {options: u.options(["company", "person", "foreign", "anonymous"])}), col(),
			f("address", "Address", "Small Text"),
			u.section("Items", {fieldname: "items_section"}),
			u.section(""), f("lines", "Items", "HTML"),
			f("add_line", "purchase.action.addLine", "Button", {hidden: !editable, click: () => state.add_line?.()}),
			f("merge_lines", "Merge Items", "Button", {hidden: 1, click: () => state.merge?.()}),
			u.section("Taxes and Charges"),
			f("has_vat", "trade.hasVat", "Check", {hidden: !editable}), col(),
			f("header_discount", "Discount Amount", "Currency", {default: 0}),
			...[["subtotal", "Net Total"], ["discount_total", "Discount Total"], ["vat_amount", "VAT"],
				["grand_total", "Grand Total"]].map(([key, label]) => f(key, label, "Currency", {read_only: 1})),
			u.section(""), f("vat_rate", "VAT Rate", "Percent", {default: state.defaults.default_vat_rate || 7}),
			f("note", "Remarks", "Small Text"),
			...(doc.void_reason ? [f("void_reason", "Reason for Cancellation", "Small Text", {read_only: 1})] : []),
		].map(field => ({...field,
			read_only: ["Button", "HTML", "Tab Break", "Section Break", "Column Break"].includes(field.fieldtype)
				|| field.fieldname === "attachment_kind" ? field.read_only : !editable || field.read_only,
			onchange: async function() { await field.onchange?.call(this); if (editable && !field.read_only) change(); },
			...(field.fieldtype === "Currency" ? {options: "currency", formatter: value => p.money(value)} : {})}));
	};
})();
