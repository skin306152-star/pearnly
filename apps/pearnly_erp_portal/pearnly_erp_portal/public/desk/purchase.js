/* Purchase record routing, native filters, monthly lists and summary fields. */
(() => {
	const p = frappe.pearnly, u = p.purchase_ui;
	p.purchase = async (page, t, wrapper) => {
		const root = "pearnly-purchase", can_write = frappe.model.can_write("Purchase Document");
		const go = (...parts) => frappe.set_route(root, ...parts);
		let cleanup = () => {}, list_cleanup = () => {}, filter_values = {}, selected_columns;
		const title = () => __("Purchase Records");
		const all_columns = [
			{fieldname: "system_no", label: __("ID"), format: (value, row) => value || row.name},
			{fieldname: "doc_date", label: __("Posting Date")},
			{fieldname: "supplier_name", label: __("Supplier")},
			{fieldname: "doc_no", label: __("Supplier Invoice No")},
			{fieldname: "summary", label: __("Description")},
			{fieldname: "doc_kind", label: __("Type"), format: u.label},
			{fieldname: "doc_type", label: __("Document Type"), format: u.label},
			{fieldname: "categories", label: __("Category")},
			{fieldname: "source", label: __("Source"), format: u.label},
			{fieldname: "status", label: __("Status"), format: u.label},
			{fieldname: "grand_total", label: __("Grand Total"), format: (value, row) => p.money(value, row.currency)},
			{fieldname: "vat_amount", label: __("VAT"), format: (value, row) => p.money(value, row.currency)},
			{fieldname: "payment_status", label: __("Payment Status"), format: u.label},
			{fieldname: "attachment_count", label: __("Attachments")},
		];
		selected_columns = ["system_no", "doc_date", "supplier_name", "summary", "grand_total", "payment_status", "attachment_count"];
		async function listing(still_current) {
			page.set_title(title());
			p.breadcrumbs({label: title(), route: p.subroute(root)});
			const defaults = await u.call("api_purchase_defaults"); if (!still_current()) return;
			const f = u.field, col = u.column, categories = defaults.categories;
			let live = true, revision = 0, ready = false, lists = [], month_form, selected = new Map();
			let form;
			const options = (values, text) => values.map(value => ({value, label: text(value)}));
			const multi = (name, label, values) => f(name, label, "MultiSelectList", {get_data: text => values
				.filter(row => row.label.toLowerCase().includes((text || "").toLowerCase()))
				.map(row => ({value: row.value, label: row.label, description: ""}))});
			const changed = frappe.utils.debounce(() => {if (ready) void refresh();}, 350);
			const dates = async () => {
				if (!ready) return;
				const today = frappe.datetime.get_today(), mode = form.get_value("period");
				let start = "", end = "";
				if (mode === "this_month") { start = moment(today).startOf("month").format("YYYY-MM-DD"); end = moment(today).endOf("month").format("YYYY-MM-DD"); }
				if (mode === "last_month") {const day = frappe.datetime.add_months(today, -1); start = moment(day).startOf("month").format("YYYY-MM-DD"); end = moment(day).endOf("month").format("YYYY-MM-DD");}
				if (mode === "this_quarter") {
					const month = Math.floor((Number(today.slice(5, 7)) - 1) / 3) * 3 + 1;
					start = `${today.slice(0, 4)}-${String(month).padStart(2, "0")}-01`;
					end = frappe.datetime.add_days(frappe.datetime.add_months(start, 3), -1);
				}
				if (mode !== "custom") await form.set_values({date_from: start, date_to: end});
			};
			form = p.layout(page.main, [
				u.section("Filters"),
				f("group", "Type", "Select", {options: [{value: "", label: __("All")}, {value: "purchase", label: __("Purchase")},
					{value: "expense", label: __("Expense")}, {value: "unpaid", label: __("Unpaid")}]}),
				f("q", "Search"), col(),
				f("period", "Period", "Select", {options: [{value: "all", label: __("All")},
					{value: "this_month", label: __("This Month")}, {value: "last_month", label: __("Last Month")},
					{value: "this_quarter", label: __("This Quarter")}, {value: "custom", label: __("Custom")}], onchange: dates}),
				f("date_basis", "Date", "Select", {options: [{value: "document", label: __("Posting Date")}, {value: "upload", label: __("Creation")}]}), col(),
				f("date_from", "From Date", "Date"), f("date_to", "To Date", "Date"),
				u.section("More Filters", {collapsible: 1, collapsed: 1}),
				multi("doc_type", "Document Type", options(["tax_invoice", "simple_tax_receipt", "receipt", "purchase_order", "substitute_receipt", "other"], u.label)), col(),
				multi("payment_status", "Payment Status", options(["unpaid", "partial", "paid"], u.label)), col(),
				multi("category", "Category", categories.filter(row => !row.parent_category).map(row => ({value: row.name, label: row.category_name}))),
				u.section("Summary", {collapsible: 1}),
				f("total_spent", "Total Amount", "Currency", {read_only: 1}),
				f("purchase_total", "Purchase Amount", "Currency", {read_only: 1}),
				f("purchase_count", "Purchase Count", "Int", {read_only: 1}), col(),
				f("expense_total", "Expense Amount", "Currency", {read_only: 1}),
				f("expense_count", "Expense Count", "Int", {read_only: 1}),
				f("vat_creditable", "Input Tax", "Currency", {read_only: 1}), col(),
				f("unpaid_total", "Outstanding Amount", "Currency", {read_only: 1}),
				f("unpaid_suppliers", "Suppliers", "Int", {read_only: 1}),
				f("unpaid", "Unpaid", "Button", {click: async () => { await form.set_value("group", "unpaid"); changed(); }}),
				u.section("Purchase Invoice"), f("records", "Purchase Invoice", "HTML"),
			].map(field => ({...field, ...(field.fieldtype === "Currency" ? {formatter: value => p.money(value, defaults.settings.base_currency)} : {}), onchange: async function() {await field.onchange?.call(this); if (!field.read_only) changed();}})));
			await form.set_values({group: "", period: "this_month", date_basis: "document",
				date_from: frappe.datetime.month_start(), date_to: frappe.datetime.month_end(), ...filter_values});
			const parameters = () => Object.fromEntries(["group", "q", "doc_type", "payment_status", "category", "date_basis", "date_from", "date_to"]
				.map(key => [key, form.get_value(key)]));
			const primary = () => {
				if (!live || !still_current()) return;
				if (!can_write) {page.clear_primary_action(); return;}
				if (!selected.size) {page.set_primary_action(__("New"), () => go("new")); return;}
				if ([...selected.values()].some(row => row.status !== "draft")) {page.clear_primary_action(); return;}
				page.set_primary_action(`${__("Delete")} (${selected.size})`, () => {
					if ([...selected.values()].some(row => row.status !== "draft")) {frappe.msgprint(__("Only Draft documents can be deleted")); return;}
					frappe.confirm(__("Are you sure you want to delete?"), async () => {
						await u.call("api_delete_purchase_drafts", {names: [...selected.keys()]}, true); await refresh();
					});
				});
			};
			async function refresh() {
				const request = ++revision, params = parameters();
				filter_values = {...params, period: form.get_value("period")};
				const data = await u.call("api_purchase_records", {params, limit: 1});
				if (!live || !still_current() || request !== revision) return;
				await form.set_values({...data, total_spent: Number(data.purchase_total) + Number(data.expense_total)});
				lists.forEach(list => list.destroy()); lists = []; selected.clear(); primary();
				const host = form.fields_dict.records.$wrapper; host.empty();
				const months = data.months.length ? data.months : [{month: "", count: 0, amount: 0}];
				month_form = p.layout(host, months.flatMap((month, i) => [
					u.section(month.month ? `${month.month} · ${month.count} · ${p.money(month.amount, defaults.settings.base_currency)}` : "Purchase Invoice", {collapsible: 1}),
					f("month_" + i, "Purchase Invoice", "HTML"),
				]));
				months.forEach((month, i) => {
					const list = p.list({page, container: month_form.fields_dict["month_" + i].$wrapper,
						doctype: "Purchase Document", selectable: can_write && month.draft_count > 0,
						on_open: row => go(row.name), columns: all_columns.filter(column => selected_columns.includes(column.fieldname)),
						load: async ({start, page_length}) => u.call("api_purchase_records", {params: {...params, month: month.month}, start, limit: page_length}),
						on_selection: rows => {
							list.data.forEach(row => selected.delete(row.name)); rows.forEach(row => selected.set(row.name, row)); primary();
						},
						row_actions: can_write ? row => row.status !== "submitted" ? [] : [{id: "payment", label: u.label(row.payment_status === "paid" ? "unpaid" : "paid"),
							action: async () => {await u.call("api_purchase_payment", {name: row.name, payload: {paid: row.payment_status === "paid" ? 0 : 1}}, true); await refresh();}}] : null,
						empty_action: can_write ? {label: __("New"), action: () => go("new")} : null,
					});
					lists.push(list);
				});
				await Promise.all(lists.map(list => list.ready));
			}
			page.add_inner_button(__("Export"), () => {
				const query = new URLSearchParams({params: JSON.stringify(parameters())});
				window.open(`/api/method/${u.api}api_purchase_export?${query}`, "_blank", "noopener,noreferrer");
			});
			page.add_inner_button(__("Columns"), () => p.dialog(__("Columns"), [f("columns", "Columns", "MultiCheck", {
				options: all_columns.map(column => ({value: column.fieldname, label: column.label, checked: selected_columns.includes(column.fieldname)})), columns: 2})],
				__("Apply"), async (values, dialog) => {selected_columns = ["system_no", ...(values.columns || []).filter(key => key !== "system_no")]; dialog.hide(); await refresh();}));
			if (can_write && defaults.capabilities.line_intakes) page.add_inner_button(__("LINE"), () => go("intakes"));
			p.add_refresh(page, refresh);
			ready = true; primary(); await refresh();
			list_cleanup = () => {live = false; lists.forEach(list => list.destroy());};
		}
		const show = p.routed_page({wrapper, root, render: async (rest, still_current) => {
			cleanup(); list_cleanup(); cleanup = list_cleanup = () => {};
			p.clear_page(page); page.clear_menu();
			if (!rest.length) {await listing(still_current); return;}
			if (rest[0] === "intakes") {cleanup = await u.intakes({page, go, still_current}); return;}
			const is_intake = rest[0] === "intake";
			const is_new = rest[0] === "new" || is_intake, amended_from = rest[0] === "amend" ? rest[1] : undefined;
			if ((!is_new && !amended_from && rest.length > 1) || (rest[0] === "amend" && !amended_from)) {await go(); return;}
			const defaults = await u.call("api_purchase_defaults");
			const incoming = is_intake ? await u.get_intake(rest[1]) : null;
			if (incoming?.intake.status === "confirmed") {await go(incoming.intake.purchase); return;}
			let doc = incoming?.doc || (is_new ? {} : await u.call("api_purchase_document", {name: amended_from || rest[0]}));
			if (!still_current()) return;
			const editable = can_write && (is_new || amended_from || doc.status === "draft");
			page.set_title(is_new ? __("New Purchase Invoice") : amended_from ? __("Amend") : doc.system_no || doc.name);
			p.breadcrumbs({label: title(), route: p.subroute(root), trail: [{label: page.title, route: ""}]});
			if (amended_from) doc = {...doc, system_no: defaults.system_no, attachments: doc.attachments.filter(row => row.kind === "bill")};
			cleanup = await u.editor({page, doc, defaults, editable, amended_from, intake: incoming?.intake, still_current, reload: () => show(), go});
		}});
		await show();
	};
})();
