/* 收发存报表（商品 × 仓库 × 仓位的收发汇总）+ 单品收发存明细。
 *
 * 表格照**旧项目那张收发存**的格式做（用户 2026-09-22 给的参考图）：
 *   分组表头：日期 | 单据号 | 入库(数量/单价/金额) | 出库(数量/单价/金额) | 结存(数量/单价/金额)
 *   一行一笔进出（正数进"入库"那三列、负数进"出库"那三列，另一侧留空）
 *   末行「合计 · N 条明细」（金额任一未知就整列显示 —，不拿已知部分冒充）
 *   日期是佛历 `08/04/2569`，金额两位小数不带币种 —— 都是旧项目的显示口径。
 *
 * 为什么这里自己拼表：**带分组表头 + 合计行的报表**用不了那套只认扁平行列的 DataTable
 * （框架自己的报表打印件 `print_grid.html` 也是用同一套官方表格类拼的）。
 * 所以格子的**内容**由纯函数 `p.ledger_grid` 算好（可单测），DOM 那一层只按官方类名摆放，
 * 数字右对齐用 Bootstrap 的 `text-right`，不上色。
 *
 * 两条路由：
 *   /desk/pearnly-stock-card                      报表列表（点行 / 选中"查看流水"）
 *   /desk/pearnly-stock-card/<商品>/<仓库>/<仓位>  二级页面：日期区间 + 期初/期末 + 明细表
 * 库存页点行打开的是同一个明细，但走**弹窗**（`p.stockLedger`）。
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

	// ── 表格内容（纯函数，可单测）─────────────────────────────────────────────

	// 单价/金额：成本未知（岗位看不到成本）显示 —；数字本身两位小数。
	const money = value => (value === null || value === undefined ? "—" : p.amount(value));
	const blank = "";

	// 一行的某一侧（入库或出库）：数量/单价/金额；不是这一侧就三格留空。
	// 出库行的 `qty` 在库里是负数（台账按符号记），列头已经写明"出库"，所以显示绝对值。
	function side(movement, is_this_side) {
		if (!is_this_side) return [blank, blank, blank];
		return [p.plain_number(Math.abs(Number(movement.qty) || 0)),
			money(movement.unit_price), money(movement.amount)];
	}

	function movement_cells(movement) {
		const qty = Number(movement.qty) || 0;
		return [
			p.buddhist_date(movement.date),
			movement.doc_no || movement.desc || "—",
			...side(movement, qty > 0),
			...side(movement, qty < 0),
			p.plain_number(movement.bal_qty), money(movement.bal_unit_cost), money(movement.bal_value),
		];
	}

	// 期初：没有期初余额（数量与金额都是 0）就不占一行 —— 参考图里"本期第一笔"那种就没有期初行。
	function opening_cells(t, card) {
		const opening = card.opening || {};
		if (!Number(opening.qty) && !Number(opening.value)) return null;
		return [
			p.buddhist_date(card.date_from), t("trade.rowOpening"),
			blank, blank, blank, blank, blank, blank,
			p.plain_number(opening.qty), money(opening.unit_cost), money(opening.value),
		];
	}

	// 合计：两侧数量分别求和；金额**任一笔未知就整列 —**（旧项目同一个诚实口径：
	// 不拿已知部分的和冒充）；结存取期末。
	function total_cells(t, card) {
		const sum = { in_qty: 0, out_qty: 0, in_amount: 0, out_amount: 0 };
		let in_known = true, out_known = true;
		for (const movement of card.rows || []) {
			const qty = Number(movement.qty) || 0;
			const amount = movement.amount;
			const target = qty > 0 ? "in" : qty < 0 ? "out" : null;
			if (!target) continue;
			sum[`${target}_qty`] += Math.abs(qty);
			if (amount === null || amount === undefined) {
				if (target === "in") in_known = false; else out_known = false;
			} else {
				sum[`${target}_amount`] += Number(amount) || 0;
			}
		}
		const closing = card.closing || {};
		// **十个格子**：标签跨"日期 + 单据号"两列，后面九格对应九个数字列。
		// 三个"单价"列在合计里**留空**（参考图就是这样：合计算的是数量与金额，没有单价）。
		// 这里以前多推了一个占位格，整行于是比表头宽一列、所有数字都右移一格（实测过）。
		return [
			t("trade.totalRows", (card.rows || []).length),
			p.plain_number(sum.in_qty), blank, in_known ? p.amount(sum.in_amount) : "—",
			p.plain_number(sum.out_qty), blank, out_known ? p.amount(sum.out_amount) : "—",
			p.plain_number(closing.qty), blank, money(closing.value),
		];
	}

	// 表头 + 逐行 + 合计，全是**文本**：DOM 那一层只负责按官方类名摆。
	// 形状照参考图：第一行 `单据(2) | 入库(3) | 出库(3) | 结存(3)`，第二行
	// `日期 | 单据号 | 数量 单价 金额 ×3`。
	p.ledger_grid = (t, card) => ({
		// [文案, 跨几列]
		group_head: [[t("trade.groupDoc"), 2], [t("trade.groupIn"), 3],
			[t("trade.groupOut"), 3], [t("trade.groupBalance"), 3]],
		// 第二行的 11 个格子；前两个左对齐，其余右对齐（数字列）。
		head: [t("trade.date"), t("trade.docNo"),
			...Array.from({ length: 3 }, () =>
				["trade.qty", "trade.price", "trade.amount"].map(key => t(key))).flat()],
		rows: [
			...(opening_cells(t, card) ? [opening_cells(t, card)] : []),
			...(card.rows || []).map(movement_cells),
		],
		foot: total_cells(t, card),
	});

	// 官方表格类（框架自己 messages/print_grid 用的就是 `.table .table-bordered`）：
	// 前两列左对齐，九个数字列右对齐，不进任何颜色。
	function build_table(grid) {
		const node = (tag, className, text) => {
			const element = document.createElement(tag);
			if (className) element.className = className;
			if (text !== undefined && text !== null) element.textContent = String(text);
			return element;
		};
		const table = node("table", "table table-bordered");
		const head = node("thead");
		const first = node("tr");
		for (const [text, span] of grid.group_head) {
			const th = node("th", "text-center", text);
			th.colSpan = span;
			first.appendChild(th);
		}
		const second = node("tr");
		grid.head.forEach((text, index) =>
			second.appendChild(node("th", index < 2 ? null : "text-right", text)));
		head.appendChild(first); head.appendChild(second);
		table.appendChild(head);

		const body = node("tbody");
		if (!grid.rows.length) {
			const row = node("tr");
			const td = node("td", "text-center text-muted", __("Nothing to show"));
			td.colSpan = 11;
			row.appendChild(td);
			body.appendChild(row);
		}
		for (const cells of grid.rows) {
			const row = node("tr");
			cells.forEach((text, index) =>
				row.appendChild(node("td", index < 2 ? null : "text-right", text)));
			body.appendChild(row);
		}
		table.appendChild(body);

		const foot = node("tfoot");
		const row = node("tr", "bold");
		const label = node("td", null, grid.foot[0]);
		label.colSpan = 2;
		row.appendChild(label);
		grid.foot.slice(1).forEach(text => row.appendChild(node("td", "text-right", text)));
		foot.appendChild(row);
		table.appendChild(foot);
		return table;
	}

	p.ledger_table = (t, card) => build_table(p.ledger_grid(t, card));

	// ── 库存页用的那个**弹窗**明细（**不是**收发存那张表）─────────────────────
	//
	// 用户 2026-09-22：收发存那一页的表格按参考图做，但**库存页的"查看"没让动**。
	// 所以这里保持它原来的样子：原生 DataTable、六列扁平行（日期/单据类型/关联单据/数量/
	// 该仓位结存/单位成本），只有布局沿用"字段两栏 + 表格在下占满宽度"那一版。
	// 与 `p.ledger_table`（分组表头 + 合计行）是两套东西，不要合并。
	const movement_columns = t => ["trade.date", "trade.kind", "trade.reference", "trade.qty",
		"trade.balance", "trade.cost"].map(key => t(key));
	// "关联单据"优先显示**单据号**（入库 IR / 出库 IS / 采购 PE / 销售 SI 那种系统号，
	// 旧线上报表显示的就是它）；没有号的来源（如盘点过账）才落回内部单据名。
	const movement_rows = card => card.rows.map(m => [m.date, __(m.kind), m.doc_no || m.desc,
		p.number(m.qty), p.number(m.bal_qty), p.money(m.bal_unit_cost)]);

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

	// ── 报表列表 + 二级明细页 ────────────────────────────────────────────────

	p.stockCard = async (page, t, wrapper) => {
		let list, form;
		// 从列表进二级页面时把报表当前的日期区间带过去（同一次会话内）。
		let range = null;

		// 右上角只留一颗刷新：`p.clear_page` 每次换级都会清掉旧的图标（不清就会越堆越多）。
		function set_refresh(action) {
			page.clear_icons?.();
			p.add_refresh(page, action);
		}

		function open_list() {
			// 日期区间留在工具栏（它必须**一直看得见**：期初/期末就是按这个区间滚出来的，
			// 与 ERPNext 自己的报表工具栏同一个做法）；商品与仓库那两条改走**官方原生筛选**
			// （那颗 Filter 按钮 + 弹层里的条件行），不再堆在工具栏上（用户 2026-09-23：
			// "把我们自己设计的勾选筛选取消掉，做成官方原生的显示出来"）。
			const fields = {};
			for (const [name, type, label, value] of [
				["date_from", "Date", "trade.from", frappe.datetime.month_start()],
				["date_to", "Date", "trade.to", frappe.datetime.get_today()],
			]) fields[name] = page.add_field({ fieldname: name, fieldtype: type, label: t(label), default: value, change: () => list?.reload() });
			const query = () => {
				const picked = p.filter_values(list);
				return {date_from: fields.date_from.get_value() || undefined,
					date_to: fields.date_to.get_value() || undefined,
					warehouse: picked.warehouse || undefined, product: picked.product || undefined};
			};
			// 这是报表：每行是一个"商品×仓库×仓位"的收发汇总，没有可删的东西。选中一行时给的是
			// 对应的那一步 —— 打开这条汇总背后的明细（**二级页面**，这个视图字段多）。
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
				// 本页支持的筛选条件（官方的字段下拉里只列这两个）。
				filter_fields: [
					{ fieldname: "warehouse", label: t("trade.warehouse"), fieldtype: "Link" },
					{ fieldname: "product", label: t("trade.product"), fieldtype: "Link" },
				],
				// 收发存七列一屏放得下、不用横向滑（用户 2026-09-23："内容太多，要滑动才能看得到，
				// 看下能不能放在一屏显示完整"）。窗口更宽时按下面的比例等比放宽。
				compact_columns: true,
				// 列头点一下排序：一次取全，排完再切这一页（同 core `p.sort_rows`）。
				sort_fields: ["product_name", "warehouse_name", "location",
					"opening_qty", "in_qty", "out_qty", "closing_qty"],
				load: async ({ start, page_length, sort_by, sort_order }) => {
					const rows = await p.call(api + "api_stock_summary", query());
					const sorted = p.sort_rows(rows, { sort_by, sort_order,
						fields: ["product_name", "warehouse_name", "location",
							"opening_qty", "in_qty", "out_qty", "closing_qty"] });
					return { items: sorted.slice(start, start + page_length), total: sorted.length };
				},
				columns: [
					{ fieldname: "product_name", width: 200, min_width: 168, label: t("trade.product") },
					{ fieldname: "warehouse_name", width: 132, min_width: 110, label: t("trade.warehouse") },
					{ fieldname: "location", width: 66, min_width: 54, label: t("trade.location") },
					{ fieldname: "opening_qty", width: 88, min_width: 78, fieldtype: "Float", label: t("trade.opening"), format: value => p.number(value) },
					{ fieldname: "in_qty", width: 88, min_width: 78, fieldtype: "Float", label: t("trade.in"), format: value => p.number(value) },
					{ fieldname: "out_qty", width: 88, min_width: 78, fieldtype: "Float", label: t("trade.out"), format: value => p.number(value) },
					{ fieldname: "closing_qty", width: 90, min_width: 80, fieldtype: "Float", label: t("trade.closing"), format: value => p.number(value) },
				],
			});
			set_refresh(() => list.reload());
		}

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
			const card = await fetch_card({ product, warehouse, location }, dates);
			if (frappe.get_route()[0] !== root) return;
			p.breadcrumbs({ label: __("nav.stockCard"), route: url(),
				trail: [{ label: card.product_name || product, route: "" }] });
			// 二级页面的重新取数：日期一改就按新区间重取这张卡（期初/期末/明细一起换）。
			// 日期那两格的 `change` 之前调的是一个**不存在的** `reload`
			// （实测 `ReferenceError: reload is not defined`），于是改了日期整页没反应、
			// 控制台还报错。先定义再挂给 `change` 和右上角那颗刷新，两处同一个函数。
			async function refresh_card() {
				const values = form.get_values() || {};
				const current = await fetch_card({ product, warehouse, location },
					{ date_from: values.date_from, date_to: values.date_to });
				if (frappe.get_route()[0] !== root) return;
				form.set_value("opening", p.number(current.opening.qty));
				form.set_value("closing", p.number(current.closing.qty));
				form.fields_dict.movements.$wrapper.empty().append(p.ledger_table(t, current));
			}
			// 四栏（每栏两行）把宽度用满，明细表另起一节占满整宽 —— 用户 2026-09-22 口径：
			// "后面不要留那么多"，字段别占半边、把表挤走。
			form = p.layout(page.main, p.field_rows([
				{ fieldtype: "Date", fieldname: "date_from", label: t("trade.from"),
					default: dates.date_from, change: () => void refresh_card() },
				{ fieldtype: "Date", fieldname: "date_to", label: t("trade.to"),
					default: dates.date_to, change: () => void refresh_card() },
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
			host.empty().append(p.ledger_table(t, card));
			set_refresh(refresh_card);
		}

		async function render(rest) {
			p.clear_page(page);
			list = null; form = null;
			if (!rest.length) { open_list(); return; }
			if (rest.length > 3) { p.redirect_up(root); return; }
			await open_ledger_page(rest);
		}

		const show = p.routed_page({ wrapper, root, render });
		await show();
	};
})();
