/* 收银台「这张单据」的三个动作：打印收据 / 退货退款 / 作废（**两条路共用一份**）。
 *
 * 为什么单独一个文件：跟开班交班那一份同一个道理 —— 这三段原先写在 `desk/cashier.js`
 * 那个大闭包里，而那个闭包在对照路由分支提前 return（函数根本没定义）。
 * 搬过来的官方收银台的单据汇总也要这三颗按钮（用户 2026-09-24："这按钮留我们之前的"），
 * 所以挪到共用层，两边调同一份。
 *
 * 三件事的**产品口径**（都不是官方那套）：
 *   · 打印收据 = 框架原生打印视图（`/printview`，我们自己的小票模板还没做，先开原生那份）；
 *   · 退货退款 = 我们的 `api_refund_sale`（按原单行 + 数量退，钱按方式退）；
 *   · 作废     = 我们的 `api_void_sale`，那一声用 `cancel`（官方"取消"也是这一声）。
 *
 * 用法（调用方传自己的文案解析器 `t` 与"完事之后干什么"）：
 *   frappe.pearnly.pos_receipt.print(doc);
 *   await frappe.pearnly.pos_receipt.refund({ t, doc, on_done, methods });
 *   await frappe.pearnly.pos_receipt.void_sale({ t, doc, on_done });
 * `methods` 是**这家公司开了的那几种收款方式**（键，见 `trade.refund` 那一格）：调用方
 * 有就传（付款面板/开班对账看的也是这份），没传才四种全有。格子里的**显示名**与付款面板
 * 同一份词条（`trade.methodCash` …），不把 `cash` 这种键面直接摆给收银员看。
 * `doc` 要是**服务端那一份**（`api_get_sale`），因为它按 `lines[].name / line_total / qty /
 * warehouse_name` 取数；客户端那张单据的行上没有这些字段。
 */
frappe.provide("frappe.pearnly.pos_receipt");

(() => {
	const p = frappe.pearnly;
	const api = "pearnly_erp_portal.cashier.api.";
	// 与 `ERP Sale Payment.method` 同一个集合（`cashier/contracts.py` 的 `PAYMENT_METHODS`）。
	// 这是**兜底**：调用方给了 `methods` 就用它（= 这家公司开了的那几种，见公司上的收款方式），
	// 没给才四种全有 —— 与付款面板、开班对账同一口径。
	const METHODS = ["cash", "card", "promptpay", "transfer"];
	const METHOD_TERMS = {cash: "trade.methodCash", card: "trade.methodCard",
		promptpay: "trade.methodPromptpay", transfer: "trade.methodTransfer"};
	const methods_of = (methods) => (Array.isArray(methods) && methods.length ? methods : METHODS);
	const num = value => {
		const parsed = Number(String(value ?? "").replace(/,/g, ""));
		return isFinite(parsed) ? parsed : 0;
	};
	const round2 = value => Math.round(num(value) * 100) / 100;
	const money = value => p.money(value);

	// 「打印收据」= 框架原生打印视图（官方出小票也走这一条）。
	function print(doc) {
		if (!doc || !doc.name) return;
		const url = "/printview?doctype=" + encodeURIComponent(doc.doctype || "ERP Sale")
			+ "&name=" + encodeURIComponent(doc.name) + "&trigger_print=1";
		window.open(url, "_blank", "noopener,noreferrer");
	}

	async function void_sale({ t, doc, on_done } = {}) {
		await p.call(api + "api_void_sale", { name: doc.name }, "POST");
		// 作废 = 官方那套里的"取消"动作：`cancel` 那一声（`cancel` 也是框架取消文档成功的音）。
		p.success(t("trade.voidNotice", 1), "cancel");
		await (on_done ? on_done() : null);
	}

	function refund({ t, doc, on_done, methods } = {}) {
		const request = crypto.randomUUID();
		let d;
		const fields = [
			{ fieldtype: "Data", fieldname: "currency",
				default: frappe.boot.sysdefaults.currency || "THB", hidden: 1 },
			{ fieldtype: "Select", fieldname: "original_line", label: t("trade.originalLine"), reqd: 1,
				options: (doc.lines || []).map(row => ({ value: row.name,
					label: `${row.description || row.product} · ${row.warehouse_name || ""} · ${p.number(row.qty)}` })) },
			{ fieldtype: "Float", fieldname: "qty", label: t("trade.qty"), reqd: 1, default: 1 },
			{ fieldtype: "Select", fieldname: "method", label: t("trade.method"),
				options: methods_of(methods).map(key => ({
					value: key, label: t(METHOD_TERMS[key] || key) })),
				default: methods_of(methods)[0], reqd: 1 },
			{ fieldtype: "Currency", options: "currency", formatter: money, fieldname: "amount",
				label: t("trade.refundAmount"), reqd: 1 },
		];
		const calculate = () => {
			if (!d) return;
			const line = (doc.lines || []).find(row => row.name === d.get_value("original_line"));
			if (line) d.set_value("amount", round2(num(line.line_total) * num(d.get_value("qty") || 0) / num(line.qty)));
		};
		fields.find(item => item.fieldname === "original_line").change = calculate;
		fields.find(item => item.fieldname === "qty").change = calculate;
		d = p.dialog(t("trade.refund"), fields, t("trade.refund"), async (values, dialog) => {
			await p.call(api + "api_refund_sale", { name: doc.name,
				lines: [{ original_line: values.original_line, qty: values.qty }],
				payments: [{ method: values.method, amount: values.amount }],
				client_uuid: request }, "POST");
			dialog.hide();
			p.success(t("trade.refund"));
			await (on_done ? on_done() : null);
		});
		calculate();
		return d;
	}

	p.pos_receipt = { print, refund, void_sale };
})();
