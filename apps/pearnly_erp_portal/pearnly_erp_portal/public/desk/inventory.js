/* 库存：**原生列表**，与商品页同一套渲染器与分页，数据来自自己的接口。
 *
 * 与旧线上库存页的对应：搜索、"只看低库存或缺货"、"显示零库存"、导出都在；
 * 旧页顶上那四张统计卡片改成**页面标题下的一行**（原生 `set_title_sub`），
 * 不自绘统计块。
 *
 * 行上的"盘点"入口取消了（用户 2026-09-22 定）：盘点归盘点那一页，库存页只看数。
 *
 * 数量永远是**库存单位**（商品上的"库存单位"）—— 与落账口径同一处，不是售卖单位。
 * 成本按岗位遮蔽：服务端没给就是"未设"，不是 0。
 *
 * 文案全部来自 `pearnly_erp_portal/i18n/messages.tsv` 的 `stock.*` 键。
 */
(() => {
	const p = frappe.pearnly;

	p.inventory = async (page, t) => {
		const api = "pearnly_erp_portal.inventory.api.";
		let list;

		const money = (value) => (value === null || value === undefined
			? t("stock.cell.notSet")
			: p.money(value));

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
			? t("stock.status.lowWith", p.number(row.min_stock))
			: t(`stock.status.${row.status}`));

		// 库存行是流水推出来的结存，没有"删除"这一说（删掉就等于改账）。选中一行时给的是
		// **对应的那一步**：打开这条商品×仓库×仓位的收发流水（跟点行进去是同一个视图）。
		const selection = p.selection_action({
			page, list: () => list,
			label: () => t("stock.action.ledger"),
			when: (_row, rows) => rows.length === 1,
			reason: () => t("stock.action.ledgerSingle"),
			run: async rows => { void p.stockLedger(rows[0]); },
		});
		list = p.list({
			page,
			doctype: "ERP Stock Balance",
			selectable: true,
			on_selection: selection,
			on_open: row => p.stockLedger(row),
			// 十列要**一屏放得下**、不用横向滑（用户 2026-09-23："内容太多，要滑动才能看得到，
			// 看下能不能放在一屏显示完整"）。`compact_columns` 让每列按下面的宽度走、并按窗口
			// 富余空间等比放宽；`min_width` 是底线，窄到放不下时才会回落到框架的横向滚动。
			compact_columns: true,
			load: async ({ start, page_length }) => {
				// 列表框架给的是 {start, page_length}；服务端的参数名是 start / limit。
				const [items, total] = await Promise.all([
					p.call(api + "api_stock", { start, limit: page_length, ...query() }),
					p.call(api + "api_stock_count", query()),
				]);
				return { items, total };
			},
			columns: [
				// 十列的底线加起来要能塞进最窄的常见窗口：否则最右那几列会被右边那块（计数）
				// 盖住、或者要横向滑才看得到（用户 2026-09-23 报的就是这两件事）。
				{ fieldname: "product_name", width: 160, min_width: 132, label: t("stock.col.name") },
				{ fieldname: "code", width: 80, min_width: 68, label: t("stock.col.code") },
				{ fieldname: "barcode", width: 110, min_width: 84, label: t("stock.col.barcode") },
				{ fieldname: "warehouse_name", width: 116, min_width: 92, label: t("stock.col.warehouse") },
				{ fieldname: "location", width: 56, min_width: 46, label: t("stock.count.location") },
				{ fieldname: "unit", width: 50, min_width: 44, label: t("stock.col.unit") },
				{ fieldname: "qty_on_hand", width: 60, min_width: 50, label: t("stock.col.onHand"),
					format: (value) => p.number(value) },
				{ fieldname: "unit_cost", width: 78, min_width: 64, label: t("stock.col.cost"),
					format: (_value, row) => money(row.cost_known ? row.unit_cost : null) },
				{ fieldname: "value", width: 82, min_width: 68, label: t("stock.col.value"),
					format: (_value, row) => money(row.cost_known ? row.value : null) },
				{ fieldname: "status", width: 68, min_width: 58, label: t("stock.col.status"),
					format: (_value, row) => status_label(row) },
			],
		});

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
			window.location.assign(
				`/api/method/${api}api_stock_export?${params.toString()}`);
		});

		await list.ready;
		await refresh_totals();
	};
})();
