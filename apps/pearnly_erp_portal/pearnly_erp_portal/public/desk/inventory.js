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

	p.inventory = async (page, t, wrapper) => {
		const api = "pearnly_erp_portal.inventory.api.";
		const root = "pearnly-inventory";
		let list;
		// 接了 Express 的公司期初从 Express 读回（同步对齐），不给录期初的入口（服务端同样拒，出入库第一期）
		const can_open = () => frappe.boot.pearnly_pages?.includes("pearnly-stock-in")
			&& !p.express_company();
		async function show_list(current) {
		page.set_title(__("nav.inventory"));
		p.breadcrumbs({ label: __("nav.inventory"), route: p.subroute(root) });

		const money = (value) => (value === null || value === undefined
			? t("stock.cell.notSet")
			: p.money(value));

		// 搜索框留在页面上；**筛选走官方原生那套**（那颗 Filter 按钮 + 弹层里的条件行）。
		// 原来我们自己做的两个勾选（只看低库存或缺货 / 显示零库存）撤掉了：同样的意思用原生
		// 条件表达更直接 —— 「在库数量 <= 0」= 缺货、「在库数量 != 0」= 不显示零库存
		// （用户 2026-09-23 口径："把我们自己设计的勾选筛选取消掉，做成官方原生的显示出来"）。
		const filters = {};
		filters.search = page.add_field({
			fieldtype: "Data",
			fieldname: "search",
			label: t("stock.field.search"),
			change: () => void list?.reload(),
		});
		// 显示方式（多仓闭环 P4）：按位置 = 物料 × 仓库（11-19 取消仓位）；按物料 = 公司各仓合计，点一行回到按位置看这件物料在各仓的数
		filters.view = page.add_field({
			fieldtype: "Select",
			fieldname: "view",
			label: t("stock.field.view"),
			options: [{ value: "position", label: t("stock.view.position") },
				{ value: "product", label: t("stock.view.product") }],
			default: "position",
			// 给默认值时框架也会叫一次 change：列表还没建就不重建
			change: () => { if (list) void build_list(); },
		});
		const by_product = () => filters.view.get_value() === "product";
		let focus_product = null;  // 按物料那一行点进来：重建按位置列表后加上这件物料的筛选
		// 原生筛选条件 → 接口参数。数量那格把**比较符**一起带过去（服务端按比较符比）。
		const query = () => {
			const picked = p.filter_values(list);
			const qty = list?.filter_area?.get().find(row => row[1] === "qty_on_hand") || [];
			return {
				q: filters.search.get_value() || undefined,
				view: by_product() ? "product" : undefined,
				product: by_product() ? undefined : (picked.product || undefined),
				warehouse: picked.warehouse || undefined,
				qty_op: qty[2] || undefined,
				qty_value: qty[3] === undefined || qty[3] === "" ? undefined : qty[3],
				conditions: p.filter_conditions(list, ["qty_on_hand", "product"]),
			};
		};

		// 四个数（有货 SKU / 库存金额 / 低库存 / 缺货）放在标题旁那个**原生指示器**里 ——
		// 这一版 Desk 的页面模板没有副标题位（`set_title_sub` 找不到 h6，等于白写），
		// 指示器是模板里真有的那一处。四个数始终按全量算（与列表开关无关），
		// 否则"缺货 0"会骗人。
		async function refresh_totals() {
			const totals = await p.call(api + "api_stock_totals", {
				q: filters.search.get_value() || undefined,
			});
			if (!current()) return;
			const color = totals.out_count ? "red" : (totals.low_count ? "orange" : "green");
			page.set_indicator(t("stock.notice.totals", totals.sku_count,
				money(totals.value), totals.low_count, totals.out_count), color);
		}

		// 库存行是流水推出来的结存，没有"删除"这一说（删掉就等于改账）。选中一行时给的是
		// **对应的那一步**：打开这条商品×仓库的收发流水（跟点行进去是同一个视图）。
		const selection = p.selection_action({
			page, list: () => list,
			label: () => t("stock.action.ledger"),
			when: (_row, rows) => rows.length === 1,
			reason: () => t("stock.action.ledgerSingle"),
			run: async rows => { void p.stockLedger(rows[0]); },
		});
		// 按物料那一行没有仓库：点开 = 换回按位置、只看这件物料
		const open_product = row => {
			focus_product = row.product;
			filters.view.set_value("position");
		};
		const place_columns = ["warehouse_name"];
		async function build_list() {
			list?.destroy(); list = null;
			page.clear_primary_action();
			const product_view = by_product();
			list = p.list({
				page,
				doctype: "ERP Stock Balance",
				selectable: !product_view,
				// 本页支持的筛选条件（官方的字段下拉里只列这几个）；按物料时没有物料这一项。
				filter_fields: [
					{ fieldname: "warehouse", label: t("stock.col.warehouse"), fieldtype: "Link" },
					...(product_view ? [] : [
						{ fieldname: "product", label: t("trade.product"), fieldtype: "Link" }]),
					{ fieldname: "qty_on_hand", label: t("stock.col.onHand"), fieldtype: "Float" },
				],
				on_selection: product_view ? undefined : selection,
				on_open: row => (product_view ? open_product(row) : p.stockLedger(row)),
				// 十列要**一屏放得下**、不用横向滑（用户 2026-09-23："内容太多，要滑动才能看得到，
				// 看下能不能放在一屏显示完整"）。`compact_columns` 让每列按下面的宽度走、并按窗口
				// 富余空间等比放宽；`min_width` 是底线，窄到放不下时才会回落到框架的横向滚动。
				compact_columns: true,
				// 名称放不下就换行完整显示（11-20）；其它列按最长的值定宽、编码完整显示，富余宽度名称 2 : 其它各 1（11-22）
				wrap_subject: true,
				// 列头点一下排序（`sort_fields` 声明服务端认的字段，见 core `p.list`）。
				sort_fields: ["product_name", "internal_code", "barcode", "warehouse_name",
					"unit", "qty_on_hand"].filter(name => !product_view || !place_columns.includes(name)),
				load: async ({ start, page_length, sort_by, sort_order }) => {
					// 列表框架给的是 {start, page_length}；服务端的参数名是 start / limit。
					const [items, total] = await Promise.all([
						p.call(api + "api_stock",
							{ start, limit: page_length, sort_by, sort_order, ...query() }),
						p.call(api + "api_stock_count", query()),
					]);
					return { items, total };
				},
				columns: [
					// 十列的底线加起来要能塞进最窄的常见窗口：否则最右那几列会被右边那块（计数）
					// 盖住、或者要横向滑才看得到（用户 2026-09-23 报的就是这两件事）。
					// 品名后接「分类 · 特性/颜色」（岳父 11-01；十列放不下再加两列，搜索框也搜得到这两样）
					// 删了状态、成本、金额、仓位四栏（岳父 11-18）；名称换行完整显示（11-20）
					{ fieldname: "product_name", width: 300, min_width: 200, label: t("stock.col.name"),
						format: (value, row) => [value, row.category_name, row.attribute].filter(Boolean).join(" · ") },
					{ fieldname: "internal_code", width: 96, min_width: 80, label: t("product.col.internalCode") },
					{ fieldname: "barcode", width: 110, min_width: 84, label: t("stock.col.barcode") },
					{ fieldname: "warehouse_name", width: 116, min_width: 92, label: t("stock.col.warehouse") },
					{ fieldname: "unit", width: 50, min_width: 44, label: t("stock.col.unit") },
					{ fieldname: "qty_on_hand", width: 60, min_width: 50, label: t("stock.col.onHand"),
						format: (value) => p.number(value) },
				].filter(column => !product_view || !place_columns.includes(column.fieldname)),
			});
			await list.ready;
			if (focus_product && !product_view) {
				const product = focus_product;
				focus_product = null;
				await list.filter_area.add([[list.doctype, "product", "=", product]]);
			}
		}
		await build_list();

		// 工具栏：导出导的是**全部命中行**（不只是当前这一页）。
		// 小的当场下载，大的排后台、做完推送后下载（`p.export_file`）。
		page.add_inner_button(t("stock.action.export"), () => void p.export_file(`${api}api_stock_export`, query()));
		if (can_open()) page.add_inner_button(t("stock.opening.title"), () =>
			frappe.set_route(root, "opening"));

		if (current()) await refresh_totals();
		}
		const show = p.routed_page({ wrapper, root, render: async (rest, current) => {
			list?.destroy(); list = null;
			p.clear_page(page);
			page.clear_secondary_action();
			if (!rest.length) return show_list(current);
			if (rest.length !== 1 || rest[0] !== "opening" || !can_open()) return p.redirect_up(root);
			return p.stockOpening(page, t, current);
		} });
		await show();
	};
})();
