/* Sales business fields composed only from native Frappe controls. */
(() => {
	const p = frappe.pearnly, u = p.sales_ui = {};
	u.api = "pearnly_erp_portal.sales.api.";
	u.call = (method, args = {}, write = false) => p.call(u.api + method, args, write ? "POST" : "GET");
	u.labels = {
		// Express 列的状态词同单据页（`p.ledger_word`，用户 11-22 统一成 录入中 / 成功 / 失败）
		express_queued: p.ledger_word("queued"), express_held: p.ledger_word("held"), express_failed: p.ledger_word("failed"),
		express_mismatch: p.ledger_word("mismatch"), express_detached: p.ledger_word("detached"),
		express_written: p.ledger_word("written"), express_handled: p.ledger_word("handled"),
		receipt: __("Receipt"), quotation: __("Quotation"),
		draft: __("Draft"), submitted: __("Submitted"), void: __("Voided"),
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
		// 只读时显示名称（Link 的标题缓存，`p.place.titles` 画行前取好），不出链接；没取到的照旧显示编号（11-20 线上：采购详情仓库显示 WH-00002）
		...(fieldtype === "Link" ? {only_select: true, no_create: true,
			formatter: (value, df) => p.escape((value && df?.options && frappe.utils.get_link_title(df.options, value)) || value || "")} : {}),
		...(fieldtype === "Select" ? {formatter: value => p.escape(u.label(value))} : {}), ...options});
	u.section = (label, options = {}) => ({fieldtype: "Section Break", label: __(label), ...options});
	u.column = () => ({fieldtype: "Column Break"});
	u.set = (form, name, changes) => {
		const field = form.fields_dict[name];
		if (!field) return;
		Object.assign(field.df, changes); field.refresh();
	};
	u.line_keys = ["item_type", "product", "description", "qty", "unit", "unit_price", "discount",
		"discount_pct", "vat_applicable", "warehouse", "new_item"];
	// 行上「出库」勾选 = 这一行出不出库（11-06 按行选物料与库存）：不勾就是只记账（服务端 `item_type = service`）
	u.clean_lines = lines => (lines || []).filter(row => row.description || row.product)
		.map(row => ({...row, ...(row.stock_out === undefined ? {} : {item_type: Number(row.stock_out) ? "goods" : "service"})}))
		.map(row => Object.fromEntries(u.line_keys.filter(key => row[key] !== undefined).map(key => [key, row[key]])));
	// Each item is a native collapsible section + FieldGroup. No generic Grid menus,
	// column configuration, row import or framework document navigation is exposed.
	u.line_fields = (state, line, change) => {
		const f = u.field, col = u.column;
		return [
			u.section(""),
			{...p.entry_choice_field("Item", state.editable), reqd: 1},
			f("product", "Item", "Data", {hidden: 1}),
			f("description", "Item Name", "Data", {hidden: 1}),
			f("qty", "Quantity", "Float", {reqd: 1, default: 1, precision: 6}),
			f("unit", "UOM"), f("unit_price", "Rate", "Currency", {default: 0}), col(),
			f("discount", "Discount Amount", "Currency", {default: 0}),
			f("vat_applicable", "VAT Applies", "Check", {default: 1}),
			f("stock_out", "trade.groupOut", "Check", {default: 1}),
			// 仓库按行（11-19，同 Express 原生赊销 / 现销明细）：出库的行选仓，新行跟上一行；只有一个仓的公司不出这一格（服务端取唯一仓）
			f("warehouse", "Warehouse", "Link", {options: "ERP Warehouse", get_query: p.warehouse_query(),
				hidden: state.single_warehouse ? 1 : 0, depends_on: "eval:doc.stock_out"}),
			f("line_total", "Amount", "Currency", {read_only: 1}),
			f("new_item", "Item", "Check", {hidden: 1}),
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
			f("doc_kind", "Document Type", "Select", {options: u.options(["receipt", "quotation"]), reqd: 1}),
			// 收款状态落在左栏单据下面，右栏是收款明细（11-01：只读详情原来左三格右五格）
			f("payment_status", "Collection Status", "Select", {hidden: editable, read_only: 1, options: u.options(["unpaid", "partial", "paid"])}), col(),
			f("is_paid", "Is Paid", "Check", {hidden: !editable || doc.doc_kind === "quotation"}),
			f("paid_amount", "Received Amount", "Currency", {hidden: editable, read_only: 1}),
			f("payment_method", "Collection Method", "Select", {options: u.options(["cash", "transfer", "promptpay", "other"]),
				depends_on: "eval:doc.is_paid && doc.doc_kind === 'receipt'"}),
			f("payment_date", "Collection Date", "Date", {depends_on: "eval:doc.is_paid && doc.doc_kind === 'receipt'"}),
			f("payment_note", "Collection Remarks", "Small Text", {hidden: editable, read_only: 1}),
			u.section("trade.buyer"),
			p.entry_choice_field("Customer", editable),
			f("customer", "Customer", "Data", {hidden: 1}),
			f("buyer_name", "Customer Name", "Data", {hidden: 1}),
			f("party_type", "trade.buyerType", "Select", {options: u.options(["company", "person", "foreign", "anonymous"])}), col(),
			f("address", "Address", "Small Text"),
			u.section("Items", {fieldname: "items_section"}),
			f("lines", "Items", "HTML"),
			f("add_line", "purchase.action.addLine", "Button", {hidden: !editable, click: () => state.add_line?.()}),
			f("bulk_edit", "Bulk Edit", "Button", {hidden: !editable, click: () => state.bulk_edit?.()}),
			f("merge_lines", "Merge Items", "Button", {hidden: 1, click: () => state.merge?.()}),
			u.section("Taxes and Charges"),
			f("has_vat", "trade.hasVat", "Check", {hidden: !editable}),
			// 门市小票多是含税价（旧线上销项的 `price_includes_vat`）：勾上就从金额里反算 VAT。
			f("price_includes_vat", "Prices Include Tax", "Check", {hidden: !editable}),
			// 左栏税设定 / 净额 / 单头折扣，右栏折扣合计 / 税额 / 合计（11-01：税率原来单独落在下一节，左二格右五格）
			f("vat_rate", "VAT Rate", "Percent", {default: state.defaults.default_vat_rate || 7}),
			f("subtotal", "Net Total", "Currency", {read_only: 1}),
			f("header_discount", "Discount Amount", "Currency", {default: 0}), col(),
			...[["discount_total", "Discount Total"], ["vat_amount", "VAT"],
				["grand_total", "Grand Total"]].map(([key, label]) => f(key, label, "Currency", {read_only: 1})),
			// 识别来的单才有（写账前与合计核对，`express.contracts.printed_hold`）；识别后显示
			f("printed_total", "Printed Total", "Currency", {hidden: !(state.doc.ocr_review || state.intake), onchange: () => !state.editable && state.ready
				&& p.save_printed_total("ERP Sales Document", state.doc, state.form?.get_value("printed_total"))}),
			u.section(""), f("note", "Remarks", "Small Text"),
			...(doc.void_reason ? [f("void_reason", "Reason for Cancellation", "Small Text", {read_only: 1})] : []),
		].map(field => ({...field,
			read_only: ["Button", "HTML", "Tab Break", "Section Break", "Column Break"].includes(field.fieldtype)
				|| field.fieldname === "attachment_kind" ? field.read_only
				: field.fieldname === "printed_total" ? !p.printed_editable(state.doc, state.editable) : !editable || field.read_only,
			onchange: async function() { await field.onchange?.call(this); if (editable && !field.read_only) change(); },
			...(field.fieldtype === "Currency" ? {options: "currency", formatter: value => p.money(value)} : {})}));
	};
})();
