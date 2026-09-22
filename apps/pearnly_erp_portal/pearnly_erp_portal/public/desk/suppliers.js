/* 供应商主档：**原生列表 + 一个弹窗**。
 *
 * 用户 2026-09-23 口径："单独做一个供应商和客户的菜单，和销售采购联动上就好了"、
 * "字段简单快捷的就好了，新建的时候一个弹窗输入相关信息就搞定"。
 *
 * 所以这一页只做三件事：列出来、新建（弹窗）、点行改（同一个弹窗）+ 停用。
 * 字段就旧线上供应商那几样：名称、税号、分店、电话、地址、备注。
 *
 * **和采购联动**：采购单据上的"供应商"就是指向这张表的原生 Link
 * （`Purchase Document.supplier`），所以这里建完，开采购单时就能选到；
 * 选了之后供应方名称/税号/地址由采购页自动回填（`purchase-fields.js` 的 onchange）。
 */
(() => {
	const p = frappe.pearnly;

	p.suppliers = async (page, t, wrapper) => {
		const api = "pearnly_erp_portal.purchasing.api.";
		const api_lookup = "pearnly_erp_portal.masters.api.api_lookup_tax";
		const can_write = frappe.model.can_write("ERP Supplier");
		// 字段就这几样，给个本地简写（与销售页的 `u.field` 同一形状）。
		const f = (fieldname, label, fieldtype = "Data", options = {}) =>
			({fieldname, label: __(label), fieldtype, ...options});
		let list;

		const columns = () => [
			{ fieldname: "supplier_name", label: t("supplier.col.name") },
			{ fieldname: "tax_id", label: t("supplier.col.taxId") },
			{ fieldname: "branch_type", label: t("supplier.col.branch"),
				format: value => (value === "branch" ? t("supplier.branch") : "") },
			{ fieldname: "phone", label: t("supplier.col.phone") },
			{ fieldname: "address", label: t("supplier.col.address") },
			{ fieldname: "is_active", label: t("supplier.col.status"),
				// 两态都要看得见：原来"正常"那格是空的，看起来像这列没数据
				// （用户 2026-09-23）。措辞与物料页同一口径（正常 / 已停用），
				// 用本页自己的术语键取 —— 框架自带的词典把 "Normal" 译成「普通」、
				// "Disabled" 译成「禁用」，走 `__()` 会被它盖掉。
				format: value => t(Number(value) ? "supplier.active" : "supplier.inactive") },
		];

		// 按税号查泰国税务局（RD）的官方登记：填税号 → 点按钮 → 抬头/地址/分支带出来。
		// 与旧项目同一件事（`services/rd/rd_api.py`），落点在本仓 `masters/rd.py`；
		// 查不到或外部接口连不上，服务端只回原因、不抛错，录入照常能存。
		async function lookup_tax(dialog) {
			const tax_id = dialog.get_value("tax_id");
			if (!String(tax_id || "").replace(/\D/g, "").length) {
				frappe.msgprint(t("supplier.lookup.needTaxId"));
				return;
			}
			frappe.dom.freeze(t("supplier.lookup.querying"));
			try {
				const branch = Number(dialog.get_value("branch_no") || 0);
				const result = await p.call(api_lookup, {tax_id, branch}, "POST");
				if (!result?.ok) {
					frappe.msgprint(t(`supplier.lookup.${result?.error || "failed"}`) || t("supplier.lookup.failed"));
					return;
				}
				const data = result.data || {};
				await dialog.set_values({
					tax_id: data.tax_id || tax_id,
					supplier_name: data.name || dialog.get_value("supplier_name") || "",
					address: data.address || dialog.get_value("address") || "",
					branch_type: Number(data.branch_no) ? "branch" : "head",
					branch_no: Number(data.branch_no) ? data.branch_no : dialog.get_value("branch_no") || "",
				});
				p.success(t("supplier.lookup.found", data.name || data.tax_id || ""));
			} finally {
				frappe.dom.unfreeze();
			}
		}

		// 一个弹窗干两件事：新建、改（`name` 不为空就是改）。
		function editor(row = {}) {
			const editable = can_write;
			const d = p.dialog(row.name || t("supplier.new"), [
				f("supplier_name", "supplier.field.name", "Data", {reqd: 1, default: row.supplier_name || ""}),
				f("phone", "supplier.field.phone", "Data", {default: row.phone || ""}),
				f("tax_id", "supplier.field.taxId", "Data", {default: row.tax_id || ""}),
				f("lookup", "supplier.lookup.action", "Button", {click: () => lookup_tax(d)}),
				f("branch_type", "supplier.field.branch", "Select", {
					options: [{value: "head", label: t("supplier.head")}, {value: "branch", label: t("supplier.branch")}],
					default: row.branch_type || "head"}),
				f("branch_no", "supplier.field.branchNo", "Data", {default: row.branch_no || ""}),
				f("address", "supplier.field.address", "Small Text", {default: row.address || ""}),
				f("note", "supplier.field.note", "Small Text", {default: row.note || ""}),
				...(row.name ? [f("is_active", "supplier.field.active", "Check", {default: Number(row.is_active) ? 1 : 0})] : []),
			].map(field => ({...field, read_only: field.read_only ?? !editable})),
			editable ? (row.name ? t("supplier.save") : t("supplier.create")) : __("Close"),
			async (values, dialog) => {
				if (!editable) {dialog.hide(); return;}
				const method = row.name ? "api_update_supplier" : "api_create_supplier";
				await p.call(api + method, row.name ? {name: row.name, ...values} : values, "POST");
				dialog.hide();
				// 主按动作要有回音：提示 + 提示音（与其他页面同一套 `p.success`）。
				p.success(__("Saved"));
				await list?.reload();
			});
			return d;
		}

		page.set_title(t("supplier.title"));
		p.breadcrumbs({label: t("supplier.title"), route: p.subroute("pearnly-suppliers")});
		const search = page.add_field({fieldname: "q", label: __("Search"), fieldtype: "Data",
			change: () => void list?.reload()});
		// 行勾选 + 表头全选（与商品/采购/销售那几个列表同一个原生实现），
		// 选中之后主按钮变"批量启用/停用" —— 主数据本来就有启用态，选择要有个去处。
		const selection = p.selection_action({
			page, list: () => list, empty: restore_new,
			label: rows => (rows.every(row => !Number(row.is_active))
				? t("supplier.action.bulkEnable", rows.length)
				: t("supplier.action.bulkDisable", rows.length)),
			run: async rows => {
				const enabling = rows.every(row => !Number(row.is_active));
				for (const row of rows) {
					await p.call(api + "api_update_supplier",
						{name: row.name, is_active: enabling ? 1 : 0}, "POST");
				}
			},
			success: rows => t("supplier.notice.toggled", rows.length),
		});
		function restore_new() {
			if (can_write) page.set_primary_action(t("supplier.new"), () => editor());
			else page.clear_primary_action();
		}
		list = p.list({page, doctype: "ERP Supplier", selectable: true, on_selection: selection,
			on_open: row => editor(row),
			columns: columns(),
			load: async () => {
				const rows = await p.call(api + "api_list_suppliers",
					{q: search.get_value() || undefined, include_inactive: 1, limit: 0});
				return {items: rows, total: rows.length};
			},
			empty_action: can_write ? {label: t("supplier.new"), action: () => editor()} : null,
		});
		restore_new();
		p.add_refresh(page, () => void list.reload());
		await list.ready;
	};
})();
