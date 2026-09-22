(() => {
	const p = frappe.pearnly, api = "pearnly_erp_portal.inventory.api.";
	p.stockLedger = async (row, dates = {}) => {
		const t = await p.trade.texts();
		await frappe.require("report.bundle.js");
		const card = await p.call(api + "api_stock_card", { product: row.product, warehouse: row.warehouse,
			location: row.location || "", date_from: dates.date_from || "1900-01-01", date_to: dates.date_to || frappe.datetime.get_today() });
		const d = p.dialog(card.product_name || row.product, [
			// 四个只读字段一排两个（`p.field_rows`）：一列到底会把这几行拉得又长又空，
			// 表格反而被推到看不见的地方。
			...p.field_rows([
				{ fieldtype: "Data", fieldname: "warehouse", label: t("trade.warehouse"), read_only: 1, default: row.warehouse_name || row.warehouse },
				{ fieldtype: "Data", fieldname: "location", label: t("trade.location"), read_only: 1, default: row.location || "" },
				{ fieldtype: "Data", fieldname: "opening", label: t("trade.opening"), read_only: 1, default: p.number(card.opening.qty) },
				{ fieldtype: "Data", fieldname: "closing", label: t("trade.closing"), read_only: 1, default: p.number(card.closing.qty) },
			]),
			{ fieldtype: "HTML", fieldname: "movements" },
		], t("trade.close"), async (_values, dialog) => dialog.hide());
		d.$wrapper.find(".modal-dialog").addClass("modal-xl");
		let table;
		d.onhide = () => table?.destroy();
		d.on_page_show = async () => {
			if (table) return;
			table = await p.table(d.fields_dict.movements.$wrapper,
			["trade.date", "trade.kind", "trade.reference", "trade.qty", "trade.balance", "trade.cost"].map(key => t(key)),
			card.rows.map(m => [m.date, __(m.kind), m.desc, p.number(m.qty), p.number(m.bal_qty), p.money(m.bal_unit_cost)]));
		};
		if (d.display) await d.on_page_show();
	};
	p.stockCard = async (page, t) => {
		let list;
		const fields = {};
		for (const [name, type, label, value] of [
			["date_from", "Date", "trade.from", frappe.datetime.month_start()],
			["date_to", "Date", "trade.to", frappe.datetime.get_today()],
			["warehouse", "Data", "trade.warehouse", ""],
		]) fields[name] = page.add_field({ fieldname: name, fieldtype: type, label: t(label), default: value, change: () => list?.reload() });
		const query = () => Object.fromEntries(Object.entries(fields).map(([key, field]) => [key, field.get_value() || undefined]));
		// 这是报表：每行是一个"商品×仓库×仓位"的收发汇总，没有可删的东西。选中一行时给的是
		// 对应的那一步 —— 打开这条汇总背后的流水明细（查询条件跟着报表走）。
		// 沿用这一页自己的 `trade.` 词表（`t` 就是这个命名空间；写 stock.* 会露出键名）。
		const selection = p.selection_action({
			page, list: () => list,
			label: () => t("trade.action.ledger"),
			when: (_row, rows) => rows.length === 1,
			reason: () => t("trade.action.ledgerSingle"),
			run: async rows => { void p.stockLedger(rows[0], query()); },
		});
		list = p.list({ page, doctype: "ERP Stock Movement", selectable: true, on_selection: selection,
			on_open: row => p.stockLedger(row, query()),
			load: async ({ start, page_length }) => {
				const rows = await p.call(api + "api_stock_summary", query());
				return { items: rows.slice(start, start + page_length), total: rows.length };
			},
			columns: [
				{ fieldname: "product_name", label: t("trade.product") },
				{ fieldname: "warehouse_name", label: t("trade.warehouse") },
				{ fieldname: "location", label: t("trade.location") },
				{ fieldname: "opening_qty", fieldtype: "Float", label: t("trade.opening"), format: value => p.number(value) },
				{ fieldname: "in_qty", fieldtype: "Float", label: t("trade.in"), format: value => p.number(value) },
				{ fieldname: "out_qty", fieldtype: "Float", label: t("trade.out"), format: value => p.number(value) },
				{ fieldname: "closing_qty", fieldtype: "Float", label: t("trade.closing"), format: value => p.number(value) },
			],
		});
		p.add_refresh(page, () => list.reload()); await list.ready;
	};
})();
