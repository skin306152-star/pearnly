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
			"contact_person", "contact_email", "promptpay_id", "address", "note", "pos_enabled"];
		const POS_METHODS = ["cash", "card", "promptpay", "transfer"];
		// 收款方式的四个标签**用术语表里已有的词**（`Cash` / `Card` / `PromptPay Amount` /
		// `Transfer`），不新造 company.method.* 那种词条。
		const METHOD_LABELS = {cash: "Cash", card: "Card", promptpay: "PromptPay Amount",
			transfer: "Transfer"};
		const method_field = (key, extra = {}) => ({fieldname: `method_${key}`,
			label: __(METHOD_LABELS[key]), fieldtype: "Check", default: 1, ...extra});
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
				empty_action: can_write()
					? {label: t("company.new"), action: () => go_to("new")} : null});
			restore_new();
			p.add_refresh(page, () => list.reload());
			await list.ready;
			// 别处的选择框指过来时（`link.js` 的 one-shot）：新建公司 / 打开这家公司。
			if (can_write()) {
				await p.take_create("pearnly-company", "ERP Company", () => go_to("new"));
			}
			await p.take_open("pearnly-company", "ERP Company", name => frappe.set_route(root, name));
		}

		function can_write() {
			return Boolean(frappe.model.can_write("ERP Company"));
		}

		function restore_new() {
			if (!can_write()) return page.clear_primary_action();
			// 新建走**二级页面**（面包屑），不再弹窗 —— 字段太多（用户 2026-09-24）。
			page.set_primary_action(t("company.new"), () => go_to("new"));
		}

		// ── 新建公司（**一整页**，不是弹窗）─────────────────────────────────────────
		//
		// 用户 2026-09-24 的口径："新建公司进入面包屑填写，字段太多了，不要弹窗"；
		// "新增是否启用 POS 收银台 …… 勾选后填写相关 pos 的配置，形成和公司绑定关系"；
		// "仓库也可以放在建公司时填写 …… 仓库可以加多个，收银台也是"；
		// "新建公司可以把有些主档全部配齐，收款方式添加，默认给他几个字段"。
		//
		// 所以这一页一次配齐：**主体资料 → 收银开关与收款方式 → 仓库（多行）→ 收银台（多台）**。
		// 提交只发一次（服务端 `company.create_company` 一把建完并互相绑定：每台收银台显式指向
		// 上面某个仓）。收款方式与收银开关的**取舍**都落在公司上，后面各页据此过滤。
		async function create_page() {
			p.clear_page(page);
			list = null;
			page.set_title(t("company.new"));
			p.breadcrumbs({label: __("nav.company"), route: p.subroute(root),
				trail: [{label: t("company.new"), route: ""}]});
			const form = p.layout(page.main, [
				// 两列排布（用户 2026-09-24："字段视角对齐，排版要合理"）：一列一列对齐、
				// 别拉成一长条 —— 与公司信息那一屏同一套 Section/Column 写法。
				{fieldtype: "Section Break", label: t("company.section.basic")},
				f("company_name", "company.field.name", "Data", {reqd: 1}),
				{fieldtype: "Column Break"},
				f("short_name", "company.field.shortName", "Data"),
				f("tax_id", "company.field.taxId", "Data"),
				// 税号查询与供应商/客户/公司信息三处同一颗按钮、同一个接口。
				f("lookup", "company.lookup.action", "Button", {click: () => lookup_tax(form)}),
				{fieldtype: "Column Break"},
				f("branch", "company.field.branch", "Data"),
				f("phone", "company.field.phone", "Data"),
				{fieldtype: "Column Break"},
				f("contact_person", "company.field.contactPerson", "Data"),
				f("contact_email", "company.field.contactEmail", "Data"),
				{fieldtype: "Column Break"},
				f("promptpay_id", "company.field.promptpay", "Data"),
				// ── 收银：开关 + 收哪几种钱 ────────────────────────────────────
				// 文案一律用**术语表里已有的词**（收银台 / 付款方式 / 现金 / 刷卡 / 扫码 / 转账），
				// 不再自己写说明句（用户 2026-09-24："做新东西不要随意加文案"）。
				{fieldtype: "Section Break"},
				{fieldname: "pos_enabled", label: __("Terminal"), fieldtype: "Check", default: 1},
				{fieldtype: "Section Break", label: __("Payment Method")},
				...POS_METHODS.flatMap((key, index) => (index === 0
					? [method_field(key)]
					: [{fieldtype: "Column Break"}, method_field(key)])),
				// ── 仓库：可以多个（第一个就是"先建的那个仓"）────────────────────
				{fieldtype: "Section Break", label: t("company.field.warehouse")},
				{fieldtype: "Table", fieldname: "warehouses", cannot_add_rows: 0,
					data: [{warehouse_name: t("company.warehouseDefaultName")}],
					fields: [{fieldname: "warehouse_name", fieldtype: "Data",
						label: t("company.field.warehouse"), in_list_view: 1, reqd: 1}]},
				// ── 收银台：可以多台，每台写明从哪个仓卖货 ──────────────────────
				{fieldtype: "Section Break", label: __("Terminal")},
				{fieldtype: "Table", fieldname: "terminals", cannot_add_rows: 0,
					data: [{terminal_name: "Counter 1", warehouse: ""}],
					fields: [
						{fieldname: "terminal_name", fieldtype: "Data",
							label: __("Terminal"), in_list_view: 1, reqd: 1},
						{fieldname: "warehouse", fieldtype: "Data",
							label: t("company.field.warehouse"), in_list_view: 1},
					]},
				{fieldtype: "Section Break"},
				f("address", "company.field.address", "Small Text"),
				f("note", "company.field.note", "Small Text"),
			]);
			const rows_of = fieldname => (form.get_value(fieldname) || [])
				.map(row => ({...row}));
			page.set_secondary_action(__("Cancel"), () => go_to());
			page.set_primary_action(t("company.new"), async () => {
				const values = payload(form);
				if (!String(values.company_name || "").trim()) {
					return frappe.msgprint({
						title: __("Missing Values Required"),
						message: __("Following fields have missing values:")
							+ `<br><br><ul><li>${p.escape(t("company.field.name"))}</ul>`,
						indicator: "orange",
					});
				}
				const warehouses = rows_of("warehouses")
					.map(row => String(row.warehouse_name || "").trim()).filter(Boolean);
				const terminals = rows_of("terminals")
					.map(row => ({terminal_name: String(row.terminal_name || "").trim(),
						warehouse: String(row.warehouse || "").trim()}))
					.filter(row => row.terminal_name);
				const methods = POS_METHODS.filter(key => Number(form.get_value(`method_${key}`)));
				await p.call(api + "api_create_company", {
					...values, warehouses, terminals, payment_methods: methods,
					pos_enabled: Number(form.get_value("pos_enabled")) ? 1 : 0,
				}, "POST");
				p.success(t("company.notice.created"), "click");
				go_to();                       // 回公司列表；点进去就是刚建的这一家
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
			// 建公司时配的那套（收银台）在这里能改：判据是服务端的 `company.manage`
			// （见 `api_get_profile` 的 `can_manage_pos`）。
			const can_edit_pos = Boolean(data.can_manage_pos) && Boolean(data.can_write);
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
				// ── 收银：开关 + 收哪几种钱 + 这家公司的收银台清单 ────────────────
				// 建公司时配的这一套在这里能改；文案同样只用现成的词。
				{fieldtype: "Section Break"},
				{fieldname: "pos_enabled", label: __("Terminal"), fieldtype: "Check",
					default: Number(data.pos_enabled) ? 1 : 0, ...ro},
				{fieldtype: "Section Break", label: __("Payment Method")},
				...POS_METHODS.flatMap((key, index) => (index === 0
					? [method_field(key, {default: (data.payment_methods || []).includes(key) ? 1 : 0, ...ro})]
					: [{fieldtype: "Column Break"},
						method_field(key, {default: (data.payment_methods || []).includes(key) ? 1 : 0, ...ro})])),
				// 收银台清单用**原生表格**（架构检查也拦自定义 HTML 表格）：
				// 一行一台，改名字 / 换卖货的仓 / 启用停用都在格子里改，随这一屏一起保存。
				// **不给删行**：要停用就把状态那格取消勾选（数据留着，历史小票还指着它）。
				{fieldtype: "Table", fieldname: "terminals", label: "",
					cannot_add_rows: !can_edit_pos, cannot_delete_rows: 1,
					data: (data.terminals || []).map(row => ({name: row.name,
						terminal_name: row.terminal_name, warehouse: row.warehouse,
						is_active: Number(row.is_active) ? 1 : 0})),
					fields: [
						{fieldname: "terminal_name", fieldtype: "Data", reqd: 1, in_list_view: 1,
							label: __("Terminal")},
						{fieldname: "warehouse", fieldtype: "Data", in_list_view: 1,
							label: t("company.field.warehouse")},
						{fieldname: "is_active", fieldtype: "Check", in_list_view: 1, default: 1,
							label: t("company.col.status")},
					]},
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
					// 收款方式单独走一条（它是子表，不在公司那几个字段里）。
					await p.call(api + "api_set_payment_methods",
						{company_name: name || undefined,
							methods: POS_METHODS.filter(key => Number(form.get_value(`method_${key}`)))},
						"POST");
					// 收银台那张表：格子里的改动一次提交（新增 / 改名 / 换仓 / 启停）。
					await p.call(api + "api_save_company_terminals",
						{company_name: name || undefined,
							rows: form.get_value("terminals") || []}, "POST");
					p.success(t("company.notice.saved"), "click");
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
			const segment = decodeURIComponent(rest[0]);
			// `/公司/new` 是**新建公司**这一页（字段太多，用户 2026-09-24："不要弹窗，
			// 新建公司进入面包屑填写"）。公司名是哈希，不会撞上 `new` 这个字。
			if (segment === "new") {
				await create_page();
				return;
			}
			await profile(segment);
			if (!still_current()) return;
		}});

		await show();
		return () => list?.destroy();
	};
})();
