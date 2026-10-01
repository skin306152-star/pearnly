/* Sales records: the same native page list and toolbar as the accepted inventory pages. */
(() => {
	const p = frappe.pearnly, u = p.sales_ui;
	p.sales = async (page, t, wrapper) => {
		const root = "pearnly-sales", can_write = frappe.model.can_write("ERP Sales Document");
		const go = (...parts) => frappe.set_route(root, ...parts);
		let cleanup = () => {}, list_cleanup = () => {};
		// 收银小票查看器要 `trade.*` 那套文案（收银页的命名空间），第一次点开时取一次。
		let trade_texts = null;
		const title = () => __("nav.sales");
		async function listing(still_current) {
			page.set_title(title());
			p.breadcrumbs({label: title(), route: p.subroute(root)});
			const defaults = await u.call("api_sales_defaults"); if (!still_current()) return;
			let live = true, list, selected = [];
			const search = page.add_field({fieldname: "q", label: __("Search"), fieldtype: "Data",
				change: () => void list?.reload()});
			// 筛选是**官方原生**那套（那颗 Filter 按钮 + 弹层里的条件行），本页只开放下面
			// `filters` 里声明的四个字段。这里把条件翻成接口参数（服务端的名字与条件一一对应）。
			const parameters = () => {
				const picked = p.filter_values(list);
				const [from, to] = picked.doc_date || [];
				return {q: search.get_value() || undefined,
					date_basis: "document",
					date_from: from || undefined, date_to: to || undefined,
					doc_kind: picked.doc_kind || undefined,
					payment_status: picked.payment_status || undefined,
					source: picked.source || undefined};
			};
			const primary = () => {
				if (!live || !still_current()) return;
				if (!can_write) {page.clear_primary_action(); return;}
				if (!selected.length) {page.set_primary_action(__("New"), () => {void go("new");}); return;}
				if (selected.some(row => row.status !== "draft")) {page.clear_primary_action(); return;}
				page.set_primary_action(`${__("Delete")} (${selected.length})`, () => frappe.confirm(__("Are you sure you want to delete?"), async () => {
					await u.call("api_delete_sale_drafts", {names: selected.map(row => row.name)}, true);
					// 主按动作要有回音：提示 + 提示音。删掉的和"保存成功"不是同一声 —— 官方
					// 批量删除放的是 `delete`（框架 `bulk_operations.js` 也是按动作名给音）。
					p.success(__("Deleted {0}", [selected.length]), "delete");
					await refresh();
				}));
			};
			const refresh = async () => {await list.reload(); primary();};
			list = p.list({page, doctype: "ERP Sales Document", selectable: true, compact_columns: true,
				// 本页支持的筛选条件（官方的字段下拉里只列这四个）。
				filter_fields: [
					{fieldname: "doc_date", label: __("Date"), fieldtype: "Date"},
					{fieldname: "doc_kind", label: __("Document Type"), fieldtype: "Select"},
					{fieldname: "payment_status", label: __("Collection Status"), fieldtype: "Select"},
					{fieldname: "source", label: __("Source"), fieldtype: "Select"},
				],
				// 收银小票（来源=POS）在收银那张单上，不能当销售单打开去看编辑页 ——
				// 点它开小票查看器（与收银页同一个），其余照旧进详情页。
				on_open: async row => {
					if (row.source !== "pos") { go(row.name); return; }
					// 收银小票的文案在 `trade.*`（收银页那套），懒取一次就留着。
					trade_texts = trade_texts || await p.texts("trade.");
					await p.trade.receipt_view(row.pos_name || row.name, trade_texts, {});
				},
				columns: [
					// `width` 是"窗口宽时按这个比例分"，`min_width` 是**不能再压的底线**：九列的
					// 底线加起来要能塞进最窄的常见窗口，否则最右那两列会被右边那块（计数）盖住
					// —— 用户 2026-09-23 报的"列和内容位置对不上，歪了"。
					{fieldname: "system_no", width: 154, min_width: 128, label: __("System number"), format: (value, row) => value || row.name},
					{fieldname: "doc_date", width: 106, min_width: 88, label: __("Date")},
					{fieldname: "buyer_name", width: 160, min_width: 96, label: __("Customer Name")},
					{fieldname: "summary", width: 180, min_width: 96, label: __("Description")},
					{fieldname: "grand_total", width: 108, min_width: 82, label: __("Grand Total"), format: value => p.money(value)},
					{fieldname: "doc_kind", width: 90, min_width: 66, label: __("Document Type"), format: u.label},
					// 「来源」：手工录入 / 上传识别 / LINE / 收银。收银小票就靠这一列认出来
					// （对齐 ERPNext 的 is_created_using_pos 标记）。
					{fieldname: "source", width: 96, min_width: 68, label: __("Source"), format: u.label},
					// 还没收到的钱标红（颜色走 `cell_class`，列表这一层只出转义文本）。
					{fieldname: "payment_status", width: 85, min_width: 62, label: __("Collection Status"), format: u.label,
						cell_class: value => value === "paid" ? "" : "pearnly-payment-due"},
					{fieldname: "status", width: 72, min_width: 52, label: __("Status"), format: u.label},
				],
				// 列头点一下排序（`sort_fields` 声明服务端认的字段，见 core `p.list`）。
				sort_fields: ["system_no", "doc_date", "buyer_name", "grand_total",
					"doc_kind", "source", "payment_status", "status", "creation"],
				load: ({start, page_length, sort_by, sort_order}) => u.call("api_sales_records",
					{params: parameters(), start, limit: page_length, sort_by, sort_order}),
				on_selection: rows => {selected = rows; primary();},
				// 收款只在**还欠钱**时给动作：已经收满的单据不再提供入口（用户 2026-09-23
				// 口径「已收不可更改」）。按钮说的是动作（收款），不是状态。
				row_actions: can_write ? row => row.status !== "submitted" || row.doc_kind !== "receipt"
					|| row.payment_status === "paid" ? []
					: [{id: "payment", label: __("Collection"), action: () => u.payment(row, refresh)}] : null,
				empty_action: can_write ? {label: __("New"), action: () => go("new")} : null,
			});
			page.add_menu_item(__("Export"), () => {
				const query = new URLSearchParams({params: JSON.stringify(parameters())});
				window.open(`/api/method/${u.api}api_sales_export?${query}`, "_blank", "noopener,noreferrer");
			});
			p.add_refresh(page, refresh);
			primary(); await list.ready;
			list_cleanup = () => {live = false; list.destroy();};
		}
		const show = p.routed_page({wrapper, root, render: async (rest, still_current) => {
			cleanup(); list_cleanup(); cleanup = list_cleanup = () => {};
			p.clear_page(page); page.clear_menu();
			if (!rest.length) {await listing(still_current); return;}
			// 从 LINE 卡片「编辑」进来（`line/<记录>`）：识别结果与 LINE 里的选择已填好；
			// 已入账就转去那张单的查看页；按 LINE 里选的公司打开（切了公司就整页重载）。
			const from_line = rest[0] === "line" && rest.length === 2 ? rest[1] : undefined;
			const opened = from_line ? await p.call("pearnly_erp_portal.integrations.api.api_open_line_intake", {name: from_line}, "POST") : null;
			if (!still_current()) return;
			if (opened?.recorded) {await go(opened.recorded); return;}
			if (opened?.switched_company) {window.location.reload(); return;}
			const is_new = rest[0] === "new" || Boolean(opened);
			if (!is_new && rest.length > 1) {await go(); return;}
			const defaults = await u.call("api_sales_defaults");
			const doc = opened ? opened.doc : is_new ? {} : await u.call("api_sales_document", {name: rest[0]});
			if (!still_current()) return;
			const editable = can_write && (is_new || doc.status === "draft");
			page.set_title(is_new ? __("trade.newSale") : doc.system_no || doc.name);
			p.breadcrumbs({label: title(), route: p.subroute(root), trail: [{label: page.title, route: ""}]});
			cleanup = await u.editor({page, doc, defaults, editable, intake: opened?.intake, still_current, reload: () => show(), go});
		}});
		await show();
	};
})();
