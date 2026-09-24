/* 客户主档：与供应商同一套形状（原生列表 + 一个弹窗），字段换成买方那几样。
 *
 * 用户 2026-09-23 口径同供应商："单独做一个供应商和客户的菜单，和销售采购联动上就好了"。
 *
 * **和销售联动**：销售单据上的"客户"是指向这张表的原生 Link
 * （`ERP Sales Document.customer`）；这里建完，开销售单时就能选到，
 * 选了之后客户名/类型/地址由销售页自动回填（`sales-fields.js` 的 onchange）。
 * 收银也一样（客户表在收银岗位上是只读可选的）。
 */
(() => {
	const p = frappe.pearnly;

	p.clients = async (page, t, wrapper, inline = null) => {
		const api = "pearnly_erp_portal.masters.api.";
		const api_lookup = "pearnly_erp_portal.masters.api.api_lookup_tax";
		const can_write = frappe.model.can_write("ERP Customer");
		// 与供应商页同一形状的本地简写。
		const f = (fieldname, label, fieldtype = "Data", options = {}) =>
			({fieldname, label: __(label), fieldtype, ...options});
		let list;

		const party_label = value => ({company: t("client.party.company"), person: t("client.party.person"),
			foreign: t("client.party.foreign"), anonymous: t("client.party.anonymous")})[value] || value || "";

		const columns = () => [
			// 六列一屏放得下、不用横向滑（用户 2026-09-23："内容太多，要滑动才能看得到"）。
			// `width` 是窗口宽时的占比，`min_width` 是最窄窗口下的底线。
			{ fieldname: "customer_name", width: 190, min_width: 150, label: t("client.col.name") },
			{ fieldname: "party_type", width: 76, min_width: 64, label: t("client.col.type"), format: party_label },
			{ fieldname: "tax_id", width: 120, min_width: 104, label: t("client.col.taxId") },
			{ fieldname: "contact_phone", width: 110, min_width: 92, label: t("client.col.phone") },
			{ fieldname: "address", width: 200, min_width: 150, label: t("client.col.address") },
			{ fieldname: "is_active", width: 76, min_width: 64, label: t("client.col.status"),
				// 两态都要看得见（原来"正常"那格是空的）；与供应商页、物料页同一口径。
				// 走本页术语键：框架词典会把 "Normal"/"Disabled" 译成「普通 / 禁用」。
				format: value => t(Number(value) ? "client.active" : "client.inactive") },
		];

		// 按税号查泰国税务局（RD）的官方登记（与供应商页同一件事）：公司买方填税号 → 点查询
		// → 官方抬头与地址带出来。个人/外国人本来就没有税号登记，所以那种类型不显示这颗按钮。
		async function lookup_tax(dialog) {
			const tax_id = dialog.get_value("tax_id");
			if (!String(tax_id || "").replace(/\D/g, "").length) {
				frappe.msgprint(t("client.lookup.needTaxId"));
				return;
			}
			frappe.dom.freeze(t("client.lookup.querying"));
			try {
				const result = await p.call(api_lookup, {tax_id, branch: 0}, "POST");
				if (!result?.ok) {
					frappe.msgprint(t(`client.lookup.${result?.error || "failed"}`) || t("client.lookup.failed"));
					return;
				}
				const data = result.data || {};
				await dialog.set_values({
					tax_id: data.tax_id || tax_id,
					customer_name: data.name || dialog.get_value("customer_name") || "",
					address: data.address || dialog.get_value("address") || "",
				});
				p.success(t("client.lookup.found", data.name || data.tax_id || ""));
			} finally {
				frappe.dom.unfreeze();
			}
		}

		// 一个弹窗干两件事：新建、改（`name` 不为空就是改）。
		function editor(row = {}) {
			const editable = can_write;
			const d = p.dialog(row.name || t("client.new"), [
				f("customer_name", "client.field.name", "Data", {reqd: 1, default: row.customer_name || ""}),
				f("party_type", "client.field.type", "Select", {
					options: [{value: "company", label: t("client.party.company")},
						{value: "person", label: t("client.party.person")},
						{value: "foreign", label: t("client.party.foreign")},
						{value: "anonymous", label: t("client.party.anonymous")}],
					default: row.party_type || "company"}),
				f("contact_phone", "client.field.phone", "Data", {default: row.contact_phone || ""}),
				f("tax_id", "client.field.taxId", "Data", {default: row.tax_id || ""}),
				// 公司才查得到税号登记（个人/外国人没有 RD 记录）。
				f("lookup", "client.lookup.action", "Button", {click: () => lookup_tax(d),
					depends_on: "eval:doc.party_type === 'company'"}),
				f("address", "client.field.address", "Small Text", {default: row.address || ""}),
				f("note", "client.field.note", "Small Text", {default: row.note || ""}),
				...(row.name ? [f("is_active", "client.field.active", "Check", {default: Number(row.is_active) ? 1 : 0})] : []),
			].map(field => ({...field, read_only: field.read_only ?? !editable})),
			editable ? (row.name ? t("client.save") : t("client.create")) : __("Close"),
			async (values, dialog) => {
				if (!editable) {dialog.hide(); return;}
				const method = row.name ? "api_update_customer" : "api_create_customer";
				const saved = await p.call(api + method, row.name ? {name: row.name, ...values} : values, "POST");
				dialog.hide();
				// 主按动作要有回音：提示 + 提示音（与其他页面同一套 `p.success`）。
				p.success(__("Saved"), "click");
				await inline?.on_saved?.({...row, ...values, ...saved});
				await list?.reload();
			});
			return d;
		}

		// Use the same editor without constructing a page or changing the caller's route.
		if (inline) {
			const row = inline.name ? await p.call("frappe.client.get", {doctype: "ERP Customer", name: inline.name})
				: {customer_name: inline.initial || ""};
			return editor(row);
		}

		page.set_title(t("client.title"));
		p.breadcrumbs({label: t("client.title"), route: p.subroute("pearnly-clients")});
		const search = page.add_field({fieldname: "q", label: __("Search"), fieldtype: "Data",
			change: () => void list?.reload()});
		// 行勾选 + 表头全选；选中后主按钮变"批量启用/停用"（客户也有启用态）。
		const selection = p.selection_action({
			page, list: () => list, empty: restore_new,
			label: rows => (rows.every(row => !Number(row.is_active))
				? t("client.action.bulkEnable", rows.length)
				: t("client.action.bulkDisable", rows.length)),
			run: async rows => {
				const enabling = rows.every(row => !Number(row.is_active));
				for (const row of rows) {
					await p.call(api + "api_update_customer",
						{name: row.name, is_active: enabling ? 1 : 0}, "POST");
				}
			},
			success: rows => t("client.notice.toggled", rows.length),
		});
		function restore_new() {
			if (can_write) page.set_primary_action(t("client.new"), () => editor());
			else page.clear_primary_action();
		}
		list = p.list({page, doctype: "ERP Customer", selectable: true, compact_columns: true,
			on_selection: selection,
			on_open: row => editor(row),
			columns: columns(),
			// 列头点一下排序：这一页**一次取全**（没有分页），所以整套在手上直接排，
			// 口径与服务端那几张一致（见 core `p.sort_rows`）。
			sort_fields: ["customer_name", "party_type", "tax_id", "contact_phone", "address", "is_active"],
			load: async ({sort_by, sort_order}) => {
				const rows = await p.call(api + "api_list_customers",
					{q: search.get_value() || undefined, include_inactive: 1, limit: 0});
				const items = p.sort_rows(rows, {sort_by, sort_order,
					fields: ["customer_name", "party_type", "tax_id", "contact_phone", "address", "is_active"]});
				return {items, total: items.length};
			},
			empty_action: can_write ? {label: t("client.new"), action: () => editor()} : null,
		});
		restore_new();
		p.add_refresh(page, () => void list.reload());
		await list.ready;
		// 别处的选择框指过来时（`link.js` 的 one-shot）：新建 / 打开这条记录。
		// **要在列表就绪之后**：按名字找行得先有行。
		if (can_write) await p.take_create("pearnly-clients", "ERP Customer", () => editor());
		await p.take_open("pearnly-clients", "ERP Customer", name => {
			const row = (list.data || []).find(item => item.name === name);
			if (row) editor(row);
		});
	};
	p.link_editors["ERP Customer"] = async context =>
		p.clients(null, await p.texts("client."), null, context);
	p.link_editors["ERP Customer"].label = __("Customer");
})();
