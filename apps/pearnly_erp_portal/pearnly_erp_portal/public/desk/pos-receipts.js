/* 收银记录：所有班次的收银小票（对齐 ERPNext 侧栏里的 "POS Receipts"）。
 *
 * 与「收银 POS」页的分工：那一页看**当前班**的小票（收银员视角，带收银台与键盘），
 * 这一页看**全部班次**的历史小票（老板对账视角），按日期/类型/状态筛。点一行开的还是
 * 那个小票查看器（`p.trade.receipt_view`），有权限时同一处能退货退款或作废。
 *
 * 导出不放在这一页：导出走销售记录那张表（POS 小票已经并进那份混合列表了），
 * 两处各导一份必然对不上。
 */
(() => {
	const p = frappe.pearnly;
	p.pos_receipts = async (page, t) => {
		const api = "pearnly_erp_portal.cashier.api.";
		const can_write = frappe.model.can_write("ERP Sale");
		const kind_label = value => t(`trade.kind.${value}`);
		const state_label = value => t(`trade.state.${value}`);
		const f = (fieldname, label, fieldtype = "Data", options = {}) =>
			({fieldname, label: __(label), fieldtype, ...options});
		let list;

		// 小票查看器 + 两个动作（与收银页同一套：退货退款、作废）；动作完刷新本页。
		async function open_receipt(row) {
			await p.trade.receipt_view(row.name, t, can_write ? {
				refund: (doc, parent) => refund(doc, parent),
				void: async (doc, parent) => {
					await p.call(api + "api_void_sale", { name: doc.name }, "POST");
					// 作废 = 官方那套里的"取消"动作，给的是 `cancel` 那一声（`form.js` 取消成功同音）。
					frappe.utils.play_sound("cancel");
					parent?.hide?.();
					await list?.reload();
				},
			} : {});
		}

		function refund(doc, parent) {
			const request = crypto.randomUUID();
			let d;
			const fields = [
				{ fieldtype: "Data", fieldname: "currency", default: "THB", hidden: 1 },
				{ fieldtype: "Select", fieldname: "original_line", label: t("trade.originalLine"), reqd: 1,
					options: doc.lines.map(line => ({ value: line.name,
						label: `${line.description || line.product} · ${line.warehouse_name || ""} · ${p.number(line.qty)}` })) },
				{ fieldtype: "Float", fieldname: "qty", label: t("trade.qty"), reqd: 1, default: 1 },
				{ fieldtype: "Select", fieldname: "method", label: t("trade.method"),
					options: "cash\ncard\npromptpay\ntransfer", default: "cash", reqd: 1 },
				{ fieldtype: "Currency", options: "currency", formatter: value => p.money(value),
					fieldname: "amount", label: t("trade.refundAmount"), reqd: 1 },
			];
			const calculate = () => {
				if (!d) return;
				const line = doc.lines.find(item => item.name === d.get_value("original_line"));
				if (line) {
					d.set_value("amount", Math.round(Number(line.line_total)
						* Number(d.get_value("qty") || 0) / Number(line.qty) * 100) / 100);
				}
			};
			fields.find(item => item.fieldname === "original_line").change = calculate;
			fields.find(item => item.fieldname === "qty").change = calculate;
			d = p.dialog(t("trade.refund"), fields, t("trade.refund"), async (values, form) => {
				await p.call(api + "api_refund_sale", { name: doc.name,
					lines: [{ original_line: values.original_line, qty: values.qty }],
					payments: [{ method: values.method, amount: values.amount }],
					client_uuid: request }, "POST");
				form.hide(); parent?.hide?.();
				await list?.reload();
			});
			calculate();
		}

		page.set_title(__("nav.posReceipts"));
		p.breadcrumbs({ label: __("nav.posReceipts"), route: p.subroute("pearnly-pos-receipts") });
		// 勾选若干张小票 → 右上角「作废选中的 N 张」：走的就是单张那条服务路径
		// （`api_void_sale`，作废会把库存还回去、单据留痕），所以批量与单张结果一致。
		// 只有"已完成"的小票能作废 —— 已作废的点不动，按钮会禁用并把原因写在 title 上。
		const selection = p.selection_action({
			page, list: () => list,
			label: rows => t("trade.action.voidCount", rows.length),
			when: row => row.status === "completed",
			reason: () => t("trade.voidBlocked"),
			confirm: rows => t("trade.voidConfirmCount", rows.length),
			run: async rows => {
				for (const row of rows) {
					await p.call(api + "api_void_sale", { name: row.name }, "POST");
				}
			},
			success: rows => t("trade.voidNotice", rows.length),
			// 作废是"取消"类动作，官方放 `cancel`（不是"保存成功"那一声）。
			sound: "cancel",
		});
		const search = page.add_field({ fieldname: "q", label: __("Search"), fieldtype: "Data",
			change: () => void list?.reload() });
		// 筛选是**官方原生**那套：字段在下面 `filters` 里声明，条件在这里翻成接口参数
		// （`sold_at` 的 Between 给 [从, 到]）。
		const query = () => {
			const picked = p.filter_values(list);
			const [from, to] = picked.sold_at || [];
			return {q: search.get_value() || undefined,
				date_from: from || undefined, date_to: to || undefined,
				sale_type: picked.sale_type || undefined, status: picked.status || undefined};
		};
		list = p.list({ page, doctype: "ERP Sale", compact_columns: true,
			// 本页支持的筛选条件（官方的字段下拉里只列这三个）。
			filter_fields: [
				{ fieldname: "sold_at", label: t("trade.date"), fieldtype: "Datetime" },
				{ fieldname: "sale_type", label: t("trade.kind"), fieldtype: "Select" },
				{ fieldname: "status", label: t("trade.status"), fieldtype: "Select" },
			],
			on_selection: can_write ? selection : undefined,
			on_open: row => void open_receipt(row),
			load: p.trade.load(api, "api_list_sales", "api_sale_total", query),
			columns: [
				// 八列的底线要能塞进最窄的常见窗口，否则最右那两列会被右边那块（计数）盖住。
				{ fieldname: "receipt_no", width: 150, min_width: 126, label: t("trade.docNo") },
				{ fieldname: "sold_at", width: 122, min_width: 100, label: t("trade.date"),
					format: value => p.datetime_text(value) },
				{ fieldname: "sale_type", width: 88, min_width: 68, label: t("trade.kind"),
					format: value => kind_label(value) },
				{ fieldname: "status", width: 82, min_width: 62, label: t("trade.status"),
					format: value => state_label(value) },
				// 这一单挂的客户（散客留空）—— 收银选的客户在这里也看得到，和主档是同一个。
				{ fieldname: "customer_name", width: 140, min_width: 96, label: t("trade.customer"),
					format: value => p.escape(value || "—") },
				{ fieldname: "grand_total", width: 96, min_width: 78, label: t("trade.total"),
					format: value => p.money(value) },
				{ fieldname: "paid_total", width: 96, min_width: 78, label: t("trade.paid"),
					format: value => p.money(value) },
				{ fieldname: "shift", width: 140, min_width: 96, label: t("trade.shiftNo") },
			],
		});
		p.add_refresh(page, () => void list?.reload());
		await list.ready;
	};
})();
