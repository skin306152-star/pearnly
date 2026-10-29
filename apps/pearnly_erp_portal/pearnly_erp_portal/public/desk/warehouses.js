/* 仓库：**当前公司**名下的仓库（`/desk/pearnly-warehouses`）。
 *
 * 用户 2026-09-23："仓库单独拿出，放在公司下方，仓库做成单独页" —— 从公司页里挪出来，
 * 自己在侧栏「公司」下面占一项。
 *
 * 仓库属于公司（官方 `Warehouse.company` 同一口径），所以这一页看的是**当前公司**的仓库：
 * 换公司（左上角那颗）跟着换。**没有"默认仓"**（用户 2026-09-24："不要做默认仓，依赖全部删掉"）：
 * 哪张单据、哪台收银台用哪个仓，都写在那个地方自己身上；多个仓时让你明确选，不替你猜。
 * 名称改了会动到历史单据上的仓库名，所以这里刻意**不给改名**，只给 新建 / 停用启用。
 *
 * 文案仍走产品那份术语表的 `company.*` 命名空间（仓库那些词就写在那儿），所以注册时用同一个前缀。
 */
(() => {
	const p = frappe.pearnly;

	p.warehouses = async (page, t, wrapper, inline = null) => {
		const api = "pearnly_erp_portal.masters.api.";
		const stock_api = "pearnly_erp_portal.inventory.api.";
		const f = (fieldname, label, fieldtype = "Data", options = {}) =>
			({fieldname, label: t(label), fieldtype, ...options});
		const can_manage = () => Boolean(frappe.model.can_write("ERP Warehouse"));
		// 接了 Express 的公司：仓库跟 Express 一一对应，在 Express 里建好、公司页「同步 Express」后自动出现——这里不给「新建」
		// （服务端 `create_warehouse` 同样拒，多仓闭环 P1）
		const can_create = () => can_manage() && !p.express_company();
		let list = null, selection = null;

		async function paint() {
			p.clear_page(page);
			page.set_title(__("nav.warehouses"));
			p.breadcrumbs({label: __("nav.warehouses"), route: p.subroute("pearnly-warehouses")});
			selection = p.selection_action({
				page, list: () => list, sound: "click", empty: restore_new,
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
					// 仓库编号（用户 10-29 批复 #3）：接了 Express 的仓就是 Express 仓库码；没填显示系统号
					{fieldname: "warehouse_code", label: t("company.col.warehouseCode"), width: 120, min_width: 90,
						format: (value, row) => row?.express_code || value || row?.name},
					// 仓库**属于公司**：这一列把绑定关系摆在面上（用户 2026-09-24：
					// "现在的仓库和公司数据是否联动"）。这一页列的是**当前公司**的仓，
					// 换公司（左上角那颗）整页跟着换。
					{fieldname: "company_name", label: __("Company"),
						width: 160, min_width: 120},
					// 「默认仓库」那一列本轮删掉了（用户 2026-09-24："不要做默认仓，依赖的全部删掉"）——
					// 仓库就是一份平铺的清单，哪个单据/哪台收银台用哪个仓都写在它自己身上。
					{fieldname: "note", label: t("company.field.note"), width: 200, min_width: 130},
					// 状态排在**备注后面**（用户 2026-09-24 的口径）。
					{fieldname: "is_active", label: t("company.col.status"), width: 100, min_width: 80,
						format: value => (Number(value) ? t("company.status.active")
							: t("company.status.inactive")),
						cell_class: value => (Number(value) ? "" : "pearnly-payment-due")},
				],
				on_open: row => (can_manage() ? editor(row) : null),
				// 行内那颗「设为默认」跟着"默认仓"一起删了（它写的就是 `is_default`）。
				// 列头点一下排序：一次取全，在本地排（同 core `p.sort_rows`）。
				sort_fields: ["warehouse_name", "warehouse_code", "company_name", "note", "is_active"],
				load: async ({sort_by, sort_order}) => {
					const rows = await p.call(api + "api_company_warehouses",
						{company_name: frappe.boot.pearnly_company, include_inactive: 1});
					const items = p.sort_rows(rows, {sort_by, sort_order,
						fields: ["warehouse_name", "warehouse_code", "company_name", "note", "is_active"]});
					return {items, total: items.length};
				},
				empty_action: can_create()
					? {label: t("company.newWarehouse"), action: () => editor(null)} : null});
			restore_new();
			await list.ready;
			// 别处的选择框指过来时（`link.js` 的 one-shot）：点「新建」开这一页自己的新建弹窗；
			// 点右边那颗箭头（打开这条记录）开它自己的弹窗。**必须在列表就绪之后** ——
			// 要按名字找到那一行（`list.data`），早于加载就什么都找不到。
			if (can_manage()) {
				// 别处选择框点「新建」过来：接了 Express 的公司说在 Express 里建（与服务端同一句）
				await p.take_create("pearnly-warehouses", "ERP Warehouse", () => (can_create() ? editor(null)
					: frappe.msgprint(__("Create the warehouse in Express, then use Sync Express on the company page"))));
				await p.take_open("pearnly-warehouses", "ERP Warehouse", name => {
					const row = (list.data || []).find(item => item.name === name);
					if (row) editor(row);
				});
			}
		}

		function restore_new() {
			if (!can_create()) return page.clear_primary_action();
			page.set_primary_action(t("company.newWarehouse"), () => editor(null));
		}

		// 新建 = 取个名字（归属当前公司，服务端定）；改 = 改名 + 备注 + 状态。
		// 状态那一格的值直接用界面上的字（下拉里没有"值/标签"之分），这里再翻回 1/0。
		const status_options = () => [t("company.status.active"), t("company.status.inactive")].join("\n");
		const status_value = label => (label === t("company.status.inactive") ? 0 : 1);
		function editor(row) {
			const is_new = !row;
			const companies = frappe.boot.pearnly_companies || [];
			const dialog = p.dialog(is_new ? t("company.newWarehouse") : row.warehouse_name, [
				// **建仓必须先选公司**（用户 2026-09-24："没有绑定公司不可以建仓库"）：
				// 新仓默认落在当前公司，可以改；改的那一侧只读（仓库属于公司，改归属等于换账）。
				// 只有一家公司时直接带出、不摆出来（录制问题 #2：一开窗就展开的公司下拉盖住仓库名那一格）。
				// 公司多了用可输入搜索的原生下拉（按名称 / 税号），少的照旧普通下拉
				// 改已有仓库时公司只读：用普通下拉（只读的 Autocomplete 框架不建选项列表，取值校验会报错、公司变空，验收 10-26）
				{fieldname: "company", label: __("Company"), fieldtype: companies.length > 10 && is_new ? "Autocomplete" : "Select", reqd: 1,
					read_only: !is_new, hidden: companies.length === 1 && is_new ? 1 : 0,
					options: (frappe.boot.pearnly_companies || []).map(item => ({
						value: item.name, label: [p.company_name_of(item, companies), companies.length > 10 && item.tax_id]
							.filter(Boolean).join(" · ")})),
					default: row?.company || frappe.boot.pearnly_company || (companies.length === 1 ? companies[0].name : "")},
				f("warehouse_name", "company.field.warehouse", "Data",
					{reqd: 1, default: row?.warehouse_name || inline?.initial || ""}),
				f("warehouse_code", "company.col.warehouseCode", "Data",
					{length: 30, default: row?.express_code || row?.warehouse_code || ""}),
				f("note", "company.field.note", "Small Text", {default: row?.note || ""}),
				f("is_active", "company.col.status", "Select",
					{options: status_options(),
						default: Number(row ? row.is_active : 1) ? t("company.status.active")
							: t("company.status.inactive")}),
				// 库位清单（第 5 项）：建了清单，这个仓的库位只能从清单选（服务端 `inventory.locations` / `positions.check`）；
				// 没建清单照旧手输。有库存的库位不能停用，用过的不能删
				...(is_new ? [] : [
					{fieldtype: "Section Break"},
					{fieldtype: "Table", fieldname: "locations", label: __("stock.location.list"), in_place_edit: true, data: [],
						fields: [
							{fieldtype: "Data", fieldname: "location_name", label: __("stock.count.location"), reqd: 1, in_list_view: 1, columns: 3},
							// 库位说明（用户 10-29）：选择框显示「A1 · 说明」，盘点打印的库位格也带上
							{fieldtype: "Data", fieldname: "description", label: __("stock.location.description"), in_list_view: 1, columns: 3},
							{fieldtype: "Check", fieldname: "is_active", label: __("Active"), default: 1, in_list_view: 1, columns: 2},
							{fieldtype: "Float", fieldname: "qty", label: __("Qty"), read_only: 1, in_list_view: 1, columns: 2},
						]},
					{fieldtype: "Button", fieldname: "from_stock", label: __("stock.location.fromStock"),
						click: () => add_in_stock()},
				]),
			], is_new ? t("company.newWarehouse") : __("Save"), async (values, dialog) => {
				const saved = await p.call(stock_api + (is_new ? "api_create_warehouse" : "api_update_warehouse"),
					is_new
						? {company: values.company, warehouse_name: values.warehouse_name, note: values.note,
							warehouse_code: values.warehouse_code || "", is_active: status_value(values.is_active)}
						: {name: row.name, warehouse_name: values.warehouse_name, note: values.note,
							...(row.express_code ? {} : {warehouse_code: values.warehouse_code || ""}),
							is_active: status_value(values.is_active)},
					"POST");
				if (!is_new && locations_loaded) {
					await p.call(stock_api.replace("api.", "locations.") + "api_save_locations", {warehouse: row.name,
						rows: (values.locations || []).map(item => ({location_name: item.location_name, description: item.description || "",
							is_active: item.is_active ? 1 : 0}))}, "POST");
				}
				dialog.hide();
				p.success(t(is_new ? "company.notice.created" : "company.notice.saved"), "click");
				await inline?.on_saved?.({...row, ...values, ...saved});
				await list?.reload();
			});
			let locations_loaded = false, in_stock = [];
			const set_locations = data => {
				const table = dialog.fields_dict.locations;
				table.df.data = data.rows.map(item => ({...item, is_active: Number(item.is_active)}));
				table.grid.refresh();
				in_stock = data.in_stock;
				dialog.set_df_property("from_stock", "hidden", !in_stock.length);
				locations_loaded = true;
			};
			function add_in_stock() {
				const table = dialog.fields_dict.locations;
				const have = new Set((table.grid.get_data() || []).map(item => item.location_name));
				for (const item of in_stock) if (!have.has(item.location_name)) table.df.data.push({...item, is_active: 1});
				table.grid.refresh();
				dialog.set_df_property("from_stock", "hidden", 1);
			}
			if (!is_new && can_manage()) {
				void p.call(stock_api.replace("api.", "locations.") + "api_get_locations", {warehouse: row.name}).then(set_locations);
			}
			// 光标先放在仓库名（框架默认落在第一格，也就是公司那格，下拉一展开就盖住下面）。
			const focus_name = () => dialog.fields_dict.warehouse_name?.$input?.trigger("focus");
			dialog.$wrapper.one("shown.bs.modal", () => setTimeout(focus_name, 0));
			// 由 Express 同步来的档：Express 里有的栏位只认 Express（服务端 `express.locks` 同一份清单兜底）
			if (row?.express_code) {
				["warehouse_name", "warehouse_code", "company", "is_active"].forEach(key => dialog.fields_dict[key] && dialog.set_df_property(key, "read_only", 1));
			}
			return dialog;
		}

		if (inline) {
			// 别处仓库选择框点「新建」（原地弹窗）：接了 Express 的公司不开新建表单，直接说服务端那同一句（验收复核 P1：原来填完一张表才被拒）。
			// 回的是原生消息框（同样是对话框，调用方等它关）
			if (!inline.name && p.express_company())
				return frappe.msgprint(__("Create the warehouse in Express, then use Sync Express on the company page"));
			const row = inline.name ? await p.call("frappe.client.get", {doctype: "ERP Warehouse", name: inline.name}) : null;
			const dialog = editor(row);
			if (!can_manage()) {
				Object.values(dialog.fields_dict).forEach(field => dialog.set_df_property(field.df.fieldname, "read_only", 1));
				dialog.get_primary_btn().prop("disabled", true);
			}
			return dialog;
		}
		await paint();
		return () => list?.destroy();
	};
	p.link_editors["ERP Warehouse"] = async context =>
		p.warehouses(null, await p.texts("company."), null, context);
	p.link_editors["ERP Warehouse"].label = __("Warehouse");
})();
