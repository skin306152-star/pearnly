/* 我的收款（11-11）：收款员本人在 LINE / CC 录的收款。列表只看；还没放行的草稿点开是原生弹窗，改各张本次收、现金、
 * 各方式与折扣、销售员与部门（`collection.service.update_draft`，只认打开时的版本）。LINE 确认卡【编辑】打开的就是这一页的
 * `pearnly-my-collections/<记录>`：直接开那一笔的弹窗；存好后回 LINE 点【确认】，卡片版本变了会重出新卡再确认。
 *
 * 列表是共用的原生列表（`desk/list.js`）：服务端分页（`service.mine`，新的在前）。 */
(() => {
	const p = frappe.pearnly;
	p.myCollections = async (page, t, wrapper) => {
		const api = "pearnly_erp_portal.collection.api.";
		const root = "pearnly-my-collections";
		const status_label = value => (value ? t(`erp.collection.status.${value}`) : "");
		// 待写入、此刻在等 Express 关闭（服务端读的时候算的只读字段 `express_wait`，`collection.service.mine`）：状态不变，
		// 列表悬浮说明与点开后的内容是现成那句（11-12）
		const waiting = row => (row?.status === "queued" && row?.express_wait ? __("express.error.ACCOUNT_BUSY_LOCKED") : "");
		const money = value => p.money(value);
		const amount = value => Number(value || 0);
		let list = null;

		async function edit(name) {
			const data = await p.call(api + "api_my_collection", {name}, "POST");
			const card = data.card;
			if (card.status !== "draft") {
				frappe.msgprint({title: card.name, message: [card.customer_name, money(card.total), card.docnum
					|| status_label(card.status)].filter(Boolean).map(frappe.utils.escape_html).join("<br>")});
				return;
			}
			const methods = data.methods || {transfers: []};
			const current = Object.fromEntries((card.methods || []).map(row => [row.prefix, row]));
			const kinds = [...(methods.transfers || []).map(row => ({...row, role: "transfer"})),
				...(methods.discount ? [{...methods.discount, role: "discount"}] : [])];
			const choice = (rows, current) => [...(rows || []).map(row => ({value: row.code, label: `${row.code} ${row.name}`.trim()})),
				...(current && !(rows || []).some(row => row.code === current) ? [{value: current, label: current}] : [])];
			const fields = [
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
				primary_action: async values => {
					const invoices = card.invoices.map((row, index) => ({docnum: row.docnum, amount: values[`invoice_${index}`] || 0}))
						.filter(row => amount(row.amount) > 0);
					const chosen = kinds.map((row, index) => ({prefix: row.prefix, amount: values[`method_${index}`] || 0,
						voucher: current[row.prefix]?.voucher || ""})).filter(row => amount(row.amount) > 0);
					dialog.disable_primary_action();
					try {
						await p.call(api + "api_my_collection_save", {name: card.name, version: card.version,
							invoices: JSON.stringify(invoices), cash: values.cash || 0, methods: JSON.stringify(chosen),
						salesman: values.salesman || "", department: values.department || ""}, "POST");
					} finally {
						dialog.enable_primary_action();
					}
					frappe.utils.play_sound("click");
					frappe.show_alert({message: __("Saved"), indicator: "green"});
					dialog.hide();
					void list?.reload();
				}});
			dialog.$wrapper.addClass("pearnly-dialog");
			dialog.show();
		}

		const show = p.routed_page({wrapper, root, render: async (rest, still_current) => {
			if (rest.length > 1) {p.redirect_up(root); return;}
			if (!list) {
				page.set_title(__("erp.nav.myCollections"));
				p.breadcrumbs({label: __("erp.nav.myCollections"), route: p.subroute(root)});
				list = p.list({page, doctype: "ERP Collection", compact_columns: true,
					on_open: row => void edit(row.name),
					columns: [
						{fieldname: "doc_date", width: 100, min_width: 84, label: __("Collection Date")},
						{fieldname: "customer_name", width: 160, min_width: 96, label: __("Customer Name"),
							format: (value, row) => value || row.customer || ""},
						{fieldname: "total", width: 100, min_width: 70, label: __("Total"), format: value => money(value)},
						{fieldname: "docnum", width: 110, min_width: 76, label: __("Express"),
							format: (value, row) => value || status_label(row.status),
							title: (value, row) => waiting(row)},
					],
					load: ({start, page_length}) => p.call(api + "api_my_collections", {start, limit: page_length}),
				});
				await list.ready;
			}
			if (rest.length === 1 && still_current()) await edit(rest[0]);
		}});
		await show();
	};
})();
