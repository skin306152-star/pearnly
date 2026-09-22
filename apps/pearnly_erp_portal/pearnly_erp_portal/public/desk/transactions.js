/* Shared business line fields, composed from native Dialog/Grid controls. */
(() => {
	const p = frappe.pearnly;
	p.trade = {
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
