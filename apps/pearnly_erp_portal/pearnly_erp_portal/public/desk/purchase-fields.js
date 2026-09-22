/* Purchase business fields composed only from native Frappe controls. */
(() => {
	const p = frappe.pearnly, u = p.purchase_ui = {};
	u.api = "pearnly_erp_portal.purchasing.api.";
	u.call = (method, args = {}, write = false) => p.call(u.api + method, args, write ? "POST" : "GET");
	u.labels = {
		purchase_invoice: __("Purchase Invoice"), purchase_order: __("Purchase Order"), expense: __("Expense"),
		goods: __("Goods"), service: __("Service"), tax_invoice: __("Tax Invoice"),
		simple_tax_receipt: __("Abbreviated Tax Invoice"), receipt: __("Receipt"),
		substitute_receipt: __("Substitute Receipt"), other: __("Other"),
		head_office: __("Head Office"), head: __("Head Office"), branch: __("Branch"), none: __("None"),
		exclusive: __("Tax Exclusive"), inclusive: __("Tax Inclusive"),
		cash: __("Cash"), transfer: __("Bank Transfer"), promptpay: __("PromptPay"), bank: __("Bank"), cheque: __("Cheque"),
		draft: __("Draft"), submitted: __("Submitted"), void: __("Cancelled"),
		unpaid: __("Unpaid"), partial: __("Partly Paid"), paid: __("Paid"),
		bill: __("Invoice"), payment_proof: __("Payment Receipt"), wht_cert: __("Withholding Tax Certificate"),
		web: __("Manual"), manual: __("Manual"), ocr: __("OCR"), line: __("LINE"),
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
	u.line_keys = ["item_type", "product", "description", "code", "barcode", "qty", "unit", "unit_price",
		"discount", "vat_rate", "wht_rate", "category", "subcategory", "batch_no", "expiry_date", "warehouse", "location"];
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
					await line.form.set_values({description: item.product_name, code: item.code, barcode: item.barcode,
						unit: item.base_unit || item.unit, unit_price: item.default_cost || 0,
						vat_rate: item.vat_applicable ? state.defaults.settings.default_vat_rate : 0,
						warehouse: item.default_warehouse || "", location: item.default_location || ""});
				}}),
			f("description", "Item Name", "Data", {reqd: 1}),
			f("qty", "Quantity", "Float", {reqd: 1, default: 1, precision: 6}),
			f("unit", "UOM"), f("unit_price", "Rate", "Currency", {default: 0}), col(),
			f("discount", "Discount Amount", "Currency", {default: 0}),
			f("vat_rate", "VAT Rate", "Percent", {default: state.defaults.settings.default_vat_rate}),
			f("wht_rate", "Withholding Tax Rate", "Percent", {default: 0}),
			f("line_total", "Amount", "Currency", {read_only: 1}),
			f("vat_amount", "VAT", "Currency", {read_only: 1}),
			f("wht_amount", "Withholding Tax", "Currency", {read_only: 1, depends_on: "eval:doc.wht_rate > 0"}),
			u.section("Additional Information", {collapsible: 1, collapsed: 1}),
			f("item_type", "Item Type", "Select", {options: u.options(["goods", "service"]), default: "goods"}),
			f("code", "Item Code"), f("barcode", "Barcode"),
			f("category", "Category", "Link", {options: "ERP Purchase Category", get_query: () => ({filters: {parent_category: ["is", "not set"], is_active: 1}}),
				onchange: () => line.ready && line.form.set_value("subcategory", "")}),
			f("subcategory", "Subcategory", "Link", {options: "ERP Purchase Category",
				get_query: () => ({filters: {parent_category: line.form?.get_value("category") || "__none__", is_active: 1}})}), col(),
			f("warehouse", "Warehouse"), f("location", "Location"), f("batch_no", "Batch No"), f("expiry_date", "Expiry Date", "Date"),
			...(state.editable ? [u.section(""), f("remove", "Delete", "Button", {click: () => state.remove_line(line)})] : []),
		].map(field => ({...field,
			read_only: ["Button", "Section Break", "Column Break"].includes(field.fieldtype) ? field.read_only : !state.editable || field.read_only,
			onchange: async function() {await field.onchange?.call(this); if (!field.read_only) change();},
			...(field.fieldtype === "Currency" ? {formatter: value => p.money(value, state.form?.get_value("currency") || state.defaults.settings.base_currency)} : {}),
		}));
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
			f("doc_date", "Posting Date", "Date", {reqd: 1}),
			f("doc_no", "Supplier Invoice No"),
			f("doc_type", "Document Type", "Select", {options: u.options(["tax_invoice", "simple_tax_receipt", "receipt", "purchase_order", "substitute_receipt", "other"])}), col(),
			f("doc_kind", "Type", "Select", {options: u.options(["purchase_invoice", "purchase_order", "expense", ...(doc.doc_kind === "goods" || doc.doc_kind === "service" ? [doc.doc_kind] : [])]), reqd: 1}),
			f("is_paid", "Is Paid", "Check", {hidden: !editable}),
			f("payment_status", "Payment Status", "Select", {hidden: editable, read_only: 1, options: u.options(["unpaid", "partial", "paid"])}),
			f("payment_method", "Mode of Payment", "Select", {options: u.options(["cash", "transfer", "promptpay", "bank", "cheque", "other"]), depends_on: "eval:doc.is_paid"}),
			f("due_date", "Due Date", "Date", {depends_on: "eval:!doc.is_paid"}),
			u.section("Supplier"),
			f("supplier", "Supplier", "Link", {options: "ERP Supplier", hidden: !editable, get_query: () => ({filters: {is_active: 1}}),
				async onchange() {
					const name = state.form?.get_value("supplier"); if (!name || !editable || !state.ready) return;
					const {message: row} = await frappe.db.get_value("ERP Supplier", name,
						["supplier_name", "tax_id", "branch_type", "branch_no", "branch_name", "address"]);
					if (name !== state.form.get_value("supplier") || state.disposed) return;
					await state.form.set_values({supplier_name: row.supplier_name, supplier_tax_id: row.tax_id,
						supplier_branch_type: row.branch_type === "head" ? "head_office" : row.branch_type || "none",
						supplier_branch_no: row.branch_no, supplier_branch_name: row.branch_name, supplier_address: row.address});
				}}),
			f("supplier_name", "Supplier Name", "Data", {reqd: 1}),
			f("supplier_tax_id", "Tax ID"), f("supplier_address", "Address", "Small Text"), col(),
			f("supplier_branch_type", "Branch Type", "Select", {options: u.options(["head_office", "branch", "none"])}),
			f("supplier_branch_no", "Branch No", "Data", {depends_on: "eval:doc.supplier_branch_type === 'branch'"}),
			f("supplier_branch_name", "Branch Name", "Data", {depends_on: "eval:doc.supplier_branch_type === 'branch'"}),
			...(doc.name && !editable ? [u.section("Payments", {collapsible: 1, collapsed: 1}),
				f("paid_amount", "Paid Amount", "Currency", {read_only: 1}), f("outstanding", "Outstanding Amount", "Currency", {read_only: 1}),
				f("payments", "Payments", "Table", {read_only: 1, cannot_add_rows: 1, cannot_delete_rows: 1,
					data: doc.payments || [], fields: [f("payment_date", "Date", "Date", {in_list_view: 1, columns: 2}),
						f("amount", "Paid Amount", "Currency", {in_list_view: 1, columns: 2, formatter: value => p.money(value, doc.currency)}),
						f("method", "Mode of Payment", "Select", {options: u.options(["cash", "transfer", "promptpay", "bank", "cheque", "other"]), in_list_view: 1, columns: 2}),
						f("note", "Remarks", "Data", {in_list_view: 1, columns: 4}), f("is_reversal", "Reversal", "Check"), f("reference", "Reference")]}),
			] : []),
			u.section("Items", {fieldname: "items_section"}),
			f("currency", "Currency", "Select", {options: "THB\nUSD\nEUR\nCNY\nJPY\nSGD", reqd: 1}), col(),
			f("fx_rate", "Exchange Rate", "Float", {reqd: 1, precision: 6, depends_on: `eval:doc.currency !== '${state.defaults.settings.base_currency}'`}),
			f("price_mode", "Prices Include Tax", "Select", {options: u.options(["exclusive", "inclusive"])}),
			u.section(""), f("lines", "Items", "HTML"),
			f("add_line", "Add row", "Button", {hidden: !editable, click: () => state.add_line?.()}),
			f("merge_lines", "Merge Items", "Button", {hidden: 1, click: () => state.merge?.()}),
			u.section("Taxes and Charges"),
			f("has_vat", "Tax Invoice", "Check", {hidden: !editable}),
			f("discount_total", "Discount Amount", "Currency"), f("rounding", "Rounding Adjustment", "Currency"), col(),
			...[ ["subtotal", "Net Total"], ["vat_amount", "VAT"], ["wht_amount", "Withholding Tax"],
				["grand_total", "Grand Total"], ["net_payable", "Amount Payable"]].map(([key, label]) => f(key, label, "Currency", {read_only: 1})),
			u.section("Invoice Amount", {collapsible: 1, collapsed: 1, hidden: !editable && !doc.amount_override}),
			f("amount_override", "Manually Set Amounts", "Check", {hidden: !editable, onchange: async () => {
				if (state.ready && state.form.get_value("amount_override")) await state.form.set_values({
					override_subtotal: state.form.get_value("subtotal"), override_discount: state.form.get_value("discount_total"),
					override_vat: state.form.get_value("vat_amount"), override_grand: state.form.get_value("grand_total")});
			}}),
			f("override_subtotal", "Net Total", "Currency", {depends_on: "eval:doc.amount_override"}),
			f("override_discount", "Discount Amount", "Currency", {depends_on: "eval:doc.amount_override"}), col(),
			f("override_vat", "VAT", "Currency", {depends_on: "eval:doc.amount_override"}),
			f("override_grand", "Grand Total", "Currency", {depends_on: "eval:doc.amount_override"}),
			u.section(""), f("notes", "Remarks", "Small Text"),
			...(doc.void_reason ? [f("void_reason", "Reason for Cancellation", "Small Text", {read_only: 1})] : []),
		].map(field => ({...field,
			read_only: ["Button", "HTML", "Tab Break", "Section Break", "Column Break"].includes(field.fieldtype)
				|| field.fieldname === "attachment_kind" ? field.read_only : !editable || field.read_only,
			onchange: async function() { await field.onchange?.call(this); if (editable && !field.read_only) change(); },
			...(field.fieldtype === "Currency" ? {options: "currency", formatter: value => p.money(value, state.form?.get_value("currency") || doc.currency || state.defaults.settings.base_currency)} : {}),
		}));
	};
})();
