(() => {
	const p = frappe.pearnly;
	p.cashier = async (page, t) => {
		const api = "pearnly_erp_portal.cashier.api.", canWrite = frappe.model.can_write("ERP Sale");
		// 钱一律这么显示（两位小数、带币种）；读数时把千分位剥掉再转数字。
		const money = value => p.money(value);
		const num = value => {
			const parsed = Number(String(value ?? "").replace(/,/g, ""));
			return isFinite(parsed) ? parsed : 0;
		};
		// 汇总里的金额格：只读、走同一个钱格式。单据类型/状态用**本页自己的键**取词
		// （`trade.kind.*` / `trade.state.*`）—— 直接 `__("sale")` 这种会被框架自带词典
		// 先接走，或者干脆露出英文原文（见 docs/work 的 F15 那条坑）。
		const amount = (fieldname, label, value) => ({ fieldtype: "Currency", options: "currency",
			formatter: money, fieldname, label, read_only: 1, default: value });
		const kind_label = value => t(`trade.kind.${value}`);
		const state_label = value => t(`trade.state.${value}`);
		let list, shift;
		// 右上角那颗主按钮跟着班次走：没班是「开班」、有班是「收银开单」。
		// 为什么要单独一个函数：列表的选中状态一刷新就会去清主按钮（没选中 → 清），
		// 不把"班次动作"交回去的话，这颗按钮会被列表刷掉 —— 页面上根本点不到
		// （商品页当初踩的同一个坑，见 `products.js` 的 `restore_primary`）。
		function restore_primary() {
			if (!canWrite) { page.clear_primary_action(); return; }
			page.set_primary_action(t(shift ? "trade.checkout" : "trade.openShift"),
				() => (shift ? checkout() : openShift()));
		}
		async function refresh() {
			shift = await p.call(api + "api_current_shift");
			page.set_indicator(shift ? t("trade.shiftOpen", shift.shift.shift_seq, p.money(shift.expected_cash)) : t("trade.noShift"), shift ? "green" : "orange");
			restore_primary();
			await list?.reload();
		}
		// 开班：收银台默认带出本店那个默认台（与服务端 `terminals.resolve` 同一口径），
		// 备用金默认 0。留空就用服务端的默认台 —— 新店还没建收银台时也不会卡住。
		async function openShift() {
			const terminals = await p.call(api + "api_list_terminals");
			const preferred = terminals.find(row => Number(row.is_default)) || terminals[0];
			p.dialog(t("trade.openShift"), [
				{ fieldtype: "Data", fieldname: "currency", default: "THB", hidden: 1 },
				{ fieldtype: "Link", fieldname: "terminal", options: "ERP POS Terminal", label: t("trade.terminal"),
					default: preferred?.name || "", description: t("trade.terminalHint") },
				{ fieldtype: "Currency", options: "currency", formatter: money, fieldname: "opening_float",
					label: t("trade.float"), default: 0 },
			], t("trade.openShift"), async (values, d) => {
				const result = await p.call(api + "api_open_shift",
					{ terminal: values.terminal, opening_float: values.opening_float }, "POST");
				d.hide();
				// 主按动作要有回音：提示 + 提示音（全站同一套 `p.success`）。
				p.success(t("trade.shiftOpened", result.shift_seq));
				await refresh();
			});
		}
		function checkout() {
			let d;
			const request = crypto.randomUUID();
			function recalc() {
				if (!d) return;
				const lines = d.fields_dict.lines.grid.get_data();
				const total = lines.reduce((sum, row) => sum + Math.max(0, Number(row.qty || 0) * Number(row.unit_price || 0) - Number(row.discount || 0)), 0);
				d.set_value("total", total); d.set_value("amount", total);
			}
			d = p.dialog(t("trade.checkout"), [
				{ fieldtype: "Data", fieldname: "currency", default: "THB", hidden: 1 },
				p.trade.lines(t, { stock: true, data: [{ qty: 1 }], onChange: recalc }),
				{ fieldtype: "Currency", options: "currency", formatter: value => p.money(value), fieldname: "total", label: t("trade.total"), read_only: 1, default: 0 },
				{ fieldtype: "Select", fieldname: "method", label: t("trade.method"), options: "cash\ncard\npromptpay\ntransfer", default: "cash", reqd: 1 },
				{ fieldtype: "Currency", options: "currency", formatter: value => p.money(value), fieldname: "amount", label: t("trade.paid"), reqd: 1, default: 0 },
			], t("trade.pay"), async (values, dialog) => {
				const result = await p.call(api + "api_create_sale", { shift: shift.shift.name,
					terminal: shift.shift.terminal, lines: p.trade.clean(values.lines), has_vat: 0,
					payments: [{ method: values.method, amount: values.amount }], client_uuid: request }, "POST");
				dialog.hide(); p.success(t("trade.paidNotice", result.receipt_no || result.name, p.money(result.change_amount)));
				await refresh();
			});
			d.$wrapper.find(".modal-dialog").addClass("modal-xl");
		}
		async function receipt(name) {
			const doc = await p.call(api + "api_get_sale", { name });
			let d;
			const fields = [
				{ fieldtype: "Data", fieldname: "currency", default: "THB", hidden: 1 },
				{ fieldtype: "Data", fieldname: "status", label: t("trade.status"), read_only: 1, default: state_label(doc.status) },
				p.trade.lines(t, { readonly: true, data: doc.lines }),
				{ fieldtype: "Currency", options: "currency", formatter: value => p.money(value), fieldname: "total", label: t("trade.total"), read_only: 1, default: doc.grand_total },
			];
			if (doc.status === "completed" && canWrite) {
				if (doc.sale_type === "sale") fields.push({ fieldtype: "Button", fieldname: "refund", label: t("trade.refund"), click: () => refund(doc, d) });
				fields.push({ fieldtype: "Button", fieldname: "void", label: t("trade.void"), click: () => frappe.confirm(t("trade.voidConfirm"), () => p.trade.act(d, async () => {
					await p.call(api + "api_void_sale", { name }, "POST"); d.hide(); await refresh(); await receipt(name);
				})) });
			}
			d = p.dialog(doc.receipt_no, fields, t("trade.close"), async (_values, dialog) => dialog.hide());
			d.$wrapper.find(".modal-dialog").addClass("modal-xl");
		}
		function refund(doc, parent) {
			const request = crypto.randomUUID();
			const fields = [
				{ fieldtype: "Data", fieldname: "currency", default: "THB", hidden: 1 },
				{ fieldtype: "Select", fieldname: "original_line", label: t("trade.originalLine"), reqd: 1,
					options: doc.lines.map(row => ({ value: row.name, label: `${row.description || row.product} · ${row.warehouse_name || ""} · ${row.location || ""} · ${p.number(row.qty)}` })) },
				{ fieldtype: "Float", fieldname: "qty", label: t("trade.qty"), reqd: 1, default: 1 },
				{ fieldtype: "Select", fieldname: "method", label: t("trade.method"), options: "cash\ncard\npromptpay\ntransfer", default: "cash", reqd: 1 },
				{ fieldtype: "Currency", options: "currency", formatter: value => p.money(value), fieldname: "amount", label: t("trade.refundAmount"), reqd: 1 },
			];
			let d;
			const calculate = () => {
				if (!d) return;
				const line = doc.lines.find(row => row.name === d.get_value("original_line"));
				if (line) d.set_value("amount", Math.round(Number(line.line_total) * Number(d.get_value("qty") || 0) / Number(line.qty) * 100) / 100);
			};
			fields.find(field => field.fieldname === "original_line").change = calculate;
			fields.find(field => field.fieldname === "qty").change = calculate;
			d = p.dialog(t("trade.refund"), fields, t("trade.refund"), async (values, dialog) => {
				await p.call(api + "api_refund_sale", { name: doc.name, lines: [{ original_line: values.original_line, qty: values.qty }],
					payments: [{ method: values.method, amount: values.amount }], client_uuid: request }, "POST");
				dialog.hide(); parent.hide(); await refresh(); await receipt(doc.name);
			});
			calculate();
		}
		// 小票不允许删除（退货要留痕），所以选中一条已完成的小票时给的是**对应那一步**：
		// 打开退货退款弹窗（与从小票弹窗里点"退货退款"是同一条路径）。
		const selection = p.selection_action({
			page, list: () => list, empty: restore_primary,
			label: () => t("trade.refund"),
			when: (row, rows) => rows.length === 1 && row.status === "completed"
				&& row.sale_type === "sale",
			reason: (_row, rows) => (rows.length === 1 ? t("trade.refundBlocked")
				: t("trade.refundSingle")),
			run: async rows => {
				const doc = await p.call(api + "api_get_sale", { name: rows[0].name });
				refund(doc, { hide() {} });
			},
		});
		list = p.list({ page, doctype: "ERP Sale", selectable: true, on_selection: selection,
			on_open: row => receipt(row.name),
			load: p.trade.load(api, "api_list_sales", "api_sale_total"),
			columns: [
				{ fieldname: "receipt_no", label: t("trade.docNo") },
				// 收银单据时间是 Datetime（库里带微秒），跟产品里其它时间一样显示到分钟。
				{ fieldname: "sold_at", label: t("trade.date"), format: value => p.datetime_text(value) },
				{ fieldname: "sale_type", label: t("trade.kind"), format: value => kind_label(value) },
				{ fieldname: "status", label: t("trade.status"), format: value => state_label(value) },
				{ fieldname: "grand_total", label: t("trade.total"), format: value => p.money(value) },
			],
		});
		// 交班：先把**当班汇总**摆出来（笔数、退款、各收款方式、找零、应有现金），再填实点
		// 现金；差额 = 实点 − 应有，边填边算、不为 0 就标红。交班的人不用自己算账，也不用
		// 事先知道系统记了多少 —— 数字和差额在同一张弹窗里对着看（改前只让填一个实点数）。
		function hand_over() {
			if (!shift) { frappe.msgprint(t("trade.noShift")); return; }
			const data = shift, by_method = data.by_method || {};
			let dialog;
			const paint = () => {
				if (!dialog) return;
				const diff = Math.round((num(dialog.get_value("counted_cash")) - num(data.expected_cash)) * 100) / 100;
				dialog.set_value("cash_diff", diff);
				// 不为 0 就用列表里那个红（"钱没到位"同一个色），并在下面写清是多钱还是短钱。
				// 只读的金额格框架渲染成 `control-value` 而不是 `input`，所以按容器找，别取 `$input`。
				dialog.fields_dict.cash_diff?.$wrapper
					?.find("input, .control-value, .like-disabled-input")
					.toggleClass("pearnly-payment-due", Boolean(diff));
				dialog.set_df_property("cash_diff", "description",
					diff === 0 ? t("trade.diffEven") : diff > 0 ? t("trade.diffOver") : t("trade.diffShort"));
			};
			dialog = p.dialog(t("trade.closeShift"), [
				{ fieldtype: "Int", fieldname: "sales_count", label: t("trade.salesCount"), read_only: 1, default: data.sales_count },
				amount("gross", t("trade.gross"), data.gross),
				{ fieldtype: "Column Break" },
				{ fieldtype: "Int", fieldname: "refund_count", label: t("trade.refundCount"), read_only: 1, default: data.refund_count },
				amount("net", t("trade.net"), data.net),
				{ fieldtype: "Section Break", label: t("trade.byMethod") },
				amount("cash_total", t("trade.methodCash"), by_method.cash || 0),
				amount("card_total", t("trade.methodCard"), by_method.card || 0),
				{ fieldtype: "Column Break" },
				amount("promptpay_total", t("trade.methodPromptpay"), by_method.promptpay || 0),
				amount("transfer_total", t("trade.methodTransfer"), by_method.transfer || 0),
				{ fieldtype: "Section Break", label: t("trade.countCash") },
				amount("opening_float", t("trade.float"), data.shift.opening_float),
				amount("change_total", t("trade.changeTotal"), data.change_total),
				{ fieldtype: "Column Break" },
				amount("expected_cash", t("trade.expectedCash"), data.expected_cash),
				{ fieldtype: "Currency", options: "currency", formatter: money, fieldname: "counted_cash",
					label: t("trade.countedCash"), reqd: 1, default: data.expected_cash, change: paint },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Currency", options: "currency", formatter: money, fieldname: "cash_diff",
					label: t("trade.diff"), read_only: 1, default: 0 },
			], t("trade.closeShift"), async (values, d) => {
				const result = await p.call(api + "api_close_shift",
					{ name: data.shift.name, counted_cash: values.counted_cash }, "POST");
				d.hide();
				// 交班也算主按动作：给提示和提示音，并把差额直接说在提示里。
				p.success(t("trade.shiftClosed", p.money(result.cash_diff)));
				await refresh();
			});
			dialog.$wrapper.find(".modal-dialog").addClass("modal-xl");
			paint();
		}
		// 班次记录：近 50 个班，谁开的、什么时候交的、备用金/应有/实点/差额各是多少。
		// 列与弹窗都走原生（`p.table` 是官方 DataTable），差额标记与列表同一个红。
		async function shift_history() {
			const dialog = p.dialog(t("trade.shiftHistory"), [
				{ fieldtype: "HTML", fieldname: "history" },
			], t("trade.close"), async (_values, d) => d.hide());
			dialog.$wrapper.find(".modal-dialog").addClass("modal-xl");
			const blank = "—";
			const due = value => `<span class="pearnly-payment-due">${value}</span>`;
			const columns = [
				{ name: t("trade.shiftNo"), width: 140, format: p.escape },
				{ name: t("trade.terminal"), width: 130, format: p.escape },
				{ name: t("trade.cashier"), width: 150, format: p.escape },
				{ name: t("trade.status"), width: 90, format: p.escape },
				{ name: t("trade.openedAt"), width: 130, format: p.escape },
				{ name: t("trade.closedAt"), width: 130, format: p.escape },
				{ name: t("trade.float"), width: 90, format: p.escape },
				{ name: t("trade.expectedCash"), width: 105, format: p.escape },
				{ name: t("trade.countedCash"), width: 105, format: p.escape },
				// 这一列自己拼红字，所以不走默认转义（与盘点工作台的差异列同一做法）。
				{ name: t("trade.diff"), width: 100, format: value => value },
			];
			let table;
			// 表格要等弹窗**真的显示出来**再画：原生表格构建时会往容器里插一个 `<style>`，
			// 容器还没接上文档时拿不到 `sheet`，整张表就画不出来（盘点明细弹窗踩过同一个坑）。
			// 用框架自己的 `on_page_show`（`shown.bs.modal` 时触发），不靠定时器。
			dialog.on_page_show = async () => {
				if (table) return;
				const rows = await p.call(api + "api_list_shifts", { limit: 200 });
				table = await p.table(dialog.fields_dict.history.$wrapper, columns, rows.map(row => {
					const closed = row.status === "closed";
					const diff = num(row.cash_diff);
					const shown = value => p.escape(money(value));
					return [
						p.escape(row.name), p.escape(row.terminal || blank),
						p.escape(frappe.user_info(row.cashier).fullname || row.cashier || blank),
						p.escape(t(`trade.state.${row.status}`)),
						p.escape(p.datetime_text(row.opened_at) || blank),
						p.escape(closed ? p.datetime_text(row.closed_at) || blank : blank),
						shown(row.opening_float),
						closed ? shown(row.expected_cash) : blank,
						closed ? shown(row.counted_cash) : blank,
						closed ? (diff ? due(shown(diff)) : shown(diff)) : blank,
					];
				}));
			};
		}
		if (canWrite) page.add_inner_button(t("trade.closeShift"), hand_over);
		page.add_inner_button(t("trade.shiftHistory"), () => void shift_history());
		p.add_refresh(page, refresh); await list.ready; await refresh();
	};
})();
