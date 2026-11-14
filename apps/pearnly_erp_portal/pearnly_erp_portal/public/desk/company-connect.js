/* 从连接添加（公司接入重做第 2 批，2026-11-14，用户定 A 方案）。
 *
 * 接一家公司只剩两步：CC 桌面版授权 Express 位置（或登录 MR.ERP）→ 在这里勾要用的公司。勾谁建谁，名称 / 税号 / 分店由服务端
 * 读目录带出（`masters.cc_service.pending` / `create_many`，与 CC 对话里选公司弹窗同一份目录、同一个建法），建好自动排同步。
 *
 * 两个入口用同一个原生对话框（`frappe.ui.Dialog`，官方 MultiCheck 勾选、Select 分 Express / MR.ERP、Data 搜索，按页向服务端取）：
 *   · 公司页列表「从连接添加」（`desk/company.js`）；
 *   · 授权之后自动弹：服务端算出连接里有还没接进 ERP 的公司（`express.companies.announce`）就推 `pearnly_erp_new_ledgers`，
 *     不在线的下次打开 ERP 时由启动数据 `pearnly_new_ledgers` 带出；只对能建公司的人（`pearnly_can_build`）。
 *     关掉或建好都记「看过这一批」（`api_new_ledgers_seen`），同一批不再弹。
 */
(() => {
	const p = frappe.pearnly;
	const api = "pearnly_erp_portal.masters.api.";
	const PAGE = 50;
	const ADAPTER_LABELS = {express: "Express", mrerp: "MR.ERP"};
	let dialog = null;

	const label_of = row => [row.title, row.tax_id, row.branch && row.branch !== "00000" ? row.branch : "",
		(row.years || []).join(" ")].filter(Boolean).join(" · ");
	const key_of = row => row.identity;

	p.connect_companies = ({auto = false, done = null} = {}) => {
		if (dialog) return dialog.show();
		const chosen = new Map();         // 勾上的：identity → {connection, account_set_key}
		let rows = [], total = 0, adapter = "", search = "";
		dialog = new frappe.ui.Dialog({
			title: __("company.connect.title"),
			size: "large",
			fields: [
				{fieldname: "adapter", fieldtype: "Select", label: __("Source"), options: [], hidden: 1,
					change: () => { adapter = dialog.get_value("adapter") || ""; void load(true); }},
				{fieldname: "search", fieldtype: "Data", label: __("Search"),
					change: () => { search = dialog.get_value("search") || ""; void load(true); }},
				{fieldname: "items", fieldtype: "MultiCheck", columns: 1, options: [],
					on_change: () => remember()},
				{fieldname: "more", fieldtype: "Button", label: __("Load More"), hidden: 1, click: () => void load(false)},
			],
			primary_action_label: __("Add"),
			async primary_action() {
				remember();
				const items = [...chosen.values()];
				if (!items.length) return;
				dialog.disable_primary_action();
				try {
					await p.call(api + "api_add_from_connections", {items: JSON.stringify(items)}, "POST");
				} finally {
					dialog.enable_primary_action();
				}
				frappe.boot.pearnly_new_ledgers = 0;
				dialog.hide();
				p.success(__("company.notice.created"), "click");
				await p.refresh_company_context?.();
				done?.();
			},
		});
		dialog.onhide = () => {
			if (auto) void p.call(api + "api_new_ledgers_seen", {}, "POST").catch(error => globalThis.pearnlyCaught?.(error, "company-connect"));
			frappe.boot.pearnly_new_ledgers = 0;
			dialog = null;
		};
		const field = () => dialog.fields_dict.items;
		function remember() {
			const checked = new Set(field().get_checked_options());
			rows.forEach(row => {
				if (checked.has(key_of(row))) chosen.set(key_of(row), {connection: row.connection, account_set_key: row.account_set_key});
				else chosen.delete(key_of(row));
			});
		}
		async function load(reset) {
			if (!dialog) return;
			remember();
			const start = reset ? 0 : rows.length;
			const page = await p.call(api + "api_connection_choices", {search, adapter, start, limit: PAGE});
			rows = reset ? page.rows || [] : [...rows, ...(page.rows || [])];
			total = page.total || 0;
			const adapters = page.adapters || [];
			const select = dialog.fields_dict.adapter;
			if (adapters.length > 1 && !select.df.options.length) {
				select.df.options = adapters.map(value => ({value, label: ADAPTER_LABELS[value] || value}));
				select.df.hidden = 0;
				select.refresh();
				adapter = adapters[0];
				select.set_input(adapter);
				return load(true);
			}
			field().df.options = rows.map(row => ({value: key_of(row), label: label_of(row), checked: chosen.has(key_of(row))}));
			field().refresh();
			dialog.fields_dict.more.df.hidden = rows.length >= total ? 1 : 0;
			dialog.fields_dict.more.refresh();
		}
		dialog.show();
		void load(true);
	};

	// 客户 / 供应商 / 物料的选择框（按类，全部入口一起；评估 §2.1）：没自带 `query` 的都走 `masters.link_search.search`
	// ——本公司账本档排前、账本编码与条码可搜、公司取当前公司（单据都开在当前公司）；各页给的条件（is_active 等）照带。
	const MASTERS = ["ERP Product", "ERP Customer", "ERP Supplier"];
	const FILTERABLE = ["is_active", "party_type", "track_serial", "express_stock", "category"];
	p.link_query_wrap = args => {
		if (!MASTERS.includes(args.doctype) || args.query) return;
		let filters = args.filters || {};
		if (typeof filters === "string") filters = JSON.parse(filters || "{}");
		// 只接得住「某列 = 某值」的简单条件（同服务端 `link_search.FILTERABLE`）；别的条件照原生搜索，不放宽也不丢
		if (Array.isArray(filters) || Object.entries(filters).some(([key, value]) =>
			!FILTERABLE.includes(key) || (value !== null && typeof value === "object"))) return;
		args.query = "pearnly_erp_portal.masters.link_search.search";
		args.filters = {...filters, company: frappe.boot.pearnly_company || ""};
	};

	// 授权之后自动弹：只给能建公司的人，同一批关过的不再弹（服务端按本人记）。等路由把第一页画好再弹
	// （在 app_ready 里直接开对话框会打断原生建页：工作区侧栏还没挂上，实验栈报过 set_workspace_sidebar）
	const offer = () => {
		if (!frappe.boot.pearnly_can_build || dialog) return;
		const route = frappe.get_route() || [];
		if (route[0] === "pearnly-company" && route[1] === "new") return;
		p.connect_companies({auto: true});
	};
	$(document).on("app_ready", () => {
		let first = Number(frappe.boot.pearnly_new_ledgers) > 0;
		frappe.router.on("change", () => {
			if (!first) return;
			first = false;
			frappe.after_ajax(() => setTimeout(offer, 0));
		});
		frappe.realtime.on("pearnly_erp_new_ledgers", data => {
			if (Number(data?.count) > 0) offer();
		});
	});
})();
