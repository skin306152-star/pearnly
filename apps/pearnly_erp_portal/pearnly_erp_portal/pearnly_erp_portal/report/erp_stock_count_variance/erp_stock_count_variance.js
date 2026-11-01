/* 盘点差异看板的筛选与图表。
 *
 * 报表本身（列、数据）在 erp_stock_count_variance.py；这里只放框架约定的两块：
 * 筛选字段与 `get_chart_data`（框架的报表页把图表画在上面、表格在下面）。
 *
 * 文案一律用 `__()` 取**我们语言包里的语义键**（`stock.count.book` 这种）：
 * 术语表里带 `key` 的行，生成的翻译包第一列就是那个键（见 `scripts/i18n-build.py`），
 * 所以 Desk 这边认的是键，不是英文原句 —— 写成英文反而会被框架自带的译文抢走。
 */
(() => {
	const report_name = "ERP Stock Count Variance";
	let breadcrumb_request = 0;
	async function sync_breadcrumbs(report) {
		const request = ++breadcrumb_request;
		const name = report.get_filter_value("count");
		const current = () => request === breadcrumb_request
			&& frappe.get_route()[0] === "query-report"
			&& frappe.get_route()[1] === report_name
			&& report.get_filter_value("count") === name;
		if (!current()) return;
		frappe.pearnly.stockCountBreadcrumbs({ name, report: true });
		if (!name) return;
		try {
			const task = await frappe.pearnly.call(
				"pearnly_erp_portal.inventory.api.api_get_stock_count", { name });
			if (current()) {
				frappe.pearnly.stockCountBreadcrumbs({ name, title: task.title, report: true });
			}
		} catch (_error) { globalThis.pearnlyCaught?.(_error, "erp_stock_count_variance");
			// 服务端照常报告无权/不存在；保留按编号返回上级的路径。
		}
	}

frappe.query_reports[report_name] = {
	after_refresh(report) {
		// 列头菜单与产品里其它表格一致：只留排序（见 core `p.table_menu`）。
		frappe.pearnly.table_menu(report.datatable);
		// 页头菜单只留「导出」：原生的编辑、打印、PDF、自动邮件、加列、用户权限等由页头白名单拦
		// （`pearnly_native_actions`，core `desk/actions.js`），这里不再逐项删。
	},

	filters: [
		{
			fieldname: "count",
			label: __("nav.stockCount"),
			fieldtype: "Link",
			options: "ERP Stock Count",
			reqd: 1,
			on_change(report) {
				if (report._no_refresh) return;
				void sync_breadcrumbs(report);
				return report.refresh(true);
			},
		},
		{
			fieldname: "only",
			label: __("stock.board.only"),
			fieldtype: "Select",
			// 官方 Select 的值 / 显示分开：交给服务端的是固定值（counted / uncounted / differences），显示跟界面语言
			// （原来选项就是译文，服务端按英文值比永远不命中，等于不筛，G13）
			options: [{value: "", label: __("stock.count.filter.all")}, {value: "counted", label: __("stock.count.countedRows")},
				{value: "uncounted", label: __("stock.count.rowUncounted")}, {value: "differences", label: __("stock.count.rowDifference")}],
		},
		// 按仓库 / 库位筛（用户 10-29 批复 #2：筛选，打印按库位排）；进行中的盘点同样能看
		{
			fieldname: "warehouse",
			label: __("stock.col.warehouse"),
			fieldtype: "Link",
			options: "ERP Warehouse",
			get_query: () => frappe.pearnly.warehouse_query(),
		},
		{
			fieldname: "location",
			label: __("stock.count.location"),
			fieldtype: "Data",
		},
		{
			fieldname: "chart_by",
			label: __("stock.board.by"),
			fieldtype: "Select",
			default: "location",
			options: [{value: "product_name", label: __("stock.col.name")}, {value: "warehouse_name", label: __("stock.col.warehouse")},
				{value: "location", label: __("stock.board.shelf")}, {value: "category", label: __("stock.col.category")}],
		},
	],

	// 看板要的是"哪个商品/哪个仓库/哪个货架差得最多" —— 条形取差异最大的前 12 个，
	// 账面与实盘两条并排。全量行数拉不开看，表格在下面。
	get_chart_data(columns, data) {
		const by = frappe.query_report.get_filter_value("chart_by");
		const field = ["product_name", "warehouse_name", "category"].includes(by) ? by : "location";
		const groups = new Map();
		for (const row of data || []) {
			if (!row.counted) continue;
			const label = String(row[field] || "").trim() || __("stock.count.blank");
			const bucket = groups.get(label) || { book: 0, counted: 0 };
			bucket.book += Number(row.book_qty || 0);
			bucket.counted += Number(row.counted_qty || 0);
			groups.set(label, bucket);
		}
		const ranked = [...groups.entries()]
			.sort((a, b) => Math.abs(b[1].counted - b[1].book)
				- Math.abs(a[1].counted - a[1].book))
			.slice(0, 12);
		if (!ranked.length) return null;
		const accent = getComputedStyle(document.documentElement)
			.getPropertyValue("--pearnly-accent").trim() || "#7c4dff";
		return {
			data: {
				labels: ranked.map(([label]) => label),
				datasets: [
					{ name: __("stock.count.book"), values: ranked.map(([, v]) => v.book) },
					{ name: __("stock.count.counted"), values: ranked.map(([, v]) => v.counted) },
				],
			},
			type: "bar",
			height: 280,
			colors: ["#a3a3a3", accent],
			axisOptions: { shortenYAxisNumbers: 1 },
		};
	},

	// 数量与金额按**实际值**显示（与页面同一条规矩）：报表的 Float 列默认补齐三位，
	// 这里改成不留尾随 0（3.000 → 3）。
	formatter(value, row, column, data, format_cell) {
		if (column.fieldtype === "Float") {
			const text = frappe.pearnly.number(value);
			if (column.fieldname === "difference" && data && Number(data.counted)) {
				const colour = Number(data.difference) ? "var(--red-600)" : "var(--green-600)";
				return `<span style="color: ${colour}">${text}</span>`;
			}
			return text;
		}
		if (column.fieldtype === "Currency") return frappe.pearnly.money(value);
		return format_cell(value, row, column, data);
	},

	// 报表页默认把产品侧栏换成框架那条图标轨，这里**强制**换回我们的侧栏
	// （`restore_sidebar` 同名时会短路，这里就是那种情况），免得点进来像换了个产品。
	// 顺手把框架给"平台管理员"的那几项摘掉（改报表定义、打印/PDF、自动邮件、
	// 报表生成器加列、用户权限）—— 产品里没有这些位置，租户不该看到按钮。
	async onload(report) {
		// Historical bookmarks enter the same two-level workbench.
		const name = report.get_filter_value("count");
		frappe.set_route("pearnly-stock-count", ...(name ? [name] : []));
	},
};
})();
