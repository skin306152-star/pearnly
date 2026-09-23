/* 仓库：**当前公司**名下的仓库（`/desk/pearnly-warehouses`）。
 *
 * 用户 2026-09-23："仓库单独拿出，放在公司下方，仓库做成单独页" —— 从公司页里挪出来，
 * 自己在侧栏「公司」下面占一项。
 *
 * 仓库属于公司（官方 `Warehouse.company` 同一口径），所以这一页看的是**当前公司**的仓库：
 * 换公司（左上角那颗）跟着换。每家公司各有一个默认仓（`is_default`），行内可以「设为默认」。
 * 名称改了会动到历史单据上的仓库名，所以这里刻意**不给改名**，只给 新建 / 设为默认 / 停用启用。
 *
 * 文案仍走产品那份术语表的 `company.*` 命名空间（仓库那些词就写在那儿），所以注册时用同一个前缀。
 */
(() => {
	const p = frappe.pearnly;

	p.warehouses = async (page, t) => {
		const api = "pearnly_erp_portal.masters.api.";
		const stock_api = "pearnly_erp_portal.inventory.api.";
		const f = (fieldname, label, fieldtype = "Data", options = {}) =>
			({fieldname, label: t(label), fieldtype, ...options});
		const can_manage = () => Boolean(frappe.model.can_write("ERP Warehouse"));
		let list = null, selection = null;

		async function paint() {
			p.clear_page(page);
			page.set_title(__("nav.warehouses"));
			p.breadcrumbs({label: __("nav.warehouses"), route: p.subroute("pearnly-warehouses")});
			selection = p.selection_action({
				page, list: () => list, empty: restore_new,
				label: rows => (rows.every(row => !Number(row.is_active))
					? t("company.action.bulkEnable", rows.length)
					: t("company.action.bulkDisable", rows.length)),
				when: () => can_manage(),
				reason: () => t("company.onlyAdmin"),
				run: async rows => {
					const enabling = rows.every(row => !Number(row.is_active));
					for (const row of rows) {
						await p.call(stock_api + "api_update_warehouse",
							{name: row.name, is_active: enabling ? 1 : 0}, "POST");
					}
				},
				success: rows => t("company.notice.updated", rows.length),
			});
			list = p.list({page, doctype: "ERP Warehouse", compact_columns: true,
				selectable: true, on_selection: selection,
				columns: [
					// 当前公司的仓库：服务端按 `company` 过滤（`api_company_warehouses`）。
					{fieldname: "warehouse_name", label: t("company.col.warehouse"),
						width: 220, min_width: 150},
					{fieldname: "is_default", label: t("company.col.defaultWarehouse"),
						width: 130, min_width: 100,
						format: value => (Number(value) ? t("company.yes") : "—")},
					{fieldname: "is_active", label: t("company.col.status"), width: 100, min_width: 80,
						format: value => (Number(value) ? t("company.status.active")
							: t("company.status.inactive")),
						cell_class: value => (Number(value) ? "" : "pearnly-payment-due")},
					{fieldname: "note", label: t("company.field.note"), width: 200, min_width: 130},
				],
				on_open: row => (can_manage() ? editor(row) : null),
				row_actions: can_manage() ? row => [
					{id: "default", label: t("company.setDefaultWarehouse"),
						disabled: Number(row.is_default) === 1,
						action: () => make_default(row)},
				] : null,
				load: async () => {
					const rows = await p.call(api + "api_company_warehouses",
						{company_name: frappe.boot.pearnly_company, include_inactive: 1});
					return {items: rows, total: rows.length};
				},
				empty_action: can_manage()
					? {label: t("company.newWarehouse"), action: () => editor(null)} : null});
			restore_new();
			p.add_refresh(page, () => list.reload());
			await list.ready;
			// 别处的选择框指过来时（`link.js` 的 one-shot）：点「新建」开这一页自己的新建弹窗；
			// 点右边那颗箭头（打开这条记录）开它自己的弹窗。**必须在列表就绪之后** ——
			// 要按名字找到那一行（`list.data`），早于加载就什么都找不到。
			if (can_manage()) {
				await p.take_create("pearnly-warehouses", "ERP Warehouse", () => editor(null));
				await p.take_open("pearnly-warehouses", "ERP Warehouse", name => {
					const row = (list.data || []).find(item => item.name === name);
					if (row) editor(row);
				});
			}
		}

		function restore_new() {
			if (!can_manage()) return page.clear_primary_action();
			page.set_primary_action(t("company.newWarehouse"), () => editor(null));
		}

		// 新建 = 取个名字（归属当前公司，服务端定）；改 = 改名 + 备注。
		function editor(row) {
			const is_new = !row;
			p.dialog(is_new ? t("company.newWarehouse") : row.warehouse_name, [
				f("warehouse_name", "company.field.warehouse", "Data",
					{reqd: 1, default: row?.warehouse_name || ""}),
				f("note", "company.field.note", "Small Text", {default: row?.note || ""}),
			], is_new ? t("company.newWarehouse") : __("Save"), async (values, dialog) => {
				await p.call(stock_api + (is_new ? "api_create_warehouse" : "api_update_warehouse"),
					is_new ? {warehouse_name: values.warehouse_name, note: values.note, is_default: 0}
						: {name: row.name, warehouse_name: values.warehouse_name, note: values.note},
					"POST");
				dialog.hide();
				p.success(t(is_new ? "company.notice.created" : "company.notice.saved"), "click");
				await list.reload();
			});
		}

		async function make_default(row) {
			await p.call(api + "api_set_default_warehouse", {name: row.name}, "POST");
			p.success(t("company.notice.defaultWarehouse", row.warehouse_name), "click");
			await list.reload();
		}

		await paint();
		return () => list?.destroy();
	};
})();
