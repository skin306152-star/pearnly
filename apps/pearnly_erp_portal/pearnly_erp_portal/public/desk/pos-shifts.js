/* 交班记录：所有班次（对齐 ERPNext 侧栏里的 "POS Shift Records" → POS Closing Entry）。
 *
 * 原来这些只在收银页的一个弹窗里（「班次记录」），现在给它一个自己的入口 —— 老板对账
 * 时不用先进收银台。列表是原生的，点一行看那一班的当班汇总（笔数、退款、各收款方式、
 * 找零、应有现金、实点、差额），与收银页交班弹窗同一套口径（`shifts.summary`）。
 */
(() => {
	const p = frappe.pearnly;
	p.pos_shifts = async (page, t) => {
		const api = "pearnly_erp_portal.cashier.api.";
		const money = value => p.money(value);
		const num = value => {
			const parsed = Number(String(value ?? "").replace(/,/g, ""));
			return isFinite(parsed) ? parsed : 0;
		};
		const state_label = value => t(`trade.state.${value}`);
		const amount = (fieldname, label, value) => ({ fieldtype: "Currency", options: "currency",
			formatter: money, fieldname, label, read_only: 1, default: value });
		let list;

		// 勾选班次 → 右上角「导出选中的 N 个班次」：对账用。官方的列表把「导出」放在操作菜单里
		// （导整张列表）；我们按勾选的那几个导。取数由服务端 `shifts.list_shifts` 兜着，
		// 勾了别人的班次也只导出自己看得到的那几行。
		const export_shifts = names => {
			const query = new URLSearchParams({names: JSON.stringify(names)});
			window.open(`/api/method/${api}api_shifts_export?${query}`, "_blank", "noopener,noreferrer");
		};
		const selection = p.selection_action({
			page, list: () => list,
			label: rows => t("trade.action.exportCount", rows.length),
			run: rows => export_shifts(rows.map(row => row.name)),
			success: rows => t("trade.exportNotice", rows.length),
		});

		// 一个班的汇总（只读）：与收银页交班弹窗读的是同一个接口，所以两处数字一定一致。
		async function summary_of(row) {
			const data = await p.call(api + "api_shift_summary", { name: row.name });
			const by_method = data.by_method || {};
			// 差额取这一行的实点 − 应有（`shifts.summary` 不给差额，交班时才落库到班次上）。
			const diff = Math.round((num(row.counted_cash) - num(row.expected_cash)) * 100) / 100;
			const labels = { cash: "trade.methodCash", card: "trade.methodCard", promptpay: "trade.methodPromptpay",
				transfer: "trade.methodTransfer" };
			const shown = (data.balances || []).map(balance => balance.method).filter(method => labels[method]);
			const method_fields = (shown.length ? shown : Object.keys(labels))
				.map(method => amount(`${method}_total`, t(labels[method]), by_method[method] || 0));
			const dialog = p.dialog(`${t("trade.shiftNo")} ${row.name}`, [
				{ fieldtype: "Data", fieldname: "terminal", label: t("trade.terminal"), read_only: 1,
					default: row.terminal || "" },
				{ fieldtype: "Data", fieldname: "cashier", label: t("trade.cashier"), read_only: 1,
					default: frappe.user_info(row.cashier).fullname || row.cashier || "" },
				amount("opening_float", t("trade.float"), data.shift.opening_float),
				amount("expected_cash", t("trade.expectedCash"), data.expected_cash),
				{ fieldtype: "Column Break" },
				{ fieldtype: "Int", fieldname: "sales_count", label: t("trade.salesCount"), read_only: 1,
					default: data.sales_count },
				{ fieldtype: "Int", fieldname: "refund_count", label: t("trade.refundCount"), read_only: 1,
					default: data.refund_count },
				amount("change_total", t("trade.changeTotal"), data.change_total),
				amount("counted_cash", t("trade.countedCash"), row.counted_cash),
				{ fieldtype: "Section Break", label: t("trade.byMethod") },
				// 只列公司启用的、或这一班实际收过的方式（与交班对账表同一份，录制问题 #19）。
				...method_fields.flatMap((field, index) => index === 2 ? [{ fieldtype: "Column Break" }, field] : [field]),
				// 不再加一节标题：下面只有一格，写"差额"两遍反而像两件事。
				amount("cash_diff", t("trade.diff"), diff),
			], t("trade.close"), async (_values, d) => d.hide());
			// 差额不为 0 就标红（与列表、交班弹窗同一个红）。
			dialog.fields_dict.cash_diff?.$wrapper
				?.find("input, .control-value, .like-disabled-input")
				.toggleClass("pearnly-payment-due", Boolean(diff));
		}

		page.set_title(__("nav.posShifts"));
		p.breadcrumbs({ label: __("nav.posShifts"), route: p.subroute("pearnly-pos-shifts") });
		list = p.list({ page, doctype: "ERP Shift", compact_columns: true,
			on_selection: selection,
			on_open: row => void summary_of(row),
			// 列头点一下排序：一次取全，排完再切这一页（同 core `p.sort_rows`）。
			sort_fields: ["name", "terminal", "cashier", "status", "opened_at", "closed_at",
				"expected_cash", "counted_cash", "cash_diff"],
			load: async ({ start, page_length, sort_by, sort_order }) => {
				const rows = await p.call(api + "api_list_shifts", { limit: 0 });
				const sorted = p.sort_rows(rows, { sort_by, sort_order,
					fields: ["name", "terminal", "cashier", "status", "opened_at", "closed_at",
						"expected_cash", "counted_cash", "cash_diff"] });
				return { items: sorted.slice(start, start + page_length), total: sorted.length };
			},
				columns: [
				// 九列的底线要能塞进最窄的常见窗口，否则最右那几列会被右边那块（计数）盖住、
				// 或者要横向滑（用户 2026-09-23）。
				{ fieldname: "name", width: 140, min_width: 104, label: t("trade.shiftNo") },
				{ fieldname: "terminal", width: 100, min_width: 80, label: t("trade.terminal") },
				{ fieldname: "cashier", width: 120, min_width: 88, label: t("trade.cashier"),
					format: value => frappe.user_info(value).fullname || value },
				{ fieldname: "status", width: 78, min_width: 62, label: t("trade.status"),
					format: value => state_label(value) },
				{ fieldname: "opened_at", width: 122, min_width: 94, label: t("trade.openedAt"),
					format: value => p.datetime_text(value) },
				{ fieldname: "closed_at", width: 122, min_width: 94, label: t("trade.closedAt"),
					format: value => p.datetime_text(value) },
				{ fieldname: "expected_cash", width: 96, min_width: 74, label: t("trade.expectedCash"),
					format: value => money(value) },
				{ fieldname: "counted_cash", width: 96, min_width: 74, label: t("trade.countedCash"),
					format: value => money(value) },
				// 差额不为 0 标红：与收银页、交班弹窗同一个红（颜色走 `cell_class`）。
				{ fieldname: "cash_diff", width: 88, min_width: 66, label: t("trade.diff"),
					format: value => money(value),
					cell_class: value => num(value) ? "pearnly-payment-due" : "" },
			],
		});
		p.add_refresh(page, () => void list?.reload());
		await list.ready;
	};
})();
