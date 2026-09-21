/* 商品数据。只用官方组件：FieldGroup 排版、Dialog 建改、DataTable 列表。
 *
 * 为什么不用原生 ListView（DocType 驱动的那套）：这一页要的是服务端**已经裁好的投影**
 * —— 成本按岗位遮蔽、单位换算、`price_set` 区分"未设价 / 真 ฿0"。走 ListView 等于把
 * 这些规则在前端再实现一遍，两处必然漂移。所以列表用 DataTable，行点击进 Dialog。
 *
 * 文案全部来自 `scripts/i18n/messages.tsv` 的 `product.*` 键，代码里不留中文/泰文。
 */
(() => {
	const p = frappe.pearnly;

	p.products = async (page, t) => {
		const api = "pearnly_erp_portal.products.service.";
		let table;
		let search = "";
		let includeInactive = false;

		// 售价与参考成本都要看"设过没有"的标记：Currency 列存不下 NULL，
		// 库里 0 与"从未设过"是同一个数字。没设过就说"未设"，不显示 ฿0.00 ——
		// 那会让人以为价格/成本真的是零。
		// 没有成本权限的岗位，服务端把值与标记一起清空，于是这里自然显示"未设"。
		const marked = (row, flag, value) =>
			row[flag] ? format_currency(value, "THB") : t("product.cell.notSet");

		async function refresh() {
			const rows = await p.call(api + "api_list_products", {
				search: search || undefined,
				include_inactive: includeInactive ? 1 : undefined,
				limit: 100,
			});
			table?.destroy();
			page.main.empty();
			page.clear_inner_toolbar();
			if (frappe.model.can_create("ERP Product")) {
				page.set_primary_action(t("product.action.new"), () => open_form(null));
			}

			const form = p.layout(page.main, [
				{
					fieldtype: "Data",
					fieldname: "search",
					label: t("product.field.search"),
					default: search,
				},
				{ fieldtype: "Column Break" },
				{
					fieldtype: "Check",
					fieldname: "include_inactive",
					label: t("product.action.toggleInactive"),
					default: includeInactive ? 1 : 0,
				},
				{ fieldtype: "Section Break" },
				{ fieldtype: "HTML", fieldname: "rows" },
			]);
			const apply_filters = async () => {
				search = form.get_value("search") || "";
				includeInactive = Boolean(form.get_value("include_inactive"));
				await refresh();
			};
			form.fields_dict.search.$input.on("change", () => void apply_filters());
			form.fields_dict.include_inactive.$input.on("change", () => void apply_filters());

			const target = form.fields_dict.rows.$wrapper;
			table = await p.table(
				target,
				[
					t("product.col.name"),
					t("product.col.code"),
					t("product.col.barcode"),
					t("product.col.unit"),
					t("product.col.price"),
					t("product.col.cost"),
					t("product.col.status"),
				],
				rows.map((row) => [
					row.product_name,
					row.code || "",
					row.barcode || "",
					row.unit || "",
					marked(row, "price_set", row.unit_price),
					marked(row, "cost_set", row.default_cost),
					row.is_active ? __("Active") : __("Disabled"),
				]),
			);
			// 行点击进编辑：DataTable 的行带 data-row-index，映射回这一份数据即可，
			// 不需要给每行塞按钮（那会多出一堆和表格不同步的控件）。
			target.on("click", ".dt-row", (event) => {
				const index = Number($(event.currentTarget).attr("data-row-index"));
				const row = rows[index];
				if (row) open_form(row);
			});
		}

		async function open_form(row) {
			const existing = row
				? await p.call(api + "api_get_product", { name: row.name })
				: {};
			const editable = !existing.name || frappe.model.can_write("ERP Product");
			const dialog = p.dialog(
				existing.name ? existing.product_name : t("product.action.new"),
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
						options: "THB", default: existing.default_cost },
					{ fieldtype: "Float", fieldname: "min_stock",
						label: t("product.field.minStock"), default: existing.min_stock },
					{ fieldtype: "Check", fieldname: "vat_applicable", label: t("product.field.vat"),
						default: existing.vat_applicable ?? 1 },
				],
				t("product.action.save"),
				async (values, d) => {
					// 空售价 = "从未设价"，与 ฿0 是两回事（服务端用 price_set 记这个区别）；
					// 所以空串要显式变 null，不能让它落成 0。
					if (values.unit_price === "") values.unit_price = null;
					if (values.default_cost === "") values.default_cost = null;
					if (existing.name) {
						await p.call(api + "api_update_product",
							{ name: existing.name, ...values }, "POST");
					} else {
						await p.call(api + "api_create_product", values, "POST");
					}
					d.hide();
					p.success(t("product.notice.saved", values.product_name));
					await refresh();
				},
			);
			if (!editable) dialog.get_primary_btn().prop("disabled", true);
			if (existing.name && frappe.model.can_write("ERP Product")) {
				dialog.add_custom_action(
					existing.is_active ? t("product.action.disable") : t("product.action.enable"),
					async () => {
						await p.call(api + "api_set_active",
							{ name: existing.name, active: existing.is_active ? 0 : 1 }, "POST");
						dialog.hide();
						p.success(t("product.notice.toggled", existing.product_name));
						await refresh();
					},
				);
			}
		}

		p.add_refresh(page, refresh);
		await refresh();
	};
})();
