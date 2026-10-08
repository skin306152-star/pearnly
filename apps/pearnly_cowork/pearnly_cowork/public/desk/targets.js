/* 目标 ERP（MR.ERP / Express）两条通道的现状与动作，给录入那一步用。
 *
 * 状态一律取自服务端合同，前端不推断"连没连上"：
 *   · 连接：`pearnly_cowork.connections.api.records`（给出 adapter / enabled / 凭据有无）；
 *   · 助手：`pearnly_cowork.companion.api.online`（在不在线，录入角色可用）/ `status`（配对管理）；
 *   · 安装包：`pearnly_cowork.companion.api.installer`（程序本体不在本仓，地址是部署配置）。
 *
 * **界面上不出现 Cowork 自己的公司资料**（用户 2026-09-25：Cowork 不记账、只留记录）：
 * 连接落在哪一份归属上由服务端解析，这里既不显示也不传。要选的「公司 / 年度」是
 * **目标 ERP 那边的**账套（用户 2026-09-26：以第三方为准），见下方 `catalog`。
 *
 * 两条通道各带一颗属于自己的按钮：MR.ERP 云端连接（填账号密码），Express 在商家
 * 自己的电脑上跑助手，所以先装上并绑定助手。
 * 文案尽量用产品已有的术语：状态词就是连接域那三个（`connect.state.*`）。
 */
