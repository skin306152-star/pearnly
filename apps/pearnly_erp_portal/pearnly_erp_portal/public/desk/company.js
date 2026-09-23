/* 公司：**一租户多公司**（用户 2026-09-23："我要做一租户多公司"）。
 *
 * 一张表两张面孔（`ERP Company`），路由分两段：
 *
 *   · `/desk/pearnly-company`          → **公司列表**（点菜单先看到它）：登记过哪几家；
 *                                        行内不放按钮，勾选之后右上角才出现「停用 / 启用」；
 *   · `/desk/pearnly-company/<公司名>` → **公司信息**（点列表任意一行进来）：抬头、简称、
 *                                        税号、分支、联系方式、收款 PromptPay、地址与备注，
 *                                        以及「设为当前公司」。改得动它的只有产品管理员。
 *
 * 仓库**不在这一页**：它有自己的入口（侧栏「公司」下面那项，`desk/warehouses.js`）。
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
		// 列表上的勾选动作（共享层那一套）：选中之后右上角才出现按钮，停用状态的行显示「启用」。
		let selection = null;

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

		// ── 公司列表（一租户多公司）：点菜单先看到这一屏 ─────────────────────────
		async function master() {
			p.clear_page(page);
			form = null;
			page.set_title(__("nav.company"));
			p.breadcrumbs({label: __("nav.company"), route: p.subroute(root)});
			selection = p.selection_action({
				page, list: () => list, empty: restore_new,
				label: rows => (rows.every(row => !Number(row.is_active))
					? t("company.action.bulkEnable", rows.length)
					: t("company.action.bulkDisable", rows.length)),
				when: row => can_write(),
				reason: () => t("company.onlyAdmin"),
				run: async rows => {
					const enabling = rows.every(row => !Number(row.is_active));
					for (const row of rows) {
						await p.call(api + "api_update_company",
							{name: row.name, is_active: enabling ? 1 : 0}, "POST");
					}
				},
				success: rows => t("company.notice.toggled", rows.length),
			});
			list = p.list({page, doctype: "ERP Company", compact_columns: true,
				selectable: true, on_selection: selection,
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
				// 单击任意一行 → 进这一家的**公司信息**（面包屑二级），行内不再放按钮。
				on_open: row => frappe.set_route(root, row.name),
				row_actions: null,
				empty_action: can_write() ? {label: t("company.new"), action: () => editor(null)} : null});
			restore_new();
			p.add_refresh(page, () => list.reload());
			await list.ready;
		}

		function can_write() {
			return Boolean(frappe.model.can_write("ERP Company"));
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

		// ── 公司信息（点列表任意一行进来）────────────────────────────────────────
		async function profile(name) {
			p.clear_page(page);
			list = null;
			const data = await p.call(api + "api_get_profile", name ? {name} : {});
			page.set_title(data.company_name || name);
			// 面包屑：公司 / <这家公司>（点公司那一级回到列表）。
			p.breadcrumbs({label: __("nav.company"), route: p.subroute(root),
				trail: [{label: data.company_name || name || "", route: ""}]});
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
					await p.call(name ? "pearnly_erp_portal.masters.api.api_update_company"
						: api + "api_update_profile",
						name ? {name, ...values} : values, "POST");
					p.success(t("company.notice.saved"));
				});
			} else {
				page.clear_primary_action();
			}
			// 「设为当前公司」只在这一屏给（列表上不再放行内按钮）：写的是自己的用户默认值。
			if (name && name !== frappe.boot.pearnly_company) {
				page.add_inner_button(t("company.setCurrent"), () => set_current({name}));
			}
			// 右上角那颗刷新：重取一次值填回表单（不整页重画），免得把正在填的内容抹掉。
			p.add_refresh(page, async () => {
				form.set_values(await p.call(api + "api_get_profile", name ? {name} : {}));
			});
		}

		const go_to = (...parts) => frappe.set_route(root, ...parts);
		const show = p.routed_page({wrapper, root, render: async (rest, still_current) => {
			// 默认（点菜单进来）就是**公司列表**；`/公司名` 才是那家的公司信息。
			if (!rest.length) {
				await master();
				return;
			}
			await profile(decodeURIComponent(rest[0]));
			if (!still_current()) return;
		}});

		await show();
		return () => list?.destroy();
	};
})();
