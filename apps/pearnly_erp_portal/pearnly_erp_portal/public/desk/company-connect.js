/* 从连接添加（公司接入重做第 2 批，2026-11-14，用户定 A 方案；11-15 照 CC「选择公司」弹窗的排布重做）。
 *
 * 接一家公司只剩两步：CC 桌面版授权 Express 位置（或登录 MR.ERP）→ 在这里勾要用的公司。勾谁建谁，名称 / 税号 / 分店由服务端
 * 读目录带出（`masters.cc_service.connection_list` / `create_many`，与 CC 对话里选公司弹窗同一份目录、同一个建法），建好自动排同步。
 *
 * 排布同 CC「选择公司」（用户 11-15 截图）：标题，右上刷新 / 关闭；整行搜索；Express / MR.ERP 两个标签（只有一种来源时不出标签）；
 * 每行行首勾选框，公司名，下面浅色一行「税号 · 总部 / 分店 · 账套文件夹」，行尾状态；列表在弹窗里滚动；底部「添加」。
 * 控件都是原生的：`frappe.ui.Dialog`（头部按钮区 `.modal-actions` 照它自己的 `btn-ghost icon-btn` 加刷新）、Data 搜索、原生标签
 * 样式（`form-tabs` / `nav-link`）、原生 MultiCheck 勾选（它的选项文字原样放进 `label-area`，两行字与行尾 `indicator-pill` 就排在
 * 那里，已添加的那行把原生勾选框设成禁用）。连接上报的账套全列：已有公司的禁用并标「已添加」；对应公司已停用的可勾（勾了就是重新启用那家，不另建）；
 * 一行都没有时：没连电脑也没 MR.ERP 连接显示「CC 桌面版未连接」，否则「无数据」。
 *
 * 两个入口用同一个对话框：
 *   · 公司页列表「从连接添加」（`desk/company.js`）（左上角公司名下拉里不放，用户 11-15）；
 *   · 授权之后自动弹：服务端算出连接里有还没接进 ERP 的公司（`express.companies.announce`）就推 `pearnly_erp_new_ledgers`，
 *     不在线的下次打开 ERP 时由启动数据 `pearnly_new_ledgers` 带出；只对能建公司的人（`pearnly_can_build`）。
 *     关掉或建好都记「看过这一批」（`api_new_ledgers_seen`），同一批不再弹。
 */
