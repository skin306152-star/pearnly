(() => {
	const p = frappe.pearnly;
	p.cashier = async (page, t) => {
		const api = "pearnly_erp_portal.cashier.api.", canWrite = frappe.model.can_write("ERP Sale");
		let list, shift;
		async function refresh() {
			shift = await p.call(api + "api_current_shift");
			page.set_indicator(shift ? t("trade.shiftOpen", shift.shift.shift_seq, p.money(shift.expected_cash)) : t("trade.noShift"), shift ? "green" : "orange");
			page.clear_primary_action();
			if (canWrite) page.set_primary_action(t(shift ? "trade.checkout" : "trade.openShift"), () => shift ? checkout() : openShift());
			await list?.reload();
		}
		function openShift() {
			p.dialog(t("trade.openShift"), [
				{ fieldtype: "Data", fieldname: "currency", default: "THB", hidden: 1 },
				{ fieldtype: "Link", fieldname: "terminal", options: "ERP POS Terminal", label: t("trade.terminal") },
				{ fieldtype: "Currency", options: "currency", formatter: value => p.money(value), fieldname: "opening_float", label: t("trade.float"), default: 0 },
			], t("trade.openShift"), async (values, d) => {
				await p.call(api + "api_open_shift", { terminal: values.terminal, opening_float: values.opening_float }, "POST"); d.hide(); await refresh();
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
				{ fieldtype: "Data", fieldname: "status", label: t("trade.status"), read_only: 1, default: __(doc.status) },
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
			page, list: () => list,
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
				{ fieldname: "sold_at", label: t("trade.date") },
				{ fieldname: "sale_type", label: t("trade.kind"), format: value => __(value) },
				{ fieldname: "status", label: t("trade.status"), format: value => __(value) },
				{ fieldname: "grand_total", label: t("trade.total"), format: value => p.money(value) },
			],
		});
		if (canWrite) page.add_inner_button(t("trade.closeShift"), () => {
			if (!shift) { frappe.msgprint(t("trade.noShift")); return; }
			p.dialog(t("trade.closeShift"), [
				{ fieldtype: "Data", fieldname: "currency", default: "THB", hidden: 1 },
				{ fieldtype: "Currency", options: "currency", formatter: value => p.money(value), fieldname: "counted_cash", label: t("trade.countedCash"), reqd: 1 },
			], t("trade.closeShift"), async (values, d) => {
				await p.call(api + "api_close_shift", { name: shift.shift.name, counted_cash: values.counted_cash }, "POST");
				d.hide(); await refresh();
			});
		});
		p.add_refresh(page, refresh); await list.ready; await refresh();
	};
})();
