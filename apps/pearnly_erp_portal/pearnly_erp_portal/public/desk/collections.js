/* 收款（11-11；用户 11-14 收成一页、一个权限）：本公司在 LINE / CC / 网页录的全部收款与写进 Express（RE）的结果，不分谁录的。
 * 页头「新建」（弹窗 `collection-new.js`，提交即放行）；点一行：草稿开原生弹窗改各张本次收、现金、各方式与折扣、销售员与部门，
 * 副按钮「撤回」；待写入 / 失败的看明细并可「撤回」（同 LINE【撤回】）；已写入的只看。改与撤回只认打开时的版本，别人刚改过回原因。
 * LINE 确认卡【编辑】打开 `pearnly-collections/<记录>`（旧地址 `pearnly-my-collections/<记录>` 转到这里）：那一笔已经不是草稿时
 * 先说「此卡片已失效」。导出、看凭证照旧（共用原件查看器 `desk/viewer.js`）。
 *
 * 列表是共用的原生列表（`desk/list.js`）：服务端分页、白名单排序、原生筛选（`collection.records`）。 */
(() => {
	// 旧地址「我的收款」（11-11～11-14，已发出的 LINE 卡【编辑】）：整页打开时（经 LIFF、LINE 登录跳转、已登录直接开）在框架路由开始前
	// 换成「收款」页同一笔。外壳的路由白名单只认菜单里的页（`desk/actions.js`），旧页已不在菜单，等路由起来再转就被换回首页了（11-14 真机）。
	const LEGACY = "/desk/pearnly-my-collections";
	if (window.location.pathname === LEGACY || window.location.pathname.startsWith(LEGACY + "/")) {
		window.history.replaceState(window.history.state, "", "/desk/pearnly-collections"
			+ window.location.pathname.slice(LEGACY.length) + window.location.search + window.location.hash);
	}
	const p = frappe.pearnly;
	p.collections = async (page, t, wrapper) => {
		const api = "pearnly_erp_portal.collection.api.";
		const root = "pearnly-collections";
		const status_label = value => (value ? t(`erp.collection.status.${value}`) : "");
		const source_label = value => (value ? t(`erp.collection.source.${value}`) : "");
		const money = value => p.money(value);
		const amount = value => Number(value || 0);
		// 待写入、此刻在等 Express 关闭（服务端读的时候算的只读字段 `express_wait`，`collection.records`）：状态不变，
		// 列表悬浮说明与点开后的内容是现成那句（11-12）
		// 服务端拒了（别人刚改过、电脑正在写…）框架已经弹出那句话；这里只接住，不让它变成页面上没接住的错误
		const settle = promise => promise.catch(window.pearnlyReported(null, "collections"));
		const waiting = row => (row?.status === "queued" && row?.express_wait ? __("express.error.ACCOUNT_BUSY_LOCKED") : "");
		let list, creating = false;
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

		async function open(name, from_card = false) {
			const data = await p.call(api + "api_collection", {name});
			if (data.status === "draft") return edit(name);
			const shown = (fieldname, label, value, fieldtype = "Data") => ({fieldtype, fieldname, label, read_only: 1,
				default: value ?? ""});
			const vouchers = (data.methods || []).filter(item => item.file);
			// 还没被电脑领走的（待写入、失败）可以撤回（同 LINE【撤回】）
			const can_withdraw = ["queued", "failed"].includes(data.status);
			const dialog = new frappe.ui.Dialog({title: data.name, size: "large", fields: [
				...(from_card ? [{fieldtype: "HTML", fieldname: "expired",
					options: `<p>${frappe.utils.escape_html(__("This card has expired. Use the latest card in LINE."))}</p>`}] : []),
				shown("customer_name", __("Customer Name"), data.customer_name),
				shown("customer", __("Customer Code"), data.customer),
				shown("doc_date", __("Collection Date"), data.doc_date, "Date"),
				shown("collector", __("Collector"), data.collector_name),
				shown("source", __("Source"), source_label(data.source)),
				{fieldtype: "Column Break"},
				shown("status", __("Status"), [status_label(data.status), waiting(data)].filter(Boolean).join(" · ")),
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
			if (can_withdraw) {
				dialog.set_primary_action(__("Withdraw"), () => void settle(withdraw(data.name, dialog)));
			}
			const viewer = vouchers.length
				? p.document_viewer(dialog.fields_dict.voucher_preview.$wrapper, {load: viewer_load}) : null;
			dialog.$wrapper.addClass("pearnly-dialog");
			dialog.onhide = () => viewer?.destroy();
			dialog.show();
			if (viewer) void viewer.show(vouchers[0].file);
		}

		async function edit(name) {
			const data = await p.call(api + "api_collection_card", {name}, "POST");
			const card = data.card;
			const methods = data.methods || {transfers: []};
			const current = Object.fromEntries((card.methods || []).map(row => [row.prefix, row]));
			const kinds = [...(methods.transfers || []).map(row => ({...row, role: "transfer"})),
				...(methods.discount ? [{...methods.discount, role: "discount"}] : [])];
			const choice = (rows, current) => [...(rows || []).map(row => ({value: row.code, label: `${row.code} ${row.name}`.trim()})),
				...(current && !(rows || []).some(row => row.code === current) ? [{value: current, label: current}] : [])];
			// 写前被电脑拒、退回草稿（`collection.push`，11-14）：原因放最前（同 LINE 还回的卡），改了再放行
			const returned = card.returned ? [{fieldtype: "Small Text", fieldname: "reason", label: __("Reason"), read_only: 1,
				default: String(card.reason || "").replace(/^\[[A-Za-z0-9_]+\]\s*/, "")}, {fieldtype: "Section Break"}] : [];
			const fields = [
				...returned,
				{fieldtype: "Data", fieldname: "customer", label: __("Customer"), read_only: 1,
					default: `${card.customer} ${card.customer_name}`},
				{fieldtype: "Data", fieldname: "book", label: __("Account Set"), read_only: 1, default: card.book},
				{fieldtype: "Column Break"},
				{fieldtype: "Select", fieldname: "salesman", label: __("Salesman"), default: card.salesman,
					options: [{value: "", label: ""}, ...choice(data.staff?.salesmen, card.salesman)]},
				{fieldtype: "Select", fieldname: "department", label: __("Department"), default: card.department,
					options: choice(data.staff?.departments, card.department)},
				{fieldtype: "Section Break", label: __("Invoices")},
				...card.invoices.map((row, index) => ({fieldtype: "Currency", fieldname: `invoice_${index}`,
					label: row.docnum, default: amount(row.amount),
					description: __("Due {0}", [money(data.available?.[row.docnum] ?? row.remaining)])})),
				{fieldtype: "Section Break", label: __("Collection Method")},
				...(methods.cash || amount(card.cash) ? [{fieldtype: "Currency", fieldname: "cash", label: __("Cash"),
					default: amount(card.cash)}] : []),
				...kinds.map((row, index) => ({fieldtype: "Currency", fieldname: `method_${index}`,
					label: row.role === "discount" ? __("Discount") : row.name, default: amount(current[row.prefix]?.amount)})),
			];
			const dialog = new frappe.ui.Dialog({title: card.name, fields, primary_action_label: __("Save"),
				primary_action: values => void settle((async () => {
					const invoices = card.invoices.map((row, index) => ({docnum: row.docnum, amount: values[`invoice_${index}`] || 0}))
						.filter(row => amount(row.amount) > 0);
					const chosen = kinds.map((row, index) => ({prefix: row.prefix, amount: values[`method_${index}`] || 0,
						voucher: current[row.prefix]?.voucher || ""})).filter(row => amount(row.amount) > 0);
					dialog.disable_primary_action();
					try {
						await p.call(api + "api_collection_save", {name: card.name, version: card.version,
							invoices: JSON.stringify(invoices), cash: values.cash || 0, methods: JSON.stringify(chosen),
						salesman: values.salesman || "", department: values.department || ""}, "POST");
					} finally {
						dialog.enable_primary_action();
					}
					frappe.utils.play_sound("click");
					frappe.show_alert({message: __("Saved"), indicator: "green"});
					dialog.hide();
					void list?.reload();
				})())});
			dialog.set_secondary_action_label(__("Withdraw"));
			dialog.set_secondary_action(() => void settle(withdraw(card.name, dialog)));
			dialog.$wrapper.addClass("pearnly-dialog");
			dialog.show();
		}

		async function withdraw(name, shown) {
			await p.call(api + "api_collection_withdraw", {name}, "POST");
			frappe.utils.play_sound("cancel");
			frappe.show_alert({message: status_label("withdrawn"), indicator: "orange"});
			shown?.hide();
			void list?.reload();
		}

		list = p.list({page, doctype: "ERP Collection", compact_columns: true,
			// 本页支持的筛选条件（官方的字段下拉里只列这三个）
			filter_fields: [
				{fieldname: "doc_date", label: __("Collection Date"), fieldtype: "Date"},
				{fieldname: "status", label: __("Status"), fieldtype: "Select"},
				{fieldname: "source", label: __("Source"), fieldtype: "Select"},
			],
			on_open: row => void settle(open(row.name)),
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
					title: (value, row) => waiting(row),
					cell_class: (value, row) => (row.status === "failed" ? "pearnly-payment-due" : "")},
			],
			// 列头点一下排序（服务端白名单 `collection.records.SORT_FIELDS`）
			sort_fields: ["doc_date", "collector", "customer_name", "cash", "transfer", "discount", "docnum"],
			load: async ({start, page_length, sort_by, sort_order}) => {
				const out = await p.call(api + "api_collections",
					{params: parameters(), start, limit: page_length, sort_by, sort_order});
				// 能录收款的人（服务端判，`records.page` 的 can_create）：页头「新建」
				if (out?.can_create && !creating) {
					creating = true;
					page.set_primary_action(__("New"), () => void p.collection_new({on_done: () => void list?.reload()}), "add");
				}
				return out;
			},
		});
		// 小的当场下载，大的排后台、做完推送后下载（`p.export_file`）
		page.add_menu_item(__("Export"), () => void p.export_file(`${api}api_collections_export`,
			{params: parameters()}));
		await list.ready;
		// LINE 确认卡【编辑】打开的 `pearnly-collections/<记录>`：直接开那一笔（不是草稿了先说卡片已失效）
		const show = p.routed_page({wrapper, root, render: async (rest, still_current) => {
			if (rest.length > 1) {p.redirect_up(root); return;}
			if (rest.length === 1 && still_current()) await open(rest[0], true);
		}});
		await show();
	};
})();
