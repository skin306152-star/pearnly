/* 收款（11-11）：收款员在 LINE / CC 录的收款与写进 Express（RE）的结果。只看、导出、看凭证；
 * 网页上不新建、不改、不删（录入只在 LINE 与 CC，写错由财务在 Express 原生作废）。
 *
 * 列表是共用的原生列表（`desk/list.js`）：服务端分页、白名单排序、原生筛选（`collection.records`）；
 * 点一行开只读弹窗：各张发票收多少、各方式与凭证（共用原件查看器 `desk/viewer.js`）。 */
(() => {
	const p = frappe.pearnly;
	p.collections = async (page, t) => {
		const api = "pearnly_erp_portal.collection.api.";
		const root = "pearnly-collections";
		const status_label = value => (value ? t(`erp.collection.status.${value}`) : "");
		const source_label = value => (value ? t(`erp.collection.source.${value}`) : "");
		const money = value => p.money(value);
		let list;
		page.set_title(__("erp.nav.collections"));
		p.breadcrumbs({label: __("erp.nav.collections"), route: p.subroute(root)});
		const search = page.add_field({fieldname: "q", label: __("Search"), fieldtype: "Data",
			change: () => void list?.reload()});
		const parameters = () => {
			const picked = p.filter_values(list);
			const [from, to] = picked.doc_date || [];
			return {q: search.get_value() || undefined, date_from: from || undefined, date_to: to || undefined,
				status: picked.status || undefined, source: picked.source || undefined,
				conditions: p.filter_conditions(list)};
		};

		const viewer_load = (row, number) => (/\.pdf$/i.test(row.file_name || "")
			? p.call(api + "api_collection_voucher_page", {file: row.file, page: number})
			: Promise.resolve({image: row.file_url, pages: 1}));

		async function open(row) {
			const data = await p.call(api + "api_collection", {name: row.name});
			const shown = (fieldname, label, value, fieldtype = "Data") => ({fieldtype, fieldname, label, read_only: 1,
				default: value ?? ""});
			const vouchers = (data.methods || []).filter(item => item.file);
			const dialog = new frappe.ui.Dialog({title: data.name, size: "large", fields: [
				shown("customer_name", __("Customer Name"), data.customer_name),
				shown("customer", __("Customer Code"), data.customer),
				shown("doc_date", __("Collection Date"), data.doc_date, "Date"),
				shown("collector", __("Collector"), data.collector_name),
				shown("source", __("Source"), source_label(data.source)),
				{fieldtype: "Column Break"},
				shown("status", __("Status"), status_label(data.status)),
				shown("docnum", __("Express No."), data.docnum),
				shown("book", __("Account Set"), data.book),
				shown("total", __("Total"), money(data.total)),
				...(data.reason ? [shown("reason", __("Reason"), data.reason, "Small Text")] : []),
				{fieldtype: "Section Break"},
				{fieldtype: "Table", fieldname: "invoices", label: __("Invoices"), cannot_add_rows: true,
					cannot_delete_rows: true, in_place_edit: false, read_only: 1, data: data.invoices || [], fields: [
						{fieldtype: "Data", fieldname: "docnum", label: __("Invoice No"), in_list_view: 1, read_only: 1},
						{fieldtype: "Date", fieldname: "invoice_date", label: __("Invoice Date"), in_list_view: 1, read_only: 1},
						{fieldtype: "Currency", fieldname: "remaining", label: __("Outstanding Amount"), in_list_view: 1, read_only: 1},
						{fieldtype: "Currency", fieldname: "amount", label: __("Amount"), in_list_view: 1, read_only: 1},
					]},
				shown("cash", __("Cash"), money(data.cash)),
				{fieldtype: "Table", fieldname: "methods", label: __("Payments"), cannot_add_rows: true,
					cannot_delete_rows: true, in_place_edit: false, read_only: 1, data: data.methods || [], fields: [
						{fieldtype: "Data", fieldname: "prefix", label: __("Prefix"), in_list_view: 1, read_only: 1},
						{fieldtype: "Data", fieldname: "name", label: __("Collection Method"), in_list_view: 1, read_only: 1},
						{fieldtype: "Currency", fieldname: "amount", label: __("Amount"), in_list_view: 1, read_only: 1},
					]},
				...(vouchers.length ? [{fieldtype: "Section Break", label: __("Attachments")},
					...vouchers.map((item, index) => ({fieldtype: "Button", fieldname: `voucher_${index}`,
						label: item.name || item.prefix, click: () => void viewer.show(item.file)})),
					{fieldtype: "HTML", fieldname: "voucher_preview"}] : []),
			]});
			const viewer = vouchers.length
				? p.document_viewer(dialog.fields_dict.voucher_preview.$wrapper, {load: viewer_load}) : null;
			dialog.$wrapper.addClass("pearnly-dialog");
			dialog.onhide = () => viewer?.destroy();
			dialog.show();
			if (viewer) void viewer.show(vouchers[0].file);
		}

		list = p.list({page, doctype: "ERP Collection", compact_columns: true,
			// 本页支持的筛选条件（官方的字段下拉里只列这三个）
			filter_fields: [
				{fieldname: "doc_date", label: __("Collection Date"), fieldtype: "Date"},
				{fieldname: "status", label: __("Status"), fieldtype: "Select"},
				{fieldname: "source", label: __("Source"), fieldtype: "Select"},
			],
			on_open: row => void open(row),
			columns: [
				{fieldname: "doc_date", width: 100, min_width: 84, label: __("Collection Date")},
				{fieldname: "collector", width: 110, min_width: 80, label: __("Collector"),
					format: (value, row) => row.collector_name || value || ""},
				{fieldname: "customer_name", width: 150, min_width: 96, label: __("Customer Name"),
					format: (value, row) => value || row.customer || ""},
				{fieldname: "invoice_summary", width: 190, min_width: 110, label: __("Invoices")},
				{fieldname: "cash", width: 90, min_width: 66, label: __("Cash"),
					format: value => (Number(value) ? money(value) : "")},
				{fieldname: "transfer", width: 90, min_width: 66, label: __("Bank Transfer"),
					format: value => (Number(value) ? money(value) : "")},
				{fieldname: "method_summary", width: 130, min_width: 80, label: __("Collection Method")},
				{fieldname: "discount", width: 80, min_width: 60, label: __("Discount"),
					format: value => (Number(value) ? money(value) : "")},
				{fieldname: "voucher_count", width: 70, min_width: 52, label: __("Attachments"),
					format: value => (Number(value) ? String(value) : "")},
				// 写入结果：写进去了显示 RE 号，否则显示状态（失败的原因在点开的弹窗里）
				{fieldname: "docnum", width: 110, min_width: 76, label: __("Express"),
					format: (value, row) => value || status_label(row.status),
					cell_class: (value, row) => (row.status === "failed" ? "pearnly-payment-due" : "")},
			],
			// 列头点一下排序（服务端白名单 `collection.records.SORT_FIELDS`）
			sort_fields: ["doc_date", "collector", "customer_name", "cash", "transfer", "discount", "docnum"],
			load: ({start, page_length, sort_by, sort_order}) => p.call(api + "api_collections",
				{params: parameters(), start, limit: page_length, sort_by, sort_order}),
		});
		// 小的当场下载，大的排后台、做完推送后下载（`p.export_file`）
		page.add_menu_item(__("Export"), () => void p.export_file(`${api}api_collections_export`,
			{params: parameters()}));
		await list.ready;
	};
})();
