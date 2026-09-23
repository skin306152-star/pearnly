/* Purchase records: the same native page list and toolbar as the accepted inventory pages. */
(() => {
	const p = frappe.pearnly, u = p.purchase_ui;
	p.purchase = async (page, t, wrapper) => {
		const root = "pearnly-purchase", can_write = frappe.model.can_write("Purchase Document");
		const go = (...parts) => frappe.set_route(root, ...parts);
		let cleanup = () => {}, list_cleanup = () => {};
		const title = () => __("Purchase Records");
		async function listing(still_current) {
			page.set_title(title());
			p.breadcrumbs({label: title(), route: p.subroute(root)});
			const defaults = await u.call("api_purchase_defaults"); if (!still_current()) return;
			let live = true, list, selected = [];
			const search = page.add_field({fieldname: "q", label: __("Search"), fieldtype: "Data",
				change: () => void list?.reload()});
			// 筛选是**官方原生**那套（那颗 Filter 按钮 + 弹层里的条件行），字段在下面 `filters`
			// 里声明；这里把条件翻成接口参数（与服务端的参数名一一对应）。
			const parameters = () => {
				const picked = p.filter_values(list);
				const [from, to] = picked.doc_date || [];
				return {q: search.get_value() || undefined,
					date_basis: "document",
					date_from: from || undefined, date_to: to || undefined,
					doc_kind: picked.doc_kind || undefined,
					payment_status: picked.payment_status || undefined,
					supplier: picked.supplier || undefined,
					status: picked.status || undefined};
			};
			const primary = () => {
				if (!live || !still_current()) return;
				if (!can_write) {page.clear_primary_action(); return;}
				if (!selected.length) {page.set_primary_action(__("New"), () => {void go("new");}); return;}
				if (selected.some(row => row.status !== "draft")) {page.clear_primary_action(); return;}
				page.set_primary_action(`${__("Delete")} (${selected.length})`, () => frappe.confirm(__("Are you sure you want to delete?"), async () => {
					await u.call("api_delete_purchase_drafts", {names: selected.map(row => row.name)}, true);
					// 主按动作要有回音：提示 + 提示音。删掉的和"保存成功"不是同一声 —— 官方
					// 批量删除放的是 `delete`（框架 `bulk_operations.js` 也是按动作名给音）。
					p.success(__("Deleted {0}", [selected.length]), "delete");
					await refresh();
				}));
			};
			list = p.list({page, doctype: "Purchase Document", selectable: true, compact_columns: true,
				// 本页支持的筛选条件（官方的字段下拉里只列这五个）。
				filter_fields: [
					{fieldname: "doc_date", label: __("Posting Date"), fieldtype: "Date"},
					{fieldname: "doc_kind", label: __("Type"), fieldtype: "Select"},
					{fieldname: "payment_status", label: __("Payment Status"), fieldtype: "Select"},
					{fieldname: "supplier", label: __("Supplier"), fieldtype: "Link"},
					{fieldname: "status", label: __("Status"), fieldtype: "Select"},
				],
				on_open: row => go(row.name),
				columns: [
					// 底线要能塞进最窄的常见窗口：否则最右那两列会被右边那块（计数 /「付款」）
					// 盖住 —— 用户 2026-09-23 报的"状态和内容对不上"。
					{fieldname: "system_no", width: 154, min_width: 128, label: __("System number"), format: (value, row) => value || row.name},
					{fieldname: "doc_date", width: 106, min_width: 88, label: __("Posting Date")},
					{fieldname: "supplier_name", width: 160, min_width: 96, label: __("Supplier")},
					{fieldname: "summary", width: 180, min_width: 96, label: __("Description")},
					{fieldname: "grand_total", width: 108, min_width: 82, label: __("Grand Total"), format: (value, row) => p.money(value, row.currency)},
					{fieldname: "doc_kind", width: 90, min_width: 66, label: __("Type"), format: u.label},
					// 还没付出去的钱标红（颜色走 `cell_class`，列表这一层只出转义文本）。
					{fieldname: "payment_status", width: 85, min_width: 62, label: __("Payment Status"), format: u.label,
						cell_class: value => value === "paid" ? "" : "pearnly-payment-due"},
					{fieldname: "status", width: 72, min_width: 52, label: __("Status"), format: u.label},
				],
				load: ({start, page_length}) => u.call("api_purchase_records", {params: parameters(), start, limit: page_length}),
				on_selection: rows => {selected = rows; primary();},
				// 行内付款和销售同一套：开弹窗、能看到金额、能改方式与日期（用户 2026-09-23）。
				// 已经付清的单据不再提供入口（用户 2026-09-23 口径「已付不可更改」）。
				row_actions: can_write ? row => row.status !== "submitted" || row.payment_status === "paid" ? []
					: [{id: "payment", label: __("Payment"), action: () => u.payment(row, refresh)}] : null,
				empty_action: can_write ? {label: __("New"), action: () => go("new")} : null,
			});
			async function refresh() {await list.reload(); primary();}
			page.add_menu_item(__("Export"), () => {
				const query = new URLSearchParams({params: JSON.stringify(parameters())});
				window.open(`/api/method/${u.api}api_purchase_export?${query}`, "_blank", "noopener,noreferrer");
			});
			if (can_write && defaults.capabilities.line_intakes) page.add_inner_button(__("LINE"), () => go("intakes"));
			p.add_refresh(page, refresh);
			primary(); await list.ready;
			list_cleanup = () => {live = false; list.destroy();};
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
			page.set_title(is_new ? __("New") : amended_from ? __("Amend") : doc.system_no || doc.name);
			p.breadcrumbs({label: title(), route: p.subroute(root), trail: [{label: page.title, route: ""}]});
			if (amended_from) doc = {...doc, system_no: defaults.system_no, attachments: doc.attachments.filter(row => row.kind === "bill")};
			cleanup = await u.editor({page, doc, defaults, editable, amended_from, intake: incoming?.intake, still_current, reload: () => show(), go});
		}});
		await show();
	};
})();
