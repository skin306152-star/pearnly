/* 公司：**一租户多公司**（用户 2026-09-23："我要做一租户多公司"）。
 *
 * 一张表两张面孔（`ERP Company`），路由分两段：
 *
 *   · `/desk/pearnly-company`        → **当前公司**的资料：抬头、简称、税号、分支、
 *                                      联系方式、收款 PromptPay、地址与备注（能改的只有产品管理员）；
 *   · `/desk/pearnly-company/master` → **公司列表**：登记过哪几家、新建、编辑、
 *                                      「设为当前公司」（写自己的用户默认值）、停用启用。
 *
 * 公司之间怎么隔离：用**官方的 `User Permission`** —— 在团队页给某个账号勾上哪几家，
 * 他列表里就只看得到那几家（官方机制，不用我们另写一套）。切换公司切的是**用户默认值**，
 * 放在左上角那颗下拉里（`desk/company-menu.js`），切换后整页重载。
 *
 * 谁能看、谁能改由服务端判（`company.read` / `company.manage`）：业务岗位都读得到
 * （开单要抬头与税号），只有产品管理员能改；能不能按税号查税务局看 `tax.lookup`。
 */
(() => {
	const p = frappe.pearnly;

	p.company = async (page, t, wrapper) => {
		const api = "pearnly_erp_portal.masters.api.";
		const root = "pearnly-company";
		const f = (fieldname, label, fieldtype = "Data", options = {}) =>
			({fieldname, label: t(label), fieldtype, ...options});
		// 这一屏的字段清单。**空值也要发**：框架的 `form.get_values()` 把空串当"没有这个
		// 字段"丢掉（`is_null("")` 为真），所以"清空一格再保存"照旧留着老值。
		const FIELDS = ["company_name", "short_name", "tax_id", "branch", "phone",
			"contact_person", "contact_email", "promptpay_id", "address", "note"];
		const payload = (form) => {
			const values = {};
			FIELDS.forEach(name => {
				const value = form.get_value(name);
				values[name] = value === null || value === undefined ? "" : value;
			});
			return values;
		};
		let form = null, list = null;

		// 填税号 → 点查询 → 抬头 / 税号 / 地址带出来（与供应商页同一套写法、同一个接口）。
		async function lookup_tax(form) {
			const tax_id = form.get_value("tax_id");
			if (!String(tax_id || "").replace(/\D/g, "").length) {
				frappe.msgprint(t("company.lookup.needTaxId"));
				return;
			}
			frappe.dom.freeze(t("company.lookup.querying"));
			try {
				const result = await p.call(api + "api_lookup_tax", {tax_id, branch: 0}, "POST");
				if (!result?.ok) {
					frappe.msgprint(t(`company.lookup.${result?.error || "failed"}`)
						|| t("company.lookup.failed"));
					return;
				}
				const data = result.data || {};
				await form.set_value("tax_id", data.tax_id || tax_id);
				await form.set_value("company_name", data.name || form.get_value("company_name") || "");
				await form.set_value("address", data.address || form.get_value("address") || "");
				if (Number(data.branch_no)) await form.set_value("branch", data.branch_no);
				p.success(t("company.lookup.found", data.name || data.tax_id || ""));
			} finally {
				frappe.dom.unfreeze();
			}
		}

		// ── 公司列表（一租户多公司）────────────────────────────────────────────
		async function master() {
			p.clear_page(page);
			form = null;
			// `nav.*` 是共用层的词（`browser_text` 把 nav. 归到 core），所以这里用 `__()`
			// 走框架的译文，不用本页那个按前缀取的 `t()`（那个只认 company. 开头）。
			page.set_title(__("nav.companyList"));
			p.breadcrumbs({label: __("nav.company"), route: p.subroute(root),
				trail: [{label: __("nav.companyList"), route: ""}]});
			list = p.list({page, doctype: "ERP Company", compact_columns: true,
				columns: [
					{fieldname: "company_name", label: t("company.col.name"), width: 180, min_width: 130},
					{fieldname: "short_name", label: t("company.col.shortName"), width: 110, min_width: 90},
					{fieldname: "tax_id", label: t("company.col.taxId"), width: 140, min_width: 110},
					{fieldname: "phone", label: t("company.col.phone"), width: 120, min_width: 96},
					{fieldname: "is_default", label: t("company.col.current"), width: 110, min_width: 90,
						format: (_value, row) => (row.name === frappe.boot.pearnly_company
							? t("company.yes") : "—")},
					{fieldname: "is_active", label: t("company.col.status"), width: 92, min_width: 74,
						// 本页的 `t()` 只认 `company.` 开头的键（按前缀取包），所以状态两态
						// 用自己的键，不去借 team./supplier. 那两个（借了只会显示键名）。
						format: value => (Number(value) ? t("company.status.active")
							: t("company.status.inactive")),
						cell_class: value => (Number(value) ? "" : "pearnly-payment-due")},
				],
				load: async () => {
					const rows = await p.call(api + "api_list_companies", {include_inactive: 1, limit: 0});
					return {items: rows, total: rows.length};
				},
				row_actions: row => row_actions(row),
				empty_action: can_write() ? {label: t("company.new"), action: () => editor(null)} : null});
			restore_new();
			p.add_refresh(page, () => list.reload());
			await list.ready;
		}

		function can_write() {
			return Boolean(frappe.model.can_write("ERP Company"));
		}

		function row_actions(row) {
			const actions = [];
			if (row.name !== frappe.boot.pearnly_company) {
				actions.push({id: "current", label: t("company.setCurrent"),
					action: () => set_current(row)});
			}
			if (can_write()) {
				actions.push({id: "edit", label: __("Edit"), action: () => editor(row)});
				actions.push({id: "active", label: row.is_active ? t("company.disable") : t("company.enable"),
					action: () => set_active(row)});
			}
			return actions.length ? actions : null;
		}

		function restore_new() {
			if (!can_write()) return page.clear_primary_action();
			page.set_primary_action(t("company.new"), () => editor(null));
		}

		// 新建 / 改：一张原生弹窗，字段与资料页同一批。
		function editor(row) {
			const is_new = !row;
			// 查询那颗按钮要拿到这张弹窗（`p.dialog` 建完才返回），所以先留个位置给它。
			const holder = {};
			const fields = [
				f("company_name", "company.field.name", "Data",
					{reqd: 1, default: row?.company_name || ""}),
				f("short_name", "company.field.shortName", "Data", {default: row?.short_name || ""}),
				f("tax_id", "company.field.taxId", "Data", {default: row?.tax_id || ""}),
				// 税号查询同样给新建/改这一屏（与供应商/客户两页同一个接口、同一颗按钮）。
				f("lookup", "company.lookup.action", "Button",
					{click: () => lookup_tax(holder.dialog)}),
				f("branch", "company.field.branch", "Data", {default: row?.branch || ""}),
				f("phone", "company.field.phone", "Data", {default: row?.phone || ""}),
				f("contact_person", "company.field.contactPerson", "Data",
					{default: row?.contact_person || ""}),
				f("contact_email", "company.field.contactEmail", "Data",
					{default: row?.contact_email || ""}),
				f("promptpay_id", "company.field.promptpay", "Data", {default: row?.promptpay_id || ""}),
				f("address", "company.field.address", "Small Text", {default: row?.address || ""}),
				f("note", "company.field.note", "Small Text", {default: row?.note || ""}),
				// 新公司**顺手建第一个仓库**（用户 2026-09-23：新建公司时就加这个入口）：
				// 没有仓库就入不了库、卖不了货；建好之后它就这家公司的默认仓，之后还能再加。
				...(is_new ? [
					{fieldtype: "Section Break", label: t("company.section.warehouse")},
					f("warehouse_name", "company.field.warehouse", "Data",
						{default: t("company.warehouseDefaultName")}),
				] : []),
			];
			holder.dialog = p.dialog(is_new ? t("company.new") : row.company_name, fields,
				is_new ? t("company.new") : __("Save"), async (values, dialog) => {
					await p.call(api + (is_new ? "api_create_company" : "api_update_company"),
						is_new ? values : {name: row.name, ...values}, "POST");
					dialog.hide();
					p.success(t(is_new ? "company.notice.created" : "company.notice.saved"));
					await list.reload();
					restore_new();
				});
		}

		// ── 当前公司的仓库（一租户多公司：仓库属于公司，库存按仓库记账）──────────
		async function warehouses() {
			p.clear_page(page);
			form = null;
			page.set_title(__("nav.companyWarehouses"));
			p.breadcrumbs({label: __("nav.company"), route: p.subroute(root),
				trail: [{label: __("nav.companyWarehouses"), route: ""}]});
			// 当前公司由服务端在 boot 里给（`masters/boot.py`）。
			const current = frappe.boot.pearnly_company;
			const can_manage = can_write();
			list = p.list({page, doctype: "ERP Warehouse", compact_columns: true,
				columns: [
					{fieldname: "warehouse_name", label: t("company.col.warehouse"),
						width: 200, min_width: 140},
					{fieldname: "is_default", label: t("company.col.defaultWarehouse"),
						width: 120, min_width: 96,
						format: value => (Number(value) ? t("company.yes") : "—")},
					{fieldname: "is_active", label: t("company.col.status"), width: 92, min_width: 74,
						format: value => (Number(value) ? t("company.status.active")
							: t("company.status.inactive")),
						cell_class: value => (Number(value) ? "" : "pearnly-payment-due")},
					{fieldname: "note", label: t("company.field.note"), width: 180, min_width: 120},
				],
				on_open: row => (can_manage ? edit_warehouse(row) : null),
				row_actions: can_manage ? row => [
					{id: "default", label: t("company.setDefaultWarehouse"),
						disabled: Number(row.is_default) === 1,
						action: () => make_default(row)},
					{id: "active", label: Number(row.is_active) ? t("company.disable")
						: t("company.enable"), action: () => toggle_warehouse(row)},
				] : null,
				load: async () => {
					const rows = await p.call(api + "api_company_warehouses",
						{company_name: current, include_inactive: 1});
					return {items: rows, total: rows.length};
				},
				empty_action: can_manage
					? {label: t("company.newWarehouse"), action: () => edit_warehouse(null)} : null});
			if (can_manage) {
				page.set_primary_action(t("company.newWarehouse"), () => edit_warehouse(null));
			} else {
				page.clear_primary_action();
			}
			p.add_refresh(page, () => list.reload());
			await list.ready;
		}

		function edit_warehouse(row) {
			const is_new = !row;
			p.dialog(is_new ? t("company.newWarehouse") : row.warehouse_name, [
				f("warehouse_name", "company.field.warehouse", "Data",
					{reqd: 1, default: row?.warehouse_name || ""}),
				f("note", "company.field.note", "Small Text", {default: row?.note || ""}),
			], is_new ? t("company.newWarehouse") : __("Save"), async (values, dialog) => {
				if (is_new) {
					await p.call("pearnly_erp_portal.inventory.api.api_create_warehouse",
						{warehouse_name: values.warehouse_name, note: values.note, is_default: 0},
						"POST");
				} else {
					await p.call("pearnly_erp_portal.inventory.api.api_update_warehouse",
						{name: row.name, warehouse_name: values.warehouse_name, note: values.note},
						"POST");
				}
				dialog.hide();
				p.success(t("company.notice.saved"));
				await list.reload();
			});
		}

		async function make_default(row) {
			await p.call(api + "api_set_default_warehouse", {name: row.name}, "POST");
			p.success(t("company.notice.defaultWarehouse", row.warehouse_name));
			await list.reload();
		}

		async function toggle_warehouse(row) {
			await p.call("pearnly_erp_portal.inventory.api.api_update_warehouse",
				{name: row.name, is_active: Number(row.is_active) ? 0 : 1}, "POST");
			p.success(t("company.notice.saved"));
			await list.reload();
		}

		// 设为当前公司：写自己的用户默认值（官方那个 `company` 键），整页重载跟着换。
		async function set_current(row) {
			await p.call(api + "api_set_active_company", {name: row.name}, "POST");
			window.location.reload();
		}

		async function set_active(row) {
			await p.call(api + "api_update_company",
				{name: row.name, is_active: Number(row.is_active) ? 0 : 1}, "POST");
			p.success(t("company.notice.toggled"));
			await list.reload();
		}

		// ── 当前公司的资料 ─────────────────────────────────────────────────────
		async function profile() {
			p.clear_page(page);
			list = null;
			page.set_title(__("nav.company"));
			p.breadcrumbs({label: __("nav.company"), route: p.subroute(root)});
			const data = await p.call(api + "api_get_profile");
			page.set_title_sub(data.company_name || "");
			const ro = {read_only: !data.can_write};
			form = p.layout(page.main, [
				{fieldtype: "Section Break", label: t("company.section.basic")},
				f("company_name", "company.field.name", "Data",
					{reqd: 1, default: data.company_name || "", ...ro}),
				f("short_name", "company.field.shortName", "Data",
					{default: data.short_name || "", ...ro}),
				f("tax_id", "company.field.taxId", "Data", {default: data.tax_id || "", ...ro}),
				...(data.can_lookup ? [f("lookup", "company.lookup.action", "Button",
					{click: () => lookup_tax(form)})] : []),
				f("branch", "company.field.branch", "Data", {default: data.branch || "", ...ro}),
				{fieldtype: "Column Break"},
				f("phone", "company.field.phone", "Data", {default: data.phone || "", ...ro}),
				f("contact_person", "company.field.contactPerson", "Data",
					{default: data.contact_person || "", ...ro}),
				f("contact_email", "company.field.contactEmail", "Data",
					{options: "Email", default: data.contact_email || "", ...ro}),
				f("promptpay_id", "company.field.promptpay", "Data",
					{default: data.promptpay_id || "", ...ro}),
				{fieldtype: "Section Break", label: t("company.section.address")},
				f("address", "company.field.address", "Small Text",
					{default: data.address || "", ...ro}),
				{fieldtype: "Column Break"},
				f("note", "company.field.note", "Small Text", {default: data.note || "", ...ro}),
			]);
			if (data.can_write) {
				page.set_primary_action(t("company.save"), async () => {
					const values = payload(form);
					if (!String(values.company_name).trim()) {
						// 用框架自己那两条 msgid（已译好），不另造文案。
						return frappe.msgprint({
							title: __("Missing Values Required"),
							message: __("Following fields have missing values:")
								+ `<br><br><ul><li>${p.escape(t("company.field.name"))}</ul>`,
							indicator: "orange",
						});
					}
					await p.call(api + "api_update_profile", values, "POST");
					p.success(t("company.notice.saved"));
				});
			} else {
				page.clear_primary_action();
			}
			// 次按钮进公司列表：一张表两张面孔，来回都在这一个页面上。
			page.add_inner_button(__("nav.companyWarehouses"), () => go_to("warehouses"));
			page.add_inner_button(__("nav.companyList"), () => go_to("master"));
			// 右上角那颗刷新：重取一次值填回表单（不整页重画），免得把正在填的内容抹掉。
			p.add_refresh(page, async () => {
				form.set_values(await p.call(api + "api_get_profile"));
			});
		}

		const go_to = (...parts) => frappe.set_route(root, ...parts);
		const show = p.routed_page({wrapper, root, render: async (rest, still_current) => {
			if (rest[0] === "master") {
				await master();
				return;
			}
			if (rest[0] === "warehouses") {
				await warehouses();
				return;
			}
			await profile();
			if (!still_current()) return;
		}});

		await show();
		return () => list?.destroy();
	};
})();
