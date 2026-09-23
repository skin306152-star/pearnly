/* 公司：本租户自己的主体资料，**一屏**（`/desk/pearnly-company`）。
 *
 * 用户 2026-09-23 口径："不要公司资料公司主档，就一个公司就好了，包含全部" ——
 * 一个租户一家公司，抬头、简称、税号、分支、联系方式、收款 PromptPay、地址与备注
 * 都在这一屏上；原来分「公司资料 / 公司主档」两张面孔，现在合成一张，也没有
 * "新建 / 设默认 / 停用"那一套。
 *
 * **不摆官方没有的东西**：原先还有「单据前缀」与「财年起月」两格，是上一轮自己加的 ——
 * 官方（ERPNext 的 Company）里没有这两个字段（官方的财年是独立的 `Fiscal Year`，单号走
 * 命名序列，两样都不在这个表上），而且这两格现在也不接任何地方，所以撤掉（用户
 * 2026-09-23："官方有单据前戳吗？官方有财年起月吗？…用官方的"）。
 *
 * 表仍是框架原生的 `ERP Company`，站上就一条默认公司。**隔离是平台原生那一层**：
 * 一个租户一个站点、一套库（Site 级），所以"本租户的公司"就是这张表里那一条 ——
 * 单据与收据上的抬头（采购的代收凭证 / 预扣税凭证、导出件抬头）都从它取。
 *
 * 谁能看、谁能改由服务端判（`company.read` / `company.manage`）：业务岗位都读得到
 * （开单要抬头与税号），只有产品管理员能改 —— 页面按服务端回的 `can_write` 决定字段
 * 是否只读，越权请求照样被拒。税号那格旁边的「查询」与供应商 / 客户两页是同一件事、
 * 同一个接口（`api_lookup_tax`，泰国税务局的官方登记），有没有这个能力也由服务端说。
 */
(() => {
	const p = frappe.pearnly;

	p.company = async (page, t) => {
		const api = "pearnly_erp_portal.masters.api.";
		const f = (fieldname, label, fieldtype = "Data", options = {}) =>
			({fieldname, label: t(label), fieldtype, ...options});
		// 这一屏的字段清单。**空值也要发**：框架的 `form.get_values()` 把空串当"没有这个
		// 字段"丢掉（`is_null("")` 为真），所以"清空一格再保存"照旧留着老值 —— 2026-09-23
		// 实测踩到（清掉备注保存后回读还是原文）。这里按自己的清单逐个取，空的就是空。
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

		// 填税号 → 点查询 → 抬头 / 税号 / 地址带出来（与供应商页同一套写法）。
		// 查不到、或外部接口连不上，服务端只回原因、不抛错，录入照常能存。
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

		async function paint() {
			p.clear_page(page);
			page.set_title(__("nav.company"));
			p.breadcrumbs({label: __("nav.company"), route: p.subroute("pearnly-company")});
			const data = await p.call(api + "api_get_profile");
			// 只读与否以**服务端的判据**为准（`company.manage`），前端不复刻一套。
			const ro = {read_only: !data.can_write};
			const form = p.layout(page.main, [
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
			// 右上角那颗刷新：重取一次值填回表单（不整页重画），免得把正在填的内容抹掉。
			p.add_refresh(page, async () => {
				form.set_values(await p.call(api + "api_get_profile"));
			});
		}

		await paint();
	};
})();