(() => {
	const p = frappe.pearnly;
	const api = "pearnly_erp_portal.masters.api.";
	const PAGE = 50;
	const ADAPTER_LABELS = {express: "Express", mrerp: "MR.ERP"};
	const HEAD_OFFICE = "00000";
	const RESCAN_WAIT_MS = 60000;   // 刷新后等电脑整份重报的上限（办公室共享盘走 SMB1 / 无线，整份重扫超过十几秒是常态）
	const RESCAN_POLL_MS = 2000;
	let dialog = null;

	const esc = value => frappe.utils.escape_html(String(value ?? ""));
	const meta_of = row => [row.book, row.tax_id,
		row.tax_id ? (!row.branch || row.branch === HEAD_OFFICE ? __("Head Office") : `${__("Branch")} ${row.branch}`) : "",
		// MR.ERP 各年度写的是「账套名 年份」，用逗号隔开才分得清；Express 的年份 / 文件夹照旧空格
		((row.folders || []).length ? row.folders : (row.years || [])).join(row.adapter === "mrerp" ? ", " : " "),
		// MR.ERP 一家公司在两条连接（两台 MR.ERP 服务器）里会出两行：带上连接的站点地址分得清（主会话 11-15）
		row.adapter === "mrerp" ? row.connection_title : ""]
		.filter(Boolean).join(" · ");
	const PILLS = {added: ["gray", () => __("company.connect.added")], inactive: ["orange", () => __("company.status.inactive")]};

	p.connect_companies = ({auto = false, done = null} = {}) => {
		if (dialog) return dialog.show();
		const chosen = new Map();         // 勾上的：identity → {connection, account_set_key}
		let rows = [], total = 0, adapter = "", adapters = [], connected = true, search = "", loading = 0;
		dialog = new frappe.ui.Dialog({
			title: __("company.connect.title"),
			size: "large",
			fields: [
				{fieldname: "search", fieldtype: "Data", placeholder: __("Search"),
					change: () => { search = dialog.get_value("search") || ""; void load(true); }},
				{fieldname: "tabs", fieldtype: "HTML"},
				{fieldname: "items", fieldtype: "MultiCheck", columns: 1, sort_options: false, options: [],
					on_change: () => remember()},
				{fieldname: "empty", fieldtype: "HTML"},
				{fieldname: "more", fieldtype: "Button", label: __("Load More"), hidden: 1, click: () => void load(false)},
			],
			primary_action_label: __("Add"),
			async primary_action() {
				const items = [...chosen.values()];
				if (!items.length) return;
				dialog.disable_primary_action();
				let made = [];
				try {
					made = await p.call(api + "api_add_from_connections", {items: JSON.stringify(items)}, "POST") || [];
				} finally {
					dialog.enable_primary_action();
				}
				frappe.boot.pearnly_new_ledgers = 0;
				dialog.hide();
				// 有新建的（含接管开户时那家空公司）说「已新建」；只是重新启用停用的公司 / 接上已有公司的，用公司页启用时那句「已更新」
				p.success(__(made.some(item => item?.created || item?.adopted) ? "company.notice.created" : "company.notice.toggled"), "click");
				await p.refresh_company_context?.();
				done?.(made.map(item => item?.id).filter(Boolean));
			},
		});
		dialog.onhide = () => {
			if (auto) void p.call(api + "api_new_ledgers_seen", {}, "POST").catch(error => globalThis.pearnlyCaught?.(error, "company-connect"));
			frappe.boot.pearnly_new_ledgers = 0;
			dialog = null;
		};
		// 右上刷新：放进原生头部按钮区、排在关闭前面，样式照关闭那颗
		// 刷新 = 请在线电脑立刻整份重扫并上报账套（`cc_service.rescan`），边等回执边更新列表（至多 RESCAN_WAIT_MS）；
		// 没有在线电脑就直接显示「CC 桌面版未连接」。等待时按钮同 frappe.call 的 btn 一样禁用，连点不重复下发
		const $refresh = $(`<button class="btn btn-ghost icon-btn" type="button" title="${esc(__("Refresh"))}">${frappe.utils.icon("refresh", "sm")}</button>`)
			.prependTo(dialog.$wrapper.find(".modal-actions"))
			.on("click", () => void rescan());
		async function rescan() {
			if ($refresh.prop("disabled")) return;
			$refresh.prop("disabled", true);
			try {
				const sent = await p.call(api + "api_connection_rescan", {}, "POST");
				const devices = sent?.devices || [];
				if (!devices.length) {
					connected = false;
					rows = [];
					total = 0;
					return paint();
				}
				// 边等边出：名单指纹变了就重取列表（扫得慢时电脑先报「进行中」快照）；都回执了、在等的电脑都离线了、或等满上限就停
				const until = Date.now() + RESCAN_WAIT_MS;
				let stamp = null;
				while (dialog && Date.now() < until) {
					await new Promise(resolve => setTimeout(resolve, RESCAN_POLL_MS));
					const state = await p.call(api + "api_connection_rescan_pending", {devices: JSON.stringify(devices)});
					if (state?.stamp !== stamp) {
						stamp = state?.stamp;
						await load(true);
					}
					if (state?.pending && !state?.online) {
						// 在等的电脑都没来领指令（刚退出的 CC 几分钟内还算在线）：当不在线，不干等满上限
						connected = false;
						rows = [];
						total = 0;
						return paint();
					}
					if (!state?.pending) break;
				}
				await load(true);
			} finally {
				$refresh.prop("disabled", false);
			}
		}
		const $tabs = $(`<div class="form-tabs-list"><ul class="nav form-tabs" role="tablist"></ul></div>`)
			.appendTo(dialog.fields_dict.tabs.$wrapper.empty()).hide();
		$tabs.on("click", ".nav-link", event => {
			const value = $(event.currentTarget).attr("data-adapter");
			if (value === adapter) return;
			adapter = value;
			paint_tabs();
			void load(true);
		});
		dialog.$wrapper.addClass("pearnly-connect-dialog");
		const field = () => dialog.fields_dict.items;
		field().$wrapper.addClass("pearnly-connect-list");
		function remember() {
			const checked = new Set(field().get_checked_options());
			rows.forEach(row => {
				if (row.state !== "added" && checked.has(row.identity)) {
					chosen.set(row.identity, {connection: row.connection, account_set_key: row.account_set_key});
				} else {
					chosen.delete(row.identity);
				}
			});
		}
		function paint_tabs() {
			$tabs.toggle(adapters.length > 1);
			$tabs.find(".form-tabs").html(adapters.map(value => `<li class="nav-item">
				<button class="nav-link ${value === adapter ? "active" : ""}" type="button" role="tab"
					data-adapter="${esc(value)}">${esc(ADAPTER_LABELS[value] || value)}</button></li>`).join(""));
		}
		// 选项文字由 MultiCheck 原样放进 `label-area`（不转义），客户数据都先转义
		const label_of = row => {
			const pill = PILLS[row.state];
			return `<span class="pearnly-connect-text"><span class="d-block">${esc(row.title)}</span>`
				+ `<span class="d-block text-muted small">${esc(meta_of(row))}</span></span>`
				+ (pill ? `<span class="indicator-pill ${pill[0]}">${esc(pill[1]())}</span>` : "");
		};
		function paint() {
			field().df.options = rows.map(row => ({value: row.identity, label: label_of(row), checked: chosen.has(row.identity)}));
			field().refresh();
			(field().options || []).forEach(option => {
				if (rows.find(row => row.identity === option.value)?.state === "added") {
					option.$checkbox?.addClass("text-muted").find(":checkbox").prop("disabled", true);
				}
			});
			field().$wrapper.toggle(rows.length > 0);
			dialog.fields_dict.empty.$wrapper.html(rows.length ? ""
				: `<div class="text-muted text-center py-4">${esc(connected ? __("No Data") : __("company.syncExpress.offline"))}</div>`);
			dialog.fields_dict.more.df.hidden = rows.length >= total ? 1 : 0;
			dialog.fields_dict.more.refresh();
		}
		async function load(reset) {
			if (!dialog) return;
			remember();
			const ticket = ++loading;
			const start = reset ? 0 : rows.length;
			const page = await p.call(api + "api_connection_choices", {search, adapter, start, limit: PAGE});
			if (!dialog || ticket !== loading) return;    // 换了标签 / 搜索：旧的那页不画
			rows = reset ? page.rows || [] : [...rows, ...(page.rows || [])];
			total = page.total || 0;
			connected = page.connected !== false;
			adapters = page.adapters || [];
			if (adapters.length && !adapters.includes(adapter)) {
				adapter = adapters[0];
				paint_tabs();
				return load(true);
			}
			paint_tabs();
			paint();
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
