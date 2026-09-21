/* 库存：**原生列表**，与商品页同一套渲染器与分页，数据来自自己的接口。
 *
 * 与旧线上库存页的对应：搜索、"只看低库存或缺货"、"显示零库存"、导出、盘点都在；
 * 旧页顶上那四张统计卡片改成**页面标题下的一行**（原生 `set_title_sub`），
 * 不自绘统计块。
 *
 * 数量永远是**库存单位**（商品上的"库存单位"）—— 与落账口径同一处，不是售卖单位。
 * 成本按岗位遮蔽：服务端没给就是"未设"，不是 0。
 *
 * 文案全部来自 `scripts/i18n/messages.tsv` 的 `stock.*` 键。
 */
(() => {
	const p = frappe.pearnly;

	p.inventory = async (page, t) => {
		const api = "pearnly_erp_portal.inventory.api.";
		let list;

		const money = (value) => (value === null || value === undefined
			? t("stock.cell.notSet")
			: format_currency(value, "THB"));

		// 原生筛选字段：搜索 + 两个开关（与商品页同一种装配方式）。
		const filters = {};
		filters.search = page.add_field({
			fieldtype: "Data",
			fieldname: "search",
			label: t("stock.field.search"),
			change: () => void list?.reload(),
		});
		filters.only_low = page.add_field({
			fieldtype: "Check",
			fieldname: "only_low",
			label: t("stock.action.onlyLow"),
			change: () => void list?.reload(),
		});
		filters.include_zero = page.add_field({
			fieldtype: "Check",
			fieldname: "include_zero",
			label: t("stock.action.toggleZero"),
			change: () => void list?.reload(),
		});
		const query = () => ({
			q: filters.search.get_value() || undefined,
			only_low: filters.only_low.get_value() ? 1 : undefined,
			include_zero: filters.include_zero.get_value() ? 1 : undefined,
		});

		// 四个数（有货 SKU / 库存金额 / 低库存 / 缺货）放在标题旁那个**原生指示器**里 ——
		// 这一版 Desk 的页面模板没有副标题位（`set_title_sub` 找不到 h6，等于白写），
		// 指示器是模板里真有的那一处。四个数始终按全量算（与列表开关无关），
		// 否则"缺货 0"会骗人。
		async function refresh_totals() {
			const totals = await p.call(api + "api_stock_totals", {
				q: filters.search.get_value() || undefined,
			});
			const color = totals.out_count ? "red" : (totals.low_count ? "orange" : "green");
			page.set_indicator(t("stock.notice.totals", totals.sku_count,
				money(totals.value), totals.low_count, totals.out_count), color);
		}

		const status_label = (row) => (row.status === "low"
			? t("stock.status.lowWith", format_number(row.min_stock, null, 3))
			: t(`stock.status.${row.status}`));

		list = p.list({
			page,
			doctype: "ERP Stock Balance",
			row_actions: (row) => [{
				id: "count",
				label: t("stock.action.count"),
				action: () => open_count(row),
			}],
			load: async ({ start, page_length }) => {
				// 列表框架给的是 {start, page_length}；服务端的参数名是 start / limit。
				const [items, total] = await Promise.all([
					p.call(api + "api_stock", { start, limit: page_length, ...query() }),
					p.call(api + "api_stock_count", query()),
				]);
				return { items, total };
			},
			columns: [
				{ fieldname: "product_name", label: t("stock.col.name") },
				{ fieldname: "code", label: t("stock.col.code") },
				{ fieldname: "barcode", label: t("stock.col.barcode") },
				{ fieldname: "warehouse_name", label: t("stock.col.warehouse") },
				{ fieldname: "unit", label: t("stock.col.unit") },
				{ fieldname: "qty_on_hand", label: t("stock.col.onHand"),
					format: (value) => format_number(value, null, 3) },
				{ fieldname: "unit_cost", label: t("stock.col.cost"),
					format: (_value, row) => money(row.cost_known ? row.unit_cost : null) },
				{ fieldname: "value", label: t("stock.col.value"),
					format: (_value, row) => money(row.cost_known ? row.value : null) },
				{ fieldname: "status", label: t("stock.col.status"),
					format: (_value, row) => status_label(row) },
			],
		});

		// 盘点：系统数看得见，只填实盘数；差异由服务端落一笔 count 流水（数量收敛到实盘）。
		function open_count(row) {
			const dialog = p.dialog(
				t("stock.count.title", row.product_name),
				[
					{ fieldtype: "HTML",
						options: `<p class="text-muted">${p.escape(t("stock.count.system",
							row.warehouse_name || "", format_number(row.qty_on_hand, null, 3),
							row.unit || ""))}</p>` },
					{ fieldtype: "Float", fieldname: "counted_qty",
						label: t("stock.count.counted"), reqd: 1, precision: 3,
						default: row.qty_on_hand },
					{ fieldtype: "Data", fieldname: "reason",
						label: t("stock.count.reason") },
				],
				t("stock.action.count"),
				async (values, d) => {
					const result = await p.call(api + "api_count", {
						warehouse: row.warehouse,
						lines: JSON.stringify([{ product: row.product,
							counted_qty: values.counted_qty }]),
						reason: values.reason || undefined,
					}, "POST");
					d.hide();
					const change = (result.adjustments || [])[0] || {};
					p.success(t("stock.notice.counted", format_number(change.delta, null, 3),
						change.counted_qty === undefined
							? "" : format_number(change.counted_qty, null, 3)));
					await list.reload();
					await refresh_totals();
				},
			);
			dialog.$wrapper.find(".modal-dialog").addClass("modal-lg");
		}

		// 工具栏：刷新在导出左边；导出导的是**全部命中行**（不只是当前这一页）。
		page.add_inner_button(t("stock.action.reload"), async () => {
			await list.reload();
			await refresh_totals();
		});
		page.add_inner_button(t("stock.action.export"), () => {
			const params = new URLSearchParams();
			for (const [key, value] of Object.entries(query())) {
				if (value !== undefined) params.set(key, value);
			}
			window.open(frappe.utils.get_url(
				`/api/method/${api}api_stock_export?${params.toString()}`));
		});

		await list.ready;
		await refresh_totals();
	};
})();
