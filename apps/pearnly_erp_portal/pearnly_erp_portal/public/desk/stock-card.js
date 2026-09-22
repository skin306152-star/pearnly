/* 收发存报表（商品 × 仓库 × 仓位的收发汇总）+ 明细流水。
 *
 * 用户 2026-09-22 口径：**收发存的"查看"是二级页面**（它字段多、要看很长一张流水），
 * 其它列表（库存、商品）的"查看"仍是原生弹窗。所以这里：
 *   /desk/pearnly-stock-card                     报表列表
 *   /desk/pearnly-stock-card/<商品>/<仓库>/<仓位>  二级页面：日期区间 + 期初/期末 + 流水表
 * 库存页点行打开的是同一个流水视图，但走 **弹窗**（`p.stockLedger`）。
 *
 * 数量按实际值显示（`p.number`），金额按岗位遮蔽（服务端没给就留空）。
 */
(() => {
	const p = frappe.pearnly;
	const api = "pearnly_erp_portal.inventory.api.";
	const root = "pearnly-stock-card";
	const url = (...parts) => p.subroute(root, ...parts);

	const fetch_card = (row, dates = {}) => p.call(api + "api_stock_card", {
		product: row.product, warehouse: row.warehouse, location: row.location || "",
		date_from: dates.date_from || "1900-01-01",
		date_to: dates.date_to || frappe.datetime.get_today(),
	});

	// 流水表本体：列由代码定义，两个入口（弹窗 / 二级页面）共用一份，免得两边走样。
	const movement_columns = t => ["trade.date", "trade.kind", "trade.reference", "trade.qty",
		"trade.balance", "trade.cost"].map(key => t(key));
	// "关联单据"优先显示**单据号**（入库 IR / 出库 IS / 采购 PE / 销售 SI 那种系统号，
	// 旧线上报表显示的就是它）；没有号的来源（如盘点过账）才落回内部单据名。
	const movement_rows = card => card.rows.map(m => [m.date, __(m.kind), m.doc_no || m.desc,
		p.number(m.qty), p.number(m.bal_qty), p.money(m.bal_unit_cost)]);

	// 库存页用的那个**弹窗**流水：四个只读字段一排两个（`p.field_rows`），下面是表。
	p.stockLedger = async (row, dates = {}) => {
		const t = await p.trade.texts();
		await frappe.require("report.bundle.js");
		const card = await fetch_card(row, dates);
		const d = p.dialog(card.product_name || row.product, [
			// 四个只读字段两栏，**表格作为整行控件一起交给 `field_rows`** —— 这样它落在
			// 新的一节里、占满整宽；只把字段交给它、表格自己 concat 在后面的话，表格会
			// 掉进"当前栏"、被挤成窄窄一条（实测）。
			...p.field_rows([
				{ fieldtype: "Data", fieldname: "warehouse", label: t("trade.warehouse"), read_only: 1, default: row.warehouse_name || row.warehouse },
				{ fieldtype: "Data", fieldname: "location", label: t("trade.location"), read_only: 1, default: row.location || "" },
				{ fieldtype: "Data", fieldname: "opening", label: t("trade.opening"), read_only: 1, default: p.number(card.opening.qty) },
				{ fieldtype: "Data", fieldname: "closing", label: t("trade.closing"), read_only: 1, default: p.number(card.closing.qty) },
				{ fieldtype: "HTML", fieldname: "movements" },
			]),
		], t("trade.close"), async (_values, dialog) => dialog.hide());
		d.$wrapper.find(".modal-dialog").addClass("modal-xl");
		let table;
		d.onhide = () => table?.destroy();
		d.on_page_show = async () => {
			if (table) return;
			table = await p.table(d.fields_dict.movements.$wrapper, movement_columns(t), movement_rows(card));
		};
		if (d.display) await d.on_page_show();
	};

	p.stockCard = async (page, t, wrapper) => {
		let list, form, table;
		// 从列表进二级页面时把报表当前的日期区间带过去（同一次会话内，位置固定在这个闭包里）。
		// 直接深链进来（刷新、书签）就落在默认区间上，页面上那两个日期字段随时能改。
		let range = null;

		function open_list() {
			const fields = {};
			for (const [name, type, label, value] of [
				["date_from", "Date", "trade.from", frappe.datetime.month_start()],
				["date_to", "Date", "trade.to", frappe.datetime.get_today()],
				["warehouse", "Data", "trade.warehouse", ""],
			]) fields[name] = page.add_field({ fieldname: name, fieldtype: type, label: t(label), default: value, change: () => list?.reload() });
			const query = () => Object.fromEntries(Object.entries(fields).map(([key, field]) => [key, field.get_value() || undefined]));
			// 这是报表：每行是一个"商品×仓库×仓位"的收发汇总，没有可删的东西。选中一行时给的是
			// 对应的那一步 —— 打开这条汇总背后的流水（**二级页面**，这个视图字段多）。
			// 沿用这一页自己的 `trade.` 词表（`t` 就是这个命名空间；写 stock.* 会露出键名）。
			const selection = p.selection_action({
				page, list: () => list,
				label: () => t("trade.action.ledger"),
				when: (_row, rows) => rows.length === 1,
				reason: () => t("trade.action.ledgerSingle"),
				run: async rows => { open_ledger(rows[0], query()); },
			});
			list = p.list({ page, doctype: "ERP Stock Movement", selectable: true, on_selection: selection,
				on_open: row => open_ledger(row, query()),
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
			p.add_refresh(page, () => list.reload());
		}

		// 二级页面：把报表那一行的流水摊成整页，日期区间可以在这一页直接改。
		function open_ledger(row, dates) {
			range = dates;
			frappe.set_route(root, row.product, row.warehouse, row.location || "");
		}

		async function open_ledger_page(rest) {
			const [product, warehouse, location = ""] = rest;
			const dates = {
				date_from: range?.date_from || frappe.datetime.month_start(),
				date_to: range?.date_to || frappe.datetime.get_today(),
			};
			range = null;
			await frappe.require("report.bundle.js");
			const card = await fetch_card({ product, warehouse, location }, dates);
			if (frappe.get_route()[0] !== root) return;
			p.breadcrumbs({ label: __("nav.stockCard"), route: url(),
				trail: [{ label: card.product_name || product, route: "" }] });
			// 四栏（每栏两行）把宽度用满，表格另起一节占满整宽 —— 用户 2026-09-22 口径：
			// "后面不要留那么多"，字段别占半边、把表挤走。
			form = p.layout(page.main, p.field_rows([
				{ fieldtype: "Date", fieldname: "date_from", label: t("trade.from"),
					default: dates.date_from, change: () => void reload() },
				{ fieldtype: "Date", fieldname: "date_to", label: t("trade.to"),
					default: dates.date_to, change: () => void reload() },
				{ fieldtype: "Data", fieldname: "product", label: t("trade.product"),
					read_only: 1, default: card.product_name || product },
				{ fieldtype: "Data", fieldname: "warehouse", label: t("trade.warehouse"),
					read_only: 1, default: warehouse },
				{ fieldtype: "Data", fieldname: "location", label: t("trade.location"),
					read_only: 1, default: location || "—" },
				{ fieldtype: "Data", fieldname: "opening", label: t("trade.opening"),
					read_only: 1, default: p.number(card.opening.qty) },
				{ fieldtype: "Data", fieldname: "closing", label: t("trade.closing"),
					read_only: 1, default: p.number(card.closing.qty) },
				{ fieldtype: "HTML", fieldname: "movements" },
			], 4));
			const host = form.fields_dict.movements.$wrapper;
			let current = card;
			async function paint() {
				table?.destroy();
				table = await p.table(host, movement_columns(t), movement_rows(current));
			}
			async function reload() {
				const values = form.get_values() || {};
				current = await fetch_card({ product, warehouse, location },
					{ date_from: values.date_from, date_to: values.date_to });
				form.set_value("opening", p.number(current.opening.qty));
				form.set_value("closing", p.number(current.closing.qty));
				await paint();
			}
			await paint();
		}

		async function render(rest) {
			p.clear_page(page);
			list = null; form = null; table?.destroy(); table = null;
			if (!rest.length) { open_list(); return; }
			if (rest.length > 3) { p.redirect_up(root); return; }
			await open_ledger_page(rest);
		}

		const show = p.routed_page({ wrapper, root, render });
		await show();
	};
})();