(() => {
	const p = frappe.pearnly;
	const API = {
		connections: "pearnly_cowork.connections.api.",
		companion: "pearnly_cowork.companion.api.",
	};
	// 目标的定义只在这一处：卡片、状态、按钮都从它派生。
	// 「仅识别」（用户 2026-09-26）：识别后导出复核工作簿，不走任何推送，所以不需要连接。
	const TARGETS = [
		{adapter: "mrerp", label: "MR.ERP", action: "connect"},
		{adapter: "express", label: "Express", action: "connect"},
		{adapter: "none", get label() {return __("intake.target.recognizeOnly");}, action: null},
	];
	const RECOGNIZE_ONLY = "none";
	const key = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

	/** 两条通道的现状：连接记录，外加 Express 的助手绑定状态。 */
	async function state() {
		const rows = (await p.call(API.connections + "records", {limit: 100})) || [];
		const connections = {};
		for (const target of TARGETS) {
			connections[target.adapter] = rows.find(row => row.adapter === target.adapter) || null;
		}
		// 助手在不在线走不要求管理权限的 `online`：只有录入角色的员工也要能选 Express（`status` 是管理用）。
		const express = connections.express;
		const agent = express ? await p.call(API.companion + "online", {connection: express.name}) : null;
		return {connections, agent};
	}

	/** 一条通道现在该怎么描述：文案 + 颜色档。判据全部来自服务端字段。 */
	function describe(adapter, snapshot) {
		if (adapter === RECOGNIZE_ONLY) return null;
		const connection = snapshot.connections[adapter];
		if (!connection) return {text: __("connect.state.notConnected"), tone: "gray"};
		if (!connection.enabled) return {text: __("connect.state.disabled"), tone: "red"};
		// Express 以助手**实际连上**为准（服务端 `online`：当前密钥 3 分钟内报过到），只生成密钥不算。
		if (adapter === "mrerp" || snapshot.agent?.online) return {text: __("connect.state.connected"), tone: "green"};
		return {text: __("intake.agent.state.missing"), tone: "orange"};
	}

	/** 能不能进上传识别：MR.ERP 要有可用连接，Express 要助手在线。 */
	function ready(adapter, snapshot) {
		if (adapter === RECOGNIZE_ONLY) return true;
		const connection = snapshot.connections[adapter];
		if (!connection || !connection.enabled) return false;
		if (adapter === "mrerp") return true;
		return Boolean(snapshot.agent?.online);
	}

	/** 助手安装包：没配地址就如实说，不猜一个我们并不拥有的下载链接。 */
	async function download_agent() {
		const info = await p.call(API.companion + "installer");
		if (!info?.configured) {
			frappe.msgprint({title: __("intake.target.download"), indicator: "orange",
				message: __("intake.installerMissing")});
			return;
		}
		window.open(info.url, "_blank", "noopener,noreferrer");
	}

	/** MR.ERP 的连接：账号密码交给连接域落库，前端不留副本。 */
	function connect_dialog({connection, on_done}) {
		const config = connection?.config || {};
		// 幂等键在弹窗打开时定下来：重试同一份输入不会建出第二条连接。
		const request_key = key();
		return p.dialog(`${__("Connect")} MR.ERP`, [
			{fieldtype: "Data", fieldname: "system_url", label: __("System URL"),
				default: config.system_url || "https://www.mrerp4sme.com", reqd: 1},
			{fieldtype: "Data", fieldname: "username", label: __("Account"), reqd: 1},
			{fieldtype: "Password", fieldname: "password", label: __("Password"), reqd: 1},
		], __("Connect"), async (values, d) => {
			await p.call(API.connections + "configure", {
				adapter: "mrerp", request_key, label: "MR.ERP",
				config: {system_url: values.system_url},
				credentials: {username: values.username, password: values.password},
				// 已经有连接时按它的版本改；没有就建一条新的。
				...(connection ? {name: connection.name, expected_revision: connection.revision} : {}),
			}, "POST");
			d.hide();
			p.success(__("Saved"), "click");
			await on_done();
		});
	}

	/**
	 * Express 的连接：打开就是「下载助手」和配对码两样。
	 * 还没有 Express 通道就先建一条；还没有密钥就当场生成（同一通道已有有效密钥时服务端直接回它，
	 * 不会重复生成）。明文只在生成那一次拿得到，之后只剩末 4 位；丢了就「重新生成」，旧的立即失效。
	 * 配对码 = 密钥 + `@` + 本站域名（2026-09-27）：小助手从配对码知道该连哪家租户，不再写死域名。
	 */
	async function express_dialog({connection, on_done}) {
		if (!connection) {
			connection = await p.call(API.connections + "configure",
				{adapter: "express", request_key: key(), label: "Express", config: {}}, "POST");
		}
		let status = await p.call(API.companion + "status", {connection: connection.name});
		let token = "";
		const issue = async (reset = false) => {
			const result = await p.call(API.companion + "issue", {connection: connection.name, request_key: key(),
				...(reset ? {reset: 1, expected_generation: status.generation} : {})}, "POST");
			token = result.token || "";
			status = result;
		};
		if (status.state !== "active") await issue();

		const d = new frappe.ui.Dialog({
			title: `${__("Connect")} Express`,
			fields: [
				{fieldtype: "Button", fieldname: "download", label: __("intake.target.download"),
					click: () => void download_agent()},
				{fieldtype: "Data", fieldname: "key", label: __("intake.agent.key"), read_only: 1},
				{fieldtype: "Button", fieldname: "copy", label: __("intake.agent.copy"),
					click: () => frappe.utils.copy_to_clipboard(`${token}@${window.location.host}`)},
			],
			primary_action_label: __("intake.agent.reset"),
			primary_action: () => frappe.confirm(__("intake.agent.resetConfirm"), async () => {
				await issue(true);
				paint();
			}),
		});
		const paint = () => {
			d.set_value("key", `${token || `exp_…${status.tail || ""}`}@${window.location.host}`);
			// 完整密钥只在刚生成时有（服务端只存指纹），这时才有「复制」。
			d.set_df_property("copy", "hidden", token ? 0 : 1);
		};
		d.$wrapper.addClass("pearnly-dialog");
		d.onhide = () => void on_done?.();
		d.show();
		paint();
		return d;
	}

	/* ── 公司 / 年度：第三方 ERP 自己的账套目录 ──────────────────────────────
	 *
	 * 目录只认服务端合同（`connections.api.targets`）：MR.ERP 由服务端登录去读选公司页
	 * （`connections/mrerp_collector.py`），Express 由已连上的助手上报。两级怎么分由服务端给
	 * （`groups`，与 LINE 同一份，见 `targets/catalog_groups.py`）：
	 *   · MR.ERP：公司 → 年度（年度就是 MR.ERP 的账套名）；
	 *   · Express：年度 → 账套，照 Express 的真实用法（用户 2026-09-27）：年度是数据文件夹
	 *     （打开哪一年的 Express），账套是那个文件夹登录列表里的一行，名字与顺序原样。
	 * **打开弹窗用服务端存着的那份，不现扫**（用户 2026-09-27）：助手发现登记表有变化会自己上报；
	 * 要全盘重扫（例如新建了年度文件夹）就点「重新读取」。服务端还没有目录时才自动读一次。
	 * 选中的值是账套键（`comidyear:seldb` / Express 路径）；推送时服务端按当前目录核对，
	 * 写入前助手再对照 Express 登记表核一次。
	 */
	// 旧版轮询节奏：前 10 秒每 0.8 秒一次，之后每 2.5 秒；助手首次全盘扫描较慢，放足余量再停。
	const POLL = {fast_ms: 800, fast_for_ms: 10000, slow_ms: 2500, give_up_ms: 330000};
	const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

	/** 连接上配置的默认账套（MR.ERP 的年/库、Express 的账套路径），用来预选。 */
	function default_key(connection) {
		const config = connection?.config || {};
		if (connection?.adapter === "mrerp") {
			return config.comidyear && config.seldb ? `${config.comidyear}:${config.seldb}` : "";
		}
		return config.account_set || config.account_dir || "";
	}

	function catalog_view(connection, data, extra = {}) {
		return {groups: data.groups || [], status: data.status, error_code: data.error_code || null,
			default_key: default_key(connection), collector_available: Boolean(data.collector_available), ...extra};
	}

	/**
	 * 给出服务端存着的目录；`reload` 为真（「重新读取」）或服务端还没有目录时，才让助手 / 服务端
	 * 重新读一次，读完换成新的（期间旧的照常能选）。`alive()` 为假时停止轮询。
	 */
	async function catalog(connection, {on_update, alive, reload = false}) {
		const read = () => p.call(API.connections + "targets", {name: connection.name});
		let data = await read();
		if (!alive()) return;
		const empty = !(data.groups || []).length && data.status === "missing";
		if (!data.collector_available || !(reload || empty)) {
			on_update(catalog_view(connection, data, {loading: false}));
			return;
		}
		on_update(catalog_view(connection, data, {loading: true}));
		const request = await p.call(API.connections + "request_refresh",
			{name: connection.name, request_key: key()}, "POST");
		const started = Date.now();
		let status = request;
		while (alive()) {
			if (status.status === "succeeded") {
				data = await read();
				// 助手的自动上报可能已经在这次结果之后又更新了一版，同样算数。
				if (Number(data.revision) >= Number(status.result_revision)) {
					if (alive()) on_update(catalog_view(connection, data, {loading: false}));
					return;
				}
			} else if (status.status === "failed" || status.status === "superseded") {
				if (alive()) on_update(catalog_view(connection, data, {loading: false, error_code: status.error_code
					|| "ERR_UNEXPECTED"}));
				return;
			}
			const elapsed = Date.now() - started;
			if (elapsed > POLL.give_up_ms) {
				on_update(catalog_view(connection, data, {loading: false, error_code: "ERR_TIMEOUT"}));
				return;
			}
			await wait(elapsed < POLL.fast_for_ms ? POLL.fast_ms : POLL.slow_ms);
			if (!alive()) return;
			status = await p.call(API.connections + "refresh_status",
				{name: connection.name, request_id: request.request_id});
		}
	}

	/**
	 * 选定 Express 账套就让助手去读这份账套的主档（商品、存货科目组、记账指纹、科目表），识别期间读完，
	 * 推送前的库存检查才有这份账套的资料（用户 2026-09-27）。读不到不拦推送，失败不打扰用户。
	 */
	function prefetch_account(connection, account_set_key) {
		if (connection?.adapter !== "express" || !account_set_key) return;
		void p.call(API.connections + "request_refresh",
			{name: connection.name, request_key: key(), account_set_key}, "POST").catch(() => null);
	}

	/* ── 存货科目组：每个账套选一次（用户 2026-09-28）──
	 * 以后在这份账套新建的库存品都挂所选的组。候选是小助手为这份账套上报、判过确是存货科目的组；
	 * 只有一个就不问（直接用它），一个都没有由推送拦下（要会计先在 Express 建组）。 */

	/** 这份 Express 账套的存货科目组：`{known, groups, chosen}`。读不到当作未知（不问、不拦）。 */
	async function stock_group(connection, account_set_key) {
		if (connection?.adapter !== "express" || !account_set_key) return null;
		return p.call(API.connections + "stock_acc_group", {name: connection.name, account_set_key}).catch(() => null);
	}

	/** 该请用户选：候选已知、合格的有两个以上、这份账套还没选过。 */
	function stock_group_needed(view) {
		return Boolean(view?.known && !view.chosen && (view.groups || []).length >= 2);
	}

	/** 下拉选项：「码 · 科目号 · 科目名 · N 个商品在用」（用量帮会计认出这家真在用的组）。 */
	function stock_group_options(view) {
		return (view?.groups || []).map(group => ({value: group.acccod, label: [group.label,
			typeof group.used_by === "number" ? __("used by {0} items", [group.used_by]) : ""].filter(Boolean).join(" · ")}));
	}

	function choose_stock_group(connection, account_set_key, acccod) {
		return p.call(API.connections + "choose_stock_acc_group", {name: connection.name, account_set_key, acccod}, "POST");
	}

	/** 请用户选一次（原生弹窗 + 下拉）。选好存下返回 true；关掉弹窗返回 false。 */
	function ask_stock_group(connection, account_set_key, view) {
		return new Promise(resolve => {
			const d = p.dialog(__("Inventory account group"), [{fieldtype: "Select", fieldname: "acccod",
				label: __("Inventory account group"), options: stock_group_options(view), reqd: 1}], __("Save"),
			async values => {
				await choose_stock_group(connection, account_set_key, values.acccod);
				resolve(true);
				d.hide();
			});
			d.onhide = () => resolve(false);
		});
	}

	/* ── MR.ERP 导入菜单：每个账套每类单据选一次（用户 2026-09-28）──
	 * 菜单号是各公司自己的；只有当前年度已关联日记账的菜单能用（服务端从 MR.ERP 导入页读出来，见
	 * `targets/mrerp_menus.py`）。某类只有一个就不问（直接用它）；多个且没选过、或选过的已失效时请人选一次，
	 * 按连接 + 账套 + 单据类型记住。一个都没有由推送拦下（要先在 MR.ERP 关联日记账）。 */
	const MENU_KINDS = {purchase: ["purchase", "purchase_expense"], sales: ["sales_credit", "sales_cash"]};
	const MENU_READ = {every_ms: 1500, give_up_ms: 90000};

	/** 这份 MR.ERP 账套的菜单视图；`fresh` 为真或还没读过时，先让服务端去 MR.ERP 读一次再给。读不到返回 null。 */
	async function mrerp_menu(connection, account_set_key, {fresh = false} = {}) {
		if (connection?.adapter !== "mrerp" || !account_set_key) return null;
		const args = {name: connection.name, account_set_key};
		const read = () => p.call(API.connections + "mrerp_menu", args).catch(() => null);
		let view = await read();
		// 菜单与主档都读到过才直接用（09-28 起读过菜单的账套还没读过主档，第一次要补读）
		if (!view || (view.known && view.defaults?.known && !fresh)) return view;
		const request = await p.call(API.connections + "request_mrerp_menu", args, "POST").catch(() => null);
		if (!request) return view;
		const started = Date.now();
		while (Date.now() - started < MENU_READ.give_up_ms) {
			await wait(MENU_READ.every_ms);
			view = await read();
			if (!view) return null;
			if ((view.observed_at || "") >= request.requested_at || (view.error_at || "") >= request.requested_at) return view;
		}
		return view;
	}

	/** 这张票会用到、要请人选的单据类型（方向没定就四类都看）。 */
	function mrerp_menu_needed(view, direction) {
		const kinds = MENU_KINDS[direction] || [...MENU_KINDS.purchase, ...MENU_KINDS.sales];
		return kinds.filter(kind => view?.kinds?.[kind]?.needed);
	}

	/* 主档默认值（部门 / 员工 / 科目等，用户 2026-10-08）：这家公司 MR.ERP 主档里某项只有一个就不问，多个时请人选一次，
	 * 按连接 + 账套记住（服务端 `targets/mrerp_masters.py`）。与菜单同一个弹窗一次问齐。 */
	const DEFAULT_FIELDS = {
		sales: ["branch", "dept", "job", "store", "employee", "area", "delivery", "customer_type", "ar_account",
			"bank_account", "product_group", "unit"],
		purchase: ["branch", "dept", "job", "store", "employee", "supplier_type", "ap_account", "product_group",
			"unit", "expense_account"],
	};

	function mrerp_default_needed(view, direction) {
		const fields = DEFAULT_FIELDS[direction] || [...new Set([...DEFAULT_FIELDS.sales, ...DEFAULT_FIELDS.purchase])];
		return fields.filter(field => view?.defaults?.fields?.[field]?.needed);
	}

	/**
	 * 推 MR.ERP 前，这份账套这张票会用到的菜单得定下来。要选时先重读一次导入页（选项可能刚变），
	 * 再用原生弹窗一次选齐；选好存下返回 true，关掉弹窗返回 false（这份账套的票这次先不推）。
	 * 读不到菜单不在这里拦（推送时服务端会核对并说明原因）。
	 */
	async function mrerp_menu_ready(connection, account_set_key, direction) {
		const any_needed = view => mrerp_menu_needed(view, direction).length || mrerp_default_needed(view, direction).length;
		let view = await mrerp_menu(connection, account_set_key);
		if (!any_needed(view)) return true;
		view = await mrerp_menu(connection, account_set_key, {fresh: true});
		const needed = mrerp_menu_needed(view, direction);
		const defaults = mrerp_default_needed(view, direction);
		if (!needed.length && !defaults.length) return true;
		const option_label = option => [option.code, option.name].filter(Boolean).join(" · ");
		return new Promise(resolve => {
			const d = p.dialog(__(defaults.length ? "intake.mrerpDefault.title" : "intake.mrerpMenu.title"), [
				...needed.map(kind => ({fieldtype: "Select", fieldname: kind,
					label: __(`intake.mrerpMenu.${kind}`), reqd: 1,
					options: view.kinds[kind].options.map(option => ({value: option.idmenu, label: option.label || option.idmenu})),
				})),
				...defaults.map(field => ({fieldtype: "Select", fieldname: `default_${field}`,
					label: __(`intake.mrerpDefault.${field}`), reqd: 1,
					options: view.defaults.fields[field].options.map(option => ({value: option.code, label: option_label(option)})),
				})),
			], __("Save"), async values => {
				for (const kind of needed) {
					await p.call(API.connections + "choose_mrerp_menu",
						{name: connection.name, account_set_key, doc_type: kind, idmenu: values[kind]}, "POST");
				}
				for (const field of defaults) {
					await p.call(API.connections + "choose_mrerp_default",
						{name: connection.name, account_set_key, field, code: values[`default_${field}`]}, "POST");
				}
				resolve(true);
				d.hide();
			});
			d.onhide = () => resolve(false);
		});
	}

	/* ── Express 账套里系统猜不准的决定：选一次、记住后同类自动套用（用户 2026-09-28）──
	 * 对方档（同税号几个档 / 分店号对不上）、存货（票面叫法对不上 Express 存货名）、服务类费用科目、
	 * 现购 / 赊购、小票税额抵不抵进项、部门。判定在服务端（`push/express/choices.py`），这里只把
	 * 要问的一次问齐。选好存下返回 true；关掉弹窗返回 false（这张票这次先不推）。 */
	const PUSH_API = "pearnly_cowork.push.application.api.";
	const NEW_CHOICE = "__new__";
	const CHOICE_VALUES = {payment: ["cash", "credit"], vat: ["claim", "expense"]};

	function choice_field(item, index, view) {
		const label = [__(`cowork.choice.${item.kind}`), item.label].filter(Boolean).join(" · ");
		const base = {fieldname: `c${index}`, label, reqd: 1, default: item.current || undefined};
		if (item.kind === "party") {
			return {...base, fieldtype: "Select", options: [...(item.options || []).map(row => ({value: row.code,
				label: [row.code, row.name, row.branch && row.branch !== "00000" ? row.branch : ""].filter(Boolean).join(" · ")})),
				{value: NEW_CHOICE, label: __("cowork.choice.new")}]};
		}
		if (item.kind === "department") {
			return {...base, fieldtype: "Select", options: (item.options || []).map(row => ({value: row.code,
				label: [row.code, row.name].filter(Boolean).join(" · ")}))};
		}
		if (CHOICE_VALUES[item.kind]) {
			return {...base, fieldtype: "Select", options: CHOICE_VALUES[item.kind].map(value => ({value,
				label: __(`cowork.choice.${value}`)}))};
		}
		const rows = item.kind === "item" ? view.options?.products : view.options?.accounts;
		const options = (rows || []).map(row => ({value: row.code, label: [row.code, row.name].filter(Boolean).join(" · ")}));
		if (item.kind === "item") options.push({value: NEW_CHOICE, label: __("cowork.choice.new")});
		// 主档没报上来（科目表空）时只能手填科目码，服务端照样核。
		return {...base, fieldtype: options.length ? "Autocomplete" : "Data", options};
	}

	async function express_choices_ready(connection, account_set_key, history, posting_kind) {
		const args = {history, connection: connection.name, account_set_key, posting_kind: posting_kind || undefined};
		const view = await p.call(PUSH_API + "choices", args);
		const needed = view?.needed || [];
		if (!needed.length) return true;
		return new Promise(resolve => {
			const d = p.dialog(__("cowork.choice.title"), needed.map((item, index) => choice_field(item, index, view)),
				__("Save"), async values => {
					const items = needed.map((item, index) => ({kind: item.kind, subject: item.subject,
						value: values[`c${index}`], label: item.label || ""}));
					await p.call(PUSH_API + "save_choices", {history, connection: connection.name, account_set_key,
						items: JSON.stringify(items)}, "POST");
					resolve(true);
					d.hide();
				});
			d.onhide = () => resolve(false);
		});
	}

	/** 目录失败的原因：错误码有对应文案就用，没有就给通用那一句（不把原始码摆给商家）。 */
	function catalog_error(code) {
		const text = __(`intake.catalog.${code}`);
		return text === `intake.catalog.${code}` ? __("intake.catalog.ERR_UNEXPECTED") : text;
	}

	p.intake_targets = {TARGETS, RECOGNIZE_ONLY, catalog, catalog_error, connect_dialog, describe, download_agent,
		prefetch_account,
		express_dialog,
		stock_group, stock_group_needed, stock_group_options, choose_stock_group, ask_stock_group,
		mrerp_menu, mrerp_menu_needed, mrerp_default_needed, mrerp_menu_ready,
		express_choices_ready,
		ready, state};
})();
