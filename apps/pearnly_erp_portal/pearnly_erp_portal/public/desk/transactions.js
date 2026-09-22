/* Shared business line fields, composed from native Dialog/Grid controls. */
(() => {
	const p = frappe.pearnly;
	p.trade = {
		// 收银小票的只读查看器：销售记录里点到"来源=POS"的行，或收银页点小票，都开这一个。
		// `actions` 由调用方给（收银页给「退货退款 / 作废」，销售记录只读打开时不给）。
		async receipt_view(name, t, actions = {}) {
			const api = "pearnly_erp_portal.cashier.api.";
			const doc = await p.call(api + "api_get_sale", { name });
			const fields = [
				{ fieldtype: "Data", fieldname: "currency", default: "THB", hidden: 1 },
				{ fieldtype: "Data", fieldname: "status", label: t("trade.status"), read_only: 1,
					default: t(`trade.state.${doc.status}`) },
				// 明细表整表只读：只给内层 `cannot_add_rows/cannot_delete_rows` 的话，弹窗里的
				// 原生表格照样会画出"添加行 / 删除 / 上传 / 批量添加"那一排（实测），点开一张
				// 已卖出的小票不该能改行，所以再加一层字段级 `read_only`。
				{ ...p.trade.lines(t, { readonly: true, data: doc.lines }), read_only: 1 },
				{ fieldtype: "Currency", options: "currency", formatter: value => p.money(value),
					fieldname: "total", label: t("trade.total"), read_only: 1, default: doc.grand_total },
			];
			let dialog;
			if (doc.sale_type === "sale" && actions.refund) {
				fields.push({ fieldtype: "Button", fieldname: "refund", label: t("trade.refund"),
					click: () => actions.refund(doc, dialog) });
			}
			if (actions.void) {
				fields.push({ fieldtype: "Button", fieldname: "void", label: t("trade.void"),
					click: () => frappe.confirm(t("trade.voidConfirm"),
						() => p.trade.act(dialog, () => actions.void(doc, dialog))) });
			}
			dialog = p.dialog(doc.receipt_no, fields, t("trade.close"), async (_values, d) => d.hide());
			dialog.$wrapper.find(".modal-dialog").addClass("modal-xl");
			return doc;
		},
		load(api, method, totalMethod, query = () => ({})) {
			return async ({ start, page_length }) => {
				const [items, total] = await Promise.all([
					p.call(api + method, { ...query(), start, limit: page_length }),
					p.call(api + totalMethod, query()),
				]); return { items, total };
			};
		},
		async texts() { return p.texts("trade."); },
		// `locked_price`：只锁单价那一格，其余照常能编 —— 出库就是这样（成本由服务端按
		// 当前结存算，界面上看得到、改不了）；整表 `readonly` 是另一回事（只读查看）。
		lines(t, { cost = false, stock = false, readonly = false, locked_price = false,
			data = [], onChange = () => {} } = {}) {
			const price = cost ? "unit_cost" : "unit_price";
			return {
				fieldtype: "Table", fieldname: "lines", label: t("trade.lines"),
				reqd: 1, in_place_edit: true, cannot_add_rows: readonly, cannot_delete_rows: readonly,
				data: data.map(row => ({ currency: "THB", ...row, warehouse: row.warehouse_name || row.warehouse,
					product: readonly ? row.description || row.product_name || row.product : row.product })),
				fields: [
					{ fieldtype: readonly ? "Data" : "Link", fieldname: "product", options: "ERP Product",
						label: t("trade.product"), reqd: stock, in_list_view: 1, columns: 3,
						get_query: () => ({ filters: { is_active: 1 } }),
						formatter: (value, _df, _options, row) => p.escape(row.description || row.product_name || value),
						async onchange() {
							const row = this.doc, selected = row.product;
							if (!selected || readonly) return;
							const item = await p.call("pearnly_erp_portal.products.service.api_get_product", { name: selected });
							if (row.product !== selected) return;
							Object.assign(row, { description: item.product_name, unit: item.unit || item.base_unit,
								warehouse: item.default_warehouse || "", location: item.default_location || "",
								[price]: cost ? item.default_cost : item.unit_price });
							this.grid_row.refresh();
						} },
					{ fieldtype: "Data", fieldname: "warehouse", label: t("trade.warehouse"), in_list_view: 1, columns: 2 },
					{ fieldtype: "Data", fieldname: "location", label: t("trade.location"), in_list_view: 1, columns: 2 },
					{ fieldtype: "Float", fieldname: "qty", label: t("trade.qty"), reqd: 1, default: 1, in_list_view: 1, columns: 1, formatter: value => p.number(value) },
					{ fieldtype: "Currency", fieldname: price, options: "currency", formatter: value => p.money(value), label: t(cost ? "trade.cost" : "trade.price"), in_list_view: 1, columns: 2 },
					{ fieldtype: "Data", fieldname: "currency", default: "THB", hidden: 1 },
					{ fieldtype: "Data", fieldname: "description", label: t("trade.description"), reqd: !stock },
					{ fieldtype: "Data", fieldname: "unit", label: t("trade.unit") },
					...(!stock ? [
						{ fieldtype: "Currency", fieldname: "discount", options: "currency", label: t("trade.discount"), default: 0 },
						{ fieldtype: "Percent", fieldname: "vat_rate", label: t("trade.vat"), default: 7 },
					] : []),
				 ].map(field => ({ ...field,
					read_only: readonly || (locked_price && field.fieldname === price) ? 1 : 0,
					onchange: async function () {
						await field.onchange?.call(this);
						onChange();
					},
				})),
			};
		},
		clean(lines) {
			// 空白行不发：明细表是可编辑的空表，框架会给它带一行空的（数量默认 1、没有商品、
			// 也没有文字说明）—— 那一行不是业务明细。以前它跟着请求发出去，入库/出库那边
			// 收到没有商品的明细直接抛 KeyError，用户看到的是服务器错误。采购/销售的自由行
			// （只有说明、没有商品）要留下，所以判空是"商品和说明都没有"。
			const kept = (lines || []).filter(row => row
				&& (String(row.product || "").trim() || String(row.description || "").trim()));
			return kept.map(row => Object.fromEntries([
				"product", "warehouse", "location", "description", "qty", "unit", "unit_price",
				"unit_cost", "discount", "vat_rate", "original_line",
			].filter(key => row[key] !== undefined).map(key => [key, row[key]])));
		},
		async act(dialog, callback) {
			if (dialog.pearnly_busy) return;
			dialog.pearnly_busy = true;
			dialog.$wrapper.find("button").prop("disabled", true);
			try { await callback(); }
			finally { dialog.pearnly_busy = false; dialog.$wrapper.find("button").prop("disabled", false); }
		},
	};
})();
