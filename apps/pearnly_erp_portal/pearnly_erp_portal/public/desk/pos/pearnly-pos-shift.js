/* 收银台的开班 / 交班弹窗（**两条路共用一份**）。
 *
 * 为什么单独一个文件：这两个弹窗原先写在 `desk/cashier.js` 那个大闭包里 —— 而那个闭包在
 * "对照路由"（`?pearnly_pos_next=1`）分支里**提前 return**，里面的函数根本没定义。
 * 搬过来的官方收银台也要「交班」这一项（用户 2026-09-24 口径：右上角那三个点里
 * **之前是什么**就放什么），所以把这一块挪到共用层，两边调同一份，不再各写一遍。
 * 旧页面下线时删掉的是它自己那一套界面，这份继续留着。
 *
 * 用法（调用方传自己的文案解析器 `t` 与"完事之后干什么"）：
 *   frappe.pearnly.pos_shift.open({ t, on_done });
 *   frappe.pearnly.pos_shift.close({ t, shift, on_done });
 * `shift` 就是 `api_current_shift` 那一份（含 balances / sales_count / net / change_total…）。
 */
frappe.provide("frappe.pearnly.pos_shift");

(() => {
	const p = frappe.pearnly;
	const api = "pearnly_erp_portal.cashier.api.";
	// 与 `ERP Sale Payment.method` 同一个集合（`cashier/contracts.py` 的 `PAYMENT_METHODS`）。
	const METHODS = ["cash", "card", "promptpay", "transfer"];
	const num = value => {
		const parsed = Number(String(value ?? "").replace(/,/g, ""));
		return isFinite(parsed) ? parsed : 0;
	};
	const round2 = value => Math.round(num(value) * 100) / 100;
	const money = value => p.money(value);
	// 币种：弹窗顶上那一格给非表格字段用；子表每一行也要带（网格里的 `options: "currency"`
	// 取的是**本行**的 currency，不带就按系统默认画成 `$`）。
	const currency_field = () => ({ fieldtype: "Data", fieldname: "currency", hidden: 1,
		default: frappe.boot.sysdefaults.currency || "THB" });
	// 官方那张对账表是整页表单，我们放在弹窗里 —— 给宽一点，五列才看得清。
	const widen = dialog => dialog.$wrapper.find(".modal-dialog").addClass("modal-lg");

	// ⚠️ 官方网格的 `columns` **不是相对权重，是十二等分里的份数**：`grid_row.js` 直接把它
	// 拼成 `col-xs-<columns>`（`make_column`），而且 `setup_columns()` 里 `total_colsize > 10`
	// 会打开横向滚动。原来开班 5+4、交班 5+4+3+3+3=18 —— 交班那张表比容器宽 50%，
	// 整数列被裁在弹窗外面，左边的付款方式被压成空白（用户 2026-09-23 截图："这个界面显示不对"）。
	// 所以每张表的总份数都排到 ≤10，并且把宽度留给要点的那几列。
	const method_field = (t, columns) => ({
		fieldname: "method", fieldtype: "Select", options: METHODS.join("\n"),
		label: t("trade.method"), in_list_view: 1, reqd: 1, columns,
	});
	// 期初金额只在**开班**那张表里给：交班时它是"开班时点过的数"，其他付款方式恒为 0，
	// 占一列会把对账表挤成横向滚动（官方 POS Closing Entry 的收银对账表也只有
	// 付款方式 / 应有 / 实点 / 差额四列）。
	const opening_field = t => ({
		fieldname: "opening_amount", fieldtype: "Currency", options: "currency",
		label: t("trade.openingAmount"), in_list_view: 1, columns: 4,
	});

	function balance_table(t, fields_extra = [], rows = null,
		{ method_columns = 6, with_opening = true } = {}) {
		return [
			{ fieldtype: "Table", fieldname: "balances", label: t("trade.balances"),
				cannot_add_rows: 1, in_place_edit: 1, reqd: 1,
				data: rows || METHODS.map(method => ({ method, opening_amount: 0,
					counted_amount: 0, currency: frappe.boot.sysdefaults.currency || "THB" })),
				fields: [
					method_field(t, method_columns),
					...(with_opening ? [opening_field(t)] : []),
					...fields_extra,
				] },
		];
	}

	async function open({ t, on_done } = {}) {
		const terminals = await p.call(api + "api_list_terminals");
		const rows = Array.isArray(terminals) ? terminals : (terminals?.terminals || []);
		const preferred = rows.find(row => Number(row.is_default)) || rows[0];
		let dialog;
		dialog = p.dialog(t("trade.openShift"), [
			currency_field(),
			{ fieldtype: "Link", fieldname: "terminal", options: "ERP POS Terminal", only_select: true,
				get_query: p.terminal_query(),
				label: t("trade.terminal"), reqd: 1, default: preferred?.name || "",
				description: t("trade.terminalHint") },
			...balance_table(t),
			{ fieldtype: "Small Text", fieldname: "note", label: t("trade.note") },
		], t("trade.openShift"), async (values, d) => {
			const openings = Object.fromEntries((values.balances || [])
				.filter(row => row.opening_amount)
				.map(row => [row.method, num(row.opening_amount)]));
			const result = await p.call(api + "api_open_shift",
				{ terminal: values.terminal, balances: openings, note: values.note }, "POST");
			d.hide();
			p.success(t("trade.shiftOpened", result.shift_seq));
			await (on_done ? on_done(result) : null);
		});
		widen(dialog);
	}

	async function close({ t, shift, on_done } = {}) {
		if (!shift) { frappe.msgprint(t("trade.noShift")); return; }
		const data = shift;
		const expected = Object.fromEntries((data.balances || [])
			.map(row => [row.method, num(row.expected_amount)]));
		const openings = Object.fromEntries((data.balances || [])
			.map(row => [row.method, num(row.opening_amount)]));
		let dialog;
		const paint = () => {
			const grid = dialog?.fields_dict?.balances?.grid;
			let cash_diff = 0;
			for (const row of grid?.data || []) {
				row.expected_amount = expected[row.method] || 0;
				row.difference = round2(num(row.counted_amount) - num(row.expected_amount));
				// 只重画动过的那一行，别整表刷新（正输着数字时整表重画会把焦点甩掉）。
				grid?.grid_rows_by_docname?.[row.name]?.refresh_row?.(row);
				if (row.method === "cash") cash_diff = row.difference;
			}
			dialog?.set_value("cash_diff", cash_diff);
			dialog?.fields_dict?.cash_diff?.$wrapper
				?.find("input, .control-value, .like-disabled-input")
				.toggleClass("pearnly-payment-due", Boolean(cash_diff));
		};
		// 表里先把期初与应有填好、默认实点=应有（现金那一行就是抽屉里应有的数）。
		const rows = METHODS.map(method => ({
			method, currency: frappe.boot.sysdefaults.currency || "THB",
			opening_amount: openings[method] || 0,
			expected_amount: expected[method] || 0,
			counted_amount: expected[method] || 0,
			difference: 0,
		}));
		dialog = p.dialog(t("trade.closeShift"), [
			currency_field(),
			{ fieldtype: "Int", fieldname: "sales_count", label: t("trade.salesCount"),
				read_only: 1, default: data.sales_count },
			{ fieldtype: "Currency", options: "currency", formatter: money, fieldname: "net",
				label: t("trade.net"), read_only: 1, default: data.net },
			{ fieldtype: "Column Break" },
			{ fieldtype: "Int", fieldname: "refund_count", label: t("trade.refundCount"),
				read_only: 1, default: data.refund_count },
			{ fieldtype: "Currency", options: "currency", formatter: money, fieldname: "change_total",
				label: t("trade.changeTotal"), read_only: 1, default: data.change_total },
			// 对账表 / 差额 / 备注都是**整行**的东西：上面那个 `Column Break` 之后没有分组分隔，
			// 它们会掉进右边那一栏 —— 半栏宽装不下这张表，格子被裁、备注被压到最小宽度
			// （用户 2026-09-23 的截图就是这个）。加一个分组，让它们从新的一整行开始。
			{ fieldtype: "Section Break" },
			...balance_table(t, [
				{ fieldname: "expected_amount", fieldtype: "Currency", options: "currency",
					label: t("trade.expectedAmount"), in_list_view: 1, read_only: 1, columns: 2 },
				{ fieldname: "counted_amount", fieldtype: "Currency", options: "currency",
					label: t("trade.countedAmount"), in_list_view: 1, reqd: 1, columns: 2 },
				{ fieldname: "difference", fieldtype: "Currency", options: "currency",
					label: t("trade.diff"), in_list_view: 1, read_only: 1, columns: 2 },
			], rows, { method_columns: 4, with_opening: false }),
			{ fieldtype: "Currency", options: "currency", formatter: money, fieldname: "cash_diff",
				label: t("trade.diff"), read_only: 1, default: 0 },
			{ fieldtype: "Small Text", fieldname: "note", label: t("trade.note") },
		], t("trade.closeShift"), async (values, d) => {
			const counted = Object.fromEntries((values.balances || [])
				.map(row => [row.method, num(row.counted_amount)]));
			const result = await p.call(api + "api_close_shift",
				{ name: shift.shift.name, counted, note: values.note }, "POST");
			d.hide();
			p.success(t("trade.shiftClosed", p.money(result.cash_diff)));
			await (on_done ? on_done(result) : null);
		});
		widen(dialog);
		dialog.fields_dict.balances.grid.wrapper.on("change", "input", () => paint());
		dialog.set_value("cash_diff", 0);
		paint();
	}

	p.pos_shift = { open, close };
})();
