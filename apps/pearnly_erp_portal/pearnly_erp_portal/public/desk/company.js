/* 公司：**一租户多公司**（用户 2026-09-23："我要做一租户多公司"）。
 *
 * 一张表两张面孔（`ERP Company`），路由分两段：
 *
 *   · `/desk/pearnly-company`          → **公司列表**（点菜单先看到它）：登记过哪几家；
 *                                        行内不放按钮，勾选之后右上角才出现「停用 / 启用」；
 *   · `/desk/pearnly-company/<公司名>` → **公司信息**（点列表任意一行进来）：抬头、简称、
 *                                        税号、分支、联系方式、收款 PromptPay、地址与备注。
 *                                        改得动它的只有产品管理员。换当前公司只在左上角公司名下拉里
 *                                        （用户 11-15 删了这一屏的「设为当前」，`desk/company-menu.js`）。
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
		const FIELDS = ["company_name", "short_name", "tax_id", "branch", "vat_registered", "phone",
			"contact_person", "contact_email", "promptpay_id", "logo", "address", "note", "pos_enabled",
			"company_type"];
		// 公司类型（用户 10-29）：汽车 / 手机电器按台管理，普通公司不按台
		const TYPE_OPTIONS = ["general", "vehicle", "device"].map(value => ({value, label: t(`company.type.${value}`)}));
		// CC 桌面版安装包（始终是最新正式版，`companion/README.md`）
		const CC_INSTALLER = "https://pearnly.com/downloads/companion/CC-Setup.exe";
		const POS_METHODS = ["cash", "card", "promptpay", "transfer"];
		const VAT_OPTIONS = [{value: "", label: ""}, {value: "yes", label: __("express.option.yes")}, {value: "no", label: __("express.option.no")}];
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
					if (result?.error === "not_vat_registered" && form.fields_dict.vat_registered) {
						await form.set_value("vat_registered", "no");
					}
					frappe.msgprint(t(`company.lookup.${result?.error || "failed"}`)
						|| t("company.lookup.failed"));
					return;
				}
				const data = result.data || {};
				await form.set_value("tax_id", data.tax_id || tax_id);
				await form.set_value("company_name", data.name || form.get_value("company_name") || "");
				await form.set_value("address", data.address || form.get_value("address") || "");
				if (Number(data.branch_no)) await form.set_value("branch", data.branch_no);
				if (form.fields_dict.vat_registered) await form.set_value("vat_registered", "yes");  // 税务局 VAT 名录里查得到
				p.success(t("company.lookup.found", data.name || data.tax_id || ""), null);  // 只是填进表单、还没保存：官方此时不出声
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
				page, list: () => list, sound: "click", empty: restore_new,
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
				// 按页取、在服务端排序（公司按 Express 账套自动建，一个事务所可以有几百家）。
				// "当前公司"那一列是按登录人的当前公司算出来的，排它等于排一个不存在于数据里的值，不列进来。
				sort_fields: ["company_name", "short_name", "tax_id", "phone", "is_active"],
				load: async ({start, page_length, sort_by, sort_order}) => {
					const [items, total] = await Promise.all([
						p.call(api + "api_list_companies", {include_inactive: 1, start, limit: page_length, sort_by, sort_order}),
						p.call(api + "api_count_companies", {include_inactive: 1}),
					]);
					return {items, total};
				},
				// 单击任意一行 → 进这一家的**公司信息**（面包屑二级），行内不再放按钮。
				on_open: row => frappe.set_route(root, row.name),
				row_actions: null,
				empty_action: can_write()
					? {label: t("company.new"), action: () => go_to("new")} : null});
			restore_new();
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
			// 从连接添加（公司接入重做第 2 批）：能建公司的人才有；勾完回到列表刷新
			if (frappe.boot.pearnly_can_build) {
				page.set_secondary_action(__("company.connect.add"), () => p.connect_companies({done: () => list?.reload?.()}));
			} else {
				page.clear_secondary_action();
			}
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
				// 两列排布（用户 2026-09-24："字段视角对齐，排版要合理"）：一列一列对齐、别拉成一长条。
				// 与公司信息那一屏同一套两栏（用户 10-31）：左栏公司与税务，右栏联系与收款、再是公司类型与接 Express
				// （11-01：公司类型原在左栏末尾，左七格右五格）；地址 | 备注一节也同详情页，紧跟在下面。
				{fieldtype: "Section Break", label: t("company.section.basic")},
				f("company_name", "company.field.name", "Data", {reqd: 1}),
				f("short_name", "company.field.shortName", "Data"),
				f("tax_id", "company.field.taxId", "Data"),
				// 税号查询与供应商/客户/公司信息三处同一颗按钮、同一个接口。
				f("lookup", "company.lookup.action", "Button", {click: () => lookup_tax(form)}),
				f("branch", "company.field.branch", "Data"),
				// 增值税登记户（收银标价含税，登记户的小票逐行拆出税额）：税号查询带出，也可以手选
				f("vat_registered", "company.field.vatRegistered", "Select", {options: VAT_OPTIONS}),
				{fieldtype: "Column Break"},
				f("phone", "company.field.phone", "Data"),
				f("contact_person", "company.field.contactPerson", "Data"),
				f("contact_email", "company.field.contactEmail", "Data"),
				f("promptpay_id", "company.field.promptpay", "Data"),
				// 「接 Express」勾选去掉（公司接入重做第 2 批，11-14）：这里新建的只用 Pearnly ERP；接账本的公司从「从连接添加」来
				f("company_type", "company.field.companyType", "Select", {options: TYPE_OPTIONS, default: "general"}),
				{fieldtype: "Section Break", label: t("company.section.address")},
				f("address", "company.field.address", "Small Text"),
				{fieldtype: "Column Break"},
				f("note", "company.field.note", "Small Text"),
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
					data: [{terminal_name: t("company.terminalDefaultName"), warehouse: ""}],
					fields: [
						{fieldname: "terminal_name", fieldtype: "Data",
							label: __("Terminal"), in_list_view: 1, reqd: 1},
						{fieldname: "warehouse", fieldtype: "Data",
							label: t("company.field.warehouse"), in_list_view: 1},
					]},
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
				await p.refresh_company_context?.();
				go_to();                       // 回公司列表；点进去就是刚建的这一家
			});
		}

		// ── Express 同步状态 ─────────────────────────────────────────────────────
		const SYNC_STATES = ["queued", "reading", "syncing", "ready", "offline", "outdated"];
		const sync_key = state => (SYNC_STATES.includes(state) ? state : "failed");
		// 失败原因：认得的原因码给译文，其余（出错信息）原样
		const SYNC_NOTES = {company_tax_missing: "express.reason.taxMissing", account_set_missing: "express.reason.noBook",
			"express.account_set_several": "Choose the account set"};
		// MR.ERP 主档整读失败的原因码（`pearnly_core.mrerp.master_export.read`）→ 现成的 MR.ERP 文案；其余 ERR_* 照 mrerp.error.<码>
		const MRERP_NOTES = {ERR_ACCOUNT_IN_USE: "mrerp.error.ERR_MRERP_ACCOUNT_IN_USE", ERR_NO_CREDS: "mrerp.reason.loginMissing",
			account_set_missing: "express.reason.noBook", "express.account_set_several": "Choose the account set"};
		// 来源决定是否互通（公司接入重做第 2 批，11-14）：不接 Express 的公司不出这一节（没有勾选框）；从连接添加进来的才有。
		// 接着但未连上（CC 桌面版没报过这家公司的账套）只给下载 CC 桌面版；连上了能「断开」（断开后回到只用 Pearnly ERP）。
		const express_section = (data, name) => {
			const on = Number(data.express_enabled) === 1, connected = Boolean(data.express_connected);
			if (!on) return [];
			const out = [{fieldtype: "Section Break", label: __("Express")}];
			if (!connected) out.push(
				{fieldname: "express_connection", label: __("Status"), fieldtype: "Data", read_only: 1,
					default: t("company.syncExpress.offline")},
				{fieldname: "cc_download", label: __("Download CC Desktop"), fieldtype: "Button",
					click: () => window.open(CC_INSTALLER, "_blank", "noopener")});
			if (connected && data.can_write) out.push(
				f("express_disconnect", "company.express.disconnect", "Button", {click: () => frappe.confirm(
					__("Are you sure you want to proceed?"), async () => {
						await p.call(api + "api_disconnect_express", {name: name || data.name}, "POST");
						p.success(t("company.notice.saved"), "click");
						await p.refresh_company_context?.();
						await show();
					})}));
			return out;
		};
		// ── MR.ERP 账本（服务端 `mrerp.catalog.company_view` 给了才有：开关开着、ERP 管理员、没接 Express、站点有 MR.ERP 主连接）──
		// 现在用的账套只读；「选择」开官方对话框（不用 "Change"：ERP 术语表里那是收银的「找零」），一颗可输入搜索的下拉（官方 Autocomplete），按输入在服务端按页搜这条连接的账套；
		// 用着 MR.ERP 的给「断开」（之后提交的单不导入，已导入的不动；服务端同样只限管理员）。
		const MRERP = "pearnly_erp_portal.mrerp.api.";
		const choose_mrerp = (data, name) => {
			const view = data.mrerp;
			const label_of = row => [row.label, row.company].filter(Boolean).join(" · ");
			const dialog = new frappe.ui.Dialog({title: __("Account Set"),
				fields: [{fieldtype: "Autocomplete", fieldname: "account_set", label: __("Search"), options: []}],
				primary_action_label: __("Select"),
				async primary_action(values) {
					if (!values.account_set) return;
					await p.call(MRERP + "choose_ledger", {company: name || data.name, connection_name: view.connection,
						account_set: values.account_set}, "POST");
					dialog.hide();
					p.success(t("company.notice.saved"), "click");
					await show();
				}});
			const fill = async text => {
				const page = await p.call(MRERP + "account_sets",
					{connection_name: view.connection, start: 0, page_length: 50, search: text || ""});
				dialog.fields_dict.account_set.set_data((page.rows || []).map(row => ({value: row.key, label: label_of(row)})));
			};
			dialog.fields_dict.account_set.$input.on("input", frappe.utils.debounce(
				() => void fill(dialog.fields_dict.account_set.$input.val()), 300));
			dialog.show();
			void fill("");
		};
		const mrerp_section = (data, name) => {
			const view = data.mrerp;
			// 只有从连接添加进来、接着 MR.ERP 的公司出这一节（换账套、断开）；手工新建的公司不再在这里接上 MR.ERP（来源决定）
			if (!view || !view.linked) return [];
			return [{fieldtype: "Section Break", label: __("MR.ERP")},
				// 这家 MR.ERP 公司的各年度账套（按单据日期选那一本）；期间读不到的标「—」
				{fieldname: "mrerp_account_set", label: __("Account Set"), fieldtype: "Small Text", read_only: 1,
					default: (view.books || []).map(book => `${book.label} ${book.period_start
						? `${book.period_start.slice(0, 4)}–${book.period_end.slice(0, 4)}` : "—"}`).join("\n") || view.label || ""},
				{fieldname: "mrerp_choose", label: __("Select"), fieldtype: "Button", click: () => choose_mrerp(data, name)},
				// 主档整读（服务端读 MR.ERP，`mrerp.masters`）：状态、最近一次时间，只读；失败原因另起一行
				{fieldtype: "Column Break"},
				{fieldname: "mrerp_sync_state", label: __("Status"), fieldtype: "Data", read_only: 1,
					default: view.sync_state ? t(`company.syncExpress.${sync_key(view.sync_state)}`) : ""},
				{fieldname: "mrerp_synced_at", label: __("Last Synced On"), fieldtype: "Datetime", read_only: 1,
					default: view.synced_at || ""},
				...(view.sync_note ? [{fieldtype: "Section Break"}, {fieldname: "mrerp_sync_note", label: __("Reason"),
					fieldtype: "Small Text", read_only: 1, default: view.sync_note === "masters_failed" ? t("company.syncExpress.failed")
						: MRERP_NOTES[view.sync_note] ? __(MRERP_NOTES[view.sync_note]) : __(`mrerp.error.${view.sync_note}`)}] : []),
				{fieldtype: "Section Break"},
				...(view.linked ? [f("mrerp_detach", "company.express.disconnect", "Button", {click: () => frappe.confirm(
					__("Are you sure you want to proceed?"), async () => {
						await p.call(MRERP + "detach", {company: name || data.name}, "POST");
						p.success(t("company.notice.saved"), "click");
						await show();
					})})] : [])];
		};
		// 还没写进 Express 的退货小票（退货单在排队、等原日汇总写入或停下）
		const pending_refunds = express => (express.pending_refunds ? [{fieldname: "express_pending_refunds",
			label: __("express.pendingRefunds"), fieldtype: "Int", read_only: 1, default: express.pending_refunds}] : []);
		const express_fields = (express, company) => [
			// 两栏成行（11-01）：账套 | 同步时间，状态 | 原因；待写退货放在短的那一栏（原来左二格、右最多四格）
			{fieldname: "express_book", label: __("Account Set"), fieldtype: "Data", read_only: 1,
				default: express.express_book || ""},
			// 同期几份账套没选过：主档同步停下，在这里选一次（与写单同一份记忆；11-15 不再默认读第一份，可能是 C 盘副本）
			...((express.book_options || []).length ? [{fieldname: "express_book_choice", label: __("Account Set"),
				fieldtype: "Select", options: [{value: "", label: ""}, ...express.book_options.map(row => ({value: row.code, label: row.name}))],
				change: async () => {
					const key = form?.get_value?.("express_book_choice");
					if (!key) return;
					await p.call("pearnly_erp_portal.express.api.choose_masters_book", {company, key}, "POST");
					p.success(t("company.notice.saved"), "click");
					await show();
				}}] : []),
			{fieldname: "express_sync_state", label: __("Status"), fieldtype: "Data", read_only: 1,
				default: express.express_sync_state ? t(`company.syncExpress.${sync_key(express.express_sync_state)}`) : ""},
			...(express.express_sync_note ? pending_refunds(express) : []),
			{fieldtype: "Column Break"},
			{fieldname: "express_synced_at", label: __("Express Synced At"), fieldtype: "Datetime", read_only: 1,
				default: express.express_synced_at || ""},
			...(express.express_sync_note ? [{fieldname: "express_sync_note", label: __("Reason"), fieldtype: "Small Text",
				read_only: 1, default: SYNC_NOTES[express.express_sync_note]
					? __(SYNC_NOTES[express.express_sync_note]) : express.express_sync_note}] : pending_refunds(express)),
			// Express 自己对不上的存货（全仓结存 ≠ 各仓合计）：同步不按它抹 ERP 的数，这里只列不拦（出入库第一期）。
			// 只读，用官方 DataTable（`p.table`，见 `show_stock_differences`），不用表格字段（会带勾选框和铅笔）
			...((express.stock_differences || []).length ? [
				{fieldtype: "Section Break", label: t("company.express.stockDifferences")},
				{fieldtype: "HTML", fieldname: "express_stock_differences"},
			] : []),
		];
		const show_stock_differences = express => {
			const host = form?.fields_dict?.express_stock_differences?.$wrapper;
			if (!host || !(express.stock_differences || []).length) return;
			p.table(host, [__("Item Code"), __("Item Name"), t("company.express.locationsTotal"), __("Balance")],
				express.stock_differences.map(row => [row.code, row.name, p.number(row.locations), p.number(row.balance)]));
		};
		// 后台同步做完推过来：提示结果，还在这一屏就重画（显示新的时间、状态、原因）
		const watching = {};  // 每家公司只挂一个监听（连点两次不弹两遍）
		const watch_sync = (company, still_current) => {
			if (watching[company]) frappe.realtime.off("pearnly_erp_express_sync", watching[company]);
			const done = data => {
				if (data?.company !== company) return;
				frappe.realtime.off("pearnly_erp_express_sync", done);
				delete watching[company];
				const key = sync_key(data.state);
				if (key === "ready") p.success(t("company.syncExpress.ready"));
				else frappe.show_alert({message: p.escape(t(`company.syncExpress.${key}`)), indicator: "orange"});
				if (still_current?.()) show();
			};
			watching[company] = done;
			frappe.realtime.on("pearnly_erp_express_sync", done);
		};

		// ── 公司信息（点列表任意一行进来）────────────────────────────────────────
		async function profile(name, still_current) {
			p.clear_page(page);
			list = null;
			const data = await p.call(api + "api_get_profile", name ? {name} : {});
			page.set_title(data.company_name || name);
			// 面包屑：公司 / <这家公司>（点公司那一级回到列表）。
			p.breadcrumbs({label: __("nav.company"), route: p.subroute(root),
				trail: [{label: data.company_name || name || "", route: ""}]});
			const ro = {read_only: !data.can_write};
			// 接了 Express 的公司：名称、地址、电话以 Express 账套为准（同步时覆盖，打印抬头取自这里；`express.locks.COMPANY_LOCKED`）
			const locked = {read_only: !data.can_write || Boolean(data.express_locked)};
			// 建公司时配的那套（收银台）在这里能改：判据是服务端的 `company.manage`
			// （见 `api_get_profile` 的 `can_manage_pos`）。
			const can_edit_pos = Boolean(data.can_manage_pos) && Boolean(data.can_write);
			form = p.layout(page.main, [
				{fieldtype: "Section Break", label: t("company.section.basic")},
				f("company_name", "company.field.name", "Data",
					{reqd: 1, default: data.company_name || "", ...locked}),
				f("short_name", "company.field.shortName", "Data",
					{default: data.short_name || "", ...ro}),
				f("tax_id", "company.field.taxId", "Data", {default: data.tax_id || "", ...ro}),
				// 接了 Express 的公司名称、地址以 Express 为准：不给按税号查税务局回填
				...(data.can_lookup && !data.express_locked ? [f("lookup", "company.lookup.action", "Button",
					{click: () => lookup_tax(form)})] : []),
				f("branch", "company.field.branch", "Data", {default: data.branch || "", ...ro}),
				f("vat_registered", "company.field.vatRegistered", "Select",
					{options: VAT_OPTIONS, default: data.vat_registered || "", ...ro}),
				{fieldtype: "Column Break"},
				f("phone", "company.field.phone", "Data", {default: data.phone || "", ...locked}),
				f("contact_person", "company.field.contactPerson", "Data",
					{default: data.contact_person || "", ...ro}),
				f("contact_email", "company.field.contactEmail", "Data",
					{options: "Email", default: data.contact_email || "", ...ro}),
				f("promptpay_id", "company.field.promptpay", "Data",
					{default: data.promptpay_id || "", ...ro}),
				f("company_type", "company.field.companyType", "Select",
					{options: TYPE_OPTIONS, default: data.company_type || "general", ...ro}),
				// 单据打印抬头的 Logo（没传就用 App 自带的 PEARNLY，`printing.header`）
				{fieldname: "logo", label: __("Logo"), fieldtype: "Attach Image", default: data.logo || "", ...ro},
				{fieldtype: "Section Break", label: t("company.section.address")},
				f("address", "company.field.address", "Small Text",
					{default: data.address || "", ...locked}),
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
				// 前面必须另起一段：付款方式那一排是 4 列，不分段表格会被塞进最后一列（右下角跑偏）。
				{fieldtype: "Section Break", label: __("Terminal")},
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
				// 接 Express（用户 10-29）：勾选；勾了未连上只给连接引导；连上了有同步状态与「断开」
				...express_section(data, name),
				// 接了 Express：最近一次同步（后台落库，见 `express.masters._record`）的账套、时间、状态与失败原因，只读
				...(data.express ? express_fields(data.express, name || data.name) : []),
				...mrerp_section(data, name),
			]);
			if (data.express) show_stock_differences(data.express);
			// MR.ERP 主档还在读 / 落库：做完服务端推给建公司 / 选账套的人，这一屏重画
			if (["queued", "reading", "syncing"].includes(data.mrerp?.sync_state)) watch_sync(name || data.name, still_current);
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
					await p.refresh_company_context?.();   // 改了公司名：左上角跟着换
					page.set_title(values.company_name || name);
					p.breadcrumbs({label: __("nav.company"), route: p.subroute(root),
						trail: [{label: values.company_name || name || "", route: ""}]});
				});
			} else {
				page.clear_primary_action();
			}
			// 接了事务所 Express 账本：主档与库存按 Express 同步一次。请求只发起读取、立刻回；读好与落库在后台，
			// 做完服务端推给本人（`pearnly_erp_express_sync`），这一屏重画显示结果（10-23：在请求里落库大账套超时被杀）。
			if (data.can_sync_express) {
				const company = name || data.name;
				page.add_inner_button(t("company.syncExpress"), async () => {
					const result = await p.call("pearnly_erp_portal.express.api.sync_company", {company}, "POST");
					const key = sync_key(result?.state);
					frappe.show_alert({message: p.escape(t(`company.syncExpress.${key}`)),
						indicator: key === "reading" ? "blue" : "orange"});
					// 状态栏当场换成这次的结果（不等做完 / 刷新才变；10-24 验收）
					if (form?.fields_dict?.express_sync_state) form.set_value("express_sync_state", t(`company.syncExpress.${key}`));
					if (key === "reading") watch_sync(company, still_current);
				});
			}
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
			await profile(segment, still_current);
			if (!still_current()) return;
		}});

		await show();
		return () => list?.destroy();
	};
})();
