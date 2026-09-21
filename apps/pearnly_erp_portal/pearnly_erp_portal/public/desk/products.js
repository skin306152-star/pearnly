/* 商品数据：**原生列表**（框架装的 ListView 渲染器），只用我们自己的取数接口。
 *
 * 为什么走 `frappe.pearnly.list`：它继承的是框架装的 BaseList + ListView 渲染，
 * 于是复选框列、第 N 项/共 M 项、20/100/500/2500 分页、空状态、行高与移动端样式
 * 全部是原生那套；而"列表视图切换 / 已保存的筛选器 / 过滤器 / 排序"属于 ListView 的
 * 另外几条装配路径（`setup_view_menu` / `setup_standard_filters` / `setup_filter_area`），
 * 产品列表按用户 2026-09-21 的口径**不放这些**，走 BaseList 正好天然没有。
 *
 * 复选框列留着的理由：它后面挂着**真有的功能** —— 批量启用/停用选中的商品。
 * 没有去处的话就不该放出来。
 *
 * 文案全部来自 `scripts/i18n/messages.tsv` 的 `product.*` 键。
 */
(() => {
	const p = frappe.pearnly;

	p.products = async (page, t) => {
		const api = "pearnly_erp_portal.products.service.";
		let list;
		let busy = false;

		// "设过没有"要跟值一起看：Currency 列存不下 NULL，库里 0 与"从未设过"是同一个数字。
		// 没有成本权限的岗位，服务端把值与标记一起清空，于是这里自然显示"未设"。
		const marked = (row, flag, value) =>
			row[flag] ? format_currency(value, "THB") : t("product.cell.notSet");

		const filters = {};
		filters.search = page.add_field({
			fieldtype: "Data",
			fieldname: "search",
			label: t("product.field.search"),
			change: () => void list?.reload(),
		});
		filters.include_inactive = page.add_field({
			fieldtype: "Check",
			fieldname: "include_inactive",
			label: t("product.action.toggleInactive"),
			change: () => void list?.reload(),
		});
		const query = () => ({
			search: filters.search.get_value() || undefined,
			include_inactive: filters.include_inactive.get_value() ? 1 : undefined,
		});

		// 右上角那一颗主按钮：没选中任何行时是"新建商品"（与参考图一致），
		// 选中若干行时切换成批量启用/停用 —— 这是产品真有的动作，复选框列因此有实际去处。
		function update_action(rows) {
			if (busy) {
				page.btn_primary?.prop("disabled", true);
				return;
			}
			if (!rows.length) {
				if (can_create) page.set_primary_action(t("product.action.new"), () => open_create());
				else page.clear_primary_action();
				return;
			}
			const enabling = rows.every((row) => !row.is_active);
			page.set_primary_action(
				enabling
					? t("product.action.bulkEnable", rows.length)
					: t("product.action.bulkDisable", rows.length),
				() => void toggle_selected(rows, enabling),
			);
		}

		async function toggle_selected(rows, enabling) {
			if (busy) return;
			busy = true;
			update_action(rows);
			try {
				for (const row of rows) {
					await p.call(api + "api_set_active",
						{ name: row.name, active: enabling ? 1 : 0 }, "POST");
				}
				p.success(t("product.notice.toggledCount", rows.length));
				list.clear_checked_items();
				await list.reload();
			} catch (error) {
				if (!error?._server_messages && !error?.responseJSON) console.error(error);
			} finally {
				busy = false;
				update_action(list.get_checked_items());
			}
		}

		list = p.list({
			page,
			doctype: "ERP Product",
			selectable: true,
			on_selection: update_action,
			on_open: (row) => open_form(row),
			load: async ({ start, page_length }) => {
				// 列表框架给的是 {start, page_length}；服务端的参数名是 start / limit。
				const [items, total] = await Promise.all([
					p.call(api + "api_list_products",
						{ start, limit: page_length, ...query() }),
					p.call(api + "api_count_products", query()),
				]);
				return { items, total };
			},
			columns: [
				{ fieldname: "product_name", label: t("product.col.name") },
				{ fieldname: "code", label: t("product.col.code") },
				{ fieldname: "barcode", label: t("product.col.barcode") },
				{ fieldname: "unit", label: t("product.col.unit") },
				{ fieldname: "unit_price", label: t("product.col.price"),
					format: (_value, row) => marked(row, "price_set", row.unit_price) },
				{ fieldname: "default_cost", label: t("product.col.cost"),
					format: (_value, row) => marked(row, "cost_set", row.default_cost) },
				{ fieldname: "is_active", label: t("product.col.status"),
					format: (value) => (value ? __("Active") : __("Disabled")) },
			],
		});

		const can_create = frappe.model.can_create("ERP Product");

		function open_create() {
			const dialog = p.dialog(
				t("product.action.new"),
				[
					{ fieldtype: "Data", fieldname: "product_name", label: t("product.field.name"),
						reqd: 1 },
					{ fieldtype: "Data", fieldname: "code", label: t("product.field.code") },
					{ fieldtype: "Data", fieldname: "barcode", label: t("product.field.barcode") },
					{ fieldtype: "Data", fieldname: "unit", label: t("product.field.unit") },
					{ fieldtype: "Column Break" },
					{ fieldtype: "Currency", fieldname: "unit_price",
						label: t("product.field.price"), options: "THB" },
					{ fieldtype: "Currency", fieldname: "default_cost",
						label: t("product.field.cost"), options: "THB" },
					{ fieldtype: "Float", fieldname: "min_stock", label: t("product.field.minStock") },
					{ fieldtype: "Check", fieldname: "vat_applicable",
						label: t("product.field.vat"), default: 1 },
				],
				t("product.action.save"),
				async (values, d) => {
					for (const field of ["unit_price", "default_cost"]) {
						if (values[field] === "") values[field] = null;
					}
					await p.call(api + "api_create_product", values, "POST");
					d.hide();
					p.success(t("product.notice.saved", values.product_name));
					await list.reload();
				},
			);
			dialog.$wrapper.find(".modal-dialog").addClass("modal-lg");
		}

		async function open_form(row) {
			const existing = await p.call(api + "api_get_product", { name: row.name });
			const can_write = frappe.model.can_write("ERP Product");
			const dialog = p.dialog(
				existing.product_name,
				[
					{ fieldtype: "Data", fieldname: "product_name", label: t("product.field.name"),
						reqd: 1, default: existing.product_name },
					{ fieldtype: "Data", fieldname: "code", label: t("product.field.code"),
						default: existing.code },
					{ fieldtype: "Data", fieldname: "barcode", label: t("product.field.barcode"),
						default: existing.barcode },
					{ fieldtype: "Data", fieldname: "unit", label: t("product.field.unit"),
						default: existing.unit },
					{ fieldtype: "Column Break" },
					{ fieldtype: "Currency", fieldname: "unit_price", label: t("product.field.price"),
						options: "THB", default: existing.price_set ? existing.unit_price : null },
					{ fieldtype: "Currency", fieldname: "default_cost", label: t("product.field.cost"),
						options: "THB", default: existing.cost_set ? existing.default_cost : null },
					{ fieldtype: "Float", fieldname: "min_stock",
						label: t("product.field.minStock"), default: existing.min_stock },
					{ fieldtype: "Check", fieldname: "vat_applicable", label: t("product.field.vat"),
						default: existing.vat_applicable },
				],
				t("product.action.save"),
				async (values, d) => {
					// 空串 = "从未设过"，与 ฿0 是两回事；所以显式变 null，不让它落成 0。
					for (const field of ["unit_price", "default_cost"]) {
						if (values[field] === "") values[field] = null;
					}
					await p.call(api + "api_update_product", { name: existing.name, ...values },
						"POST");
					d.hide();
					p.success(t("product.notice.saved", values.product_name));
					await list.reload();
				},
			);
			if (!can_write) dialog.get_primary_btn().prop("disabled", true);
		}

		p.add_refresh(page, () => list.reload());
		await list.ready;
		update_action([]);
	};
})();
