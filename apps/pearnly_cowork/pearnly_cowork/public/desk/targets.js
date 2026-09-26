/* 目标 ERP（MR.ERP / Express）两条通道的现状与动作，给录入那一步用。
 *
 * 状态一律取自服务端合同，前端不推断"连没连上"：
 *   · 连接：`pearnly_cowork.connections.api.records`（给出 adapter / enabled / 凭据有无）；
 *   · 助手：`pearnly_cowork.companion.api.status`（只有 Express 通道有"绑定"这回事）；
 *   · 安装包：`pearnly_cowork.companion.api.installer`（程序本体不在本仓，地址是部署配置）。
 *
 * **界面上不出现 Cowork 自己的公司资料**（用户 2026-09-25：Cowork 不记账、只留记录）：
 * 连接落在哪一份归属上由服务端解析，这里既不显示也不传。要选的「公司 / 年度」是
 * **目标 ERP 那边的**账套（用户 2026-09-26：以第三方为准、实时选项），见下方 `catalog`。
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
		// 没有 Express 通道时不去问绑定状态：那种问法只会换来一个权限错。
		const express = connections.express;
		const agent = express ? await p.call(API.companion + "status", {connection: express.name}) : null;
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
			p.success(__("Saved"));
			await on_done();
		});
	}

	/**
	 * Express 的连接：打开就是「下载助手」和密钥两样。
	 * 还没有 Express 通道就先建一条；还没有密钥就当场生成（同一通道已有有效密钥时服务端直接回它，
	 * 不会重复生成）。明文只在生成那一次拿得到，之后只剩末 4 位；丢了就「重新生成」，旧的立即失效。
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
					click: () => frappe.utils.copy_to_clipboard(token)},
			],
			primary_action_label: __("intake.agent.reset"),
			primary_action: () => frappe.confirm(__("intake.agent.resetConfirm"), async () => {
				await issue(true);
				paint();
			}),
		});
		const paint = () => {
			d.set_value("key", token || `exp_…${status.tail || ""}`);
			// 完整密钥只在刚生成时有（服务端只存指纹），这时才有「复制」。
			d.set_df_property("copy", "hidden", token ? 0 : 1);
		};
		d.$wrapper.addClass("pearnly-dialog");
		d.onhide = () => void on_done?.();
		d.show();
		paint();
		return d;
	}

	/* ── 公司 / 年度：第三方 ERP 自己的账套目录（实时） ─────────────────────────
	 *
	 * 目录只认服务端合同（`connections.api.targets`）：MR.ERP 由服务端登录去读选公司页
	 * （`connections/mrerp_collector.py`），Express 由已连上的助手上报。两家的账套都是
	 * 「一家公司的一个年度」，只是嵌套顺序相反；界面统一成「公司 → 年度」（用户 2026-09-26）：
	 *   · MR.ERP：一行公司（简称 · 全称）下挂几个年度账套，年度就是 MR.ERP 的账套名；
	 *   · Express：年度目录下每家公司一个账套文件夹，按公司名归组，年度取目录名（旧版界面是
	 *     先年度后账套，照 Express 目录结构）；助手报告不可写的账套不给选（旧版同）。
	 * 选中的值是账套键（`comidyear:seldb` / Express 路径），推送时连同目录证据一起交给服务端。
	 */
	// 旧版轮询节奏：前 10 秒每 0.8 秒一次，之后每 2.5 秒；MR.ERP 一次租约 180 秒，放足余量再停。
	const POLL = {fast_ms: 800, fast_for_ms: 10000, slow_ms: 2500, give_up_ms: 240000};
	const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
	// 年度按标签里最大的那个数字倒序（最近的年度在最上面，旧版 Express 年度下拉同法）。
	const year_rank = label => Math.max(-1, ...(String(label).match(/\d+/g) || []).map(Number));

	/** 目录 → 「公司 → 年度」两级选项 `[{value, label, years: [{value: 账套键, label}]}]`。 */
	function companies(adapter, account_sets) {
		const found = new Map();
		const express = adapter === "express";
		for (const row of account_sets || []) {
			const a = row.attributes || {};
			if (row.active === false || (express && a.writable === false)) continue;
			const [key, label, year] = express
				? [a.company || row.label, a.company || row.label, a.root_label || a.root || row.label]
				: [a.company || a.company_name || row.source_id,
					[a.company, a.company_name].filter(Boolean).join(" · ") || row.label, row.label];
			if (!found.has(key)) found.set(key, {value: key, label, years: []});
			found.get(key).years.push({value: row.source_id, label: year});
		}
		const list = [...found.values()];
		for (const company of list) company.years.sort((a, b) => year_rank(b.label) - year_rank(a.label));
		return list.sort((a, b) => a.label.localeCompare(b.label));
	}

	/** 连接上配置的默认账套（MR.ERP 的年/库、Express 的账套路径），用来预选。 */
	function default_key(connection) {
		const config = connection?.config || {};
		if (connection?.adapter === "mrerp") {
			return config.comidyear && config.seldb ? `${config.comidyear}:${config.seldb}` : "";
		}
		return config.account_set || config.account_dir || "";
	}

	/** 目录快照 → 界面要的形状；`evidence` 是推送时证明「这份目录是最新刷新出来的」那两样。 */
	function catalog_view(connection, data, extra = {}) {
		const fresh = data.status === "fresh" && data.refresh_status === "succeeded";
		return {companies: companies(connection.adapter, data.account_sets), status: data.status,
			error_code: data.error_code || null, default_key: default_key(connection),
			evidence: fresh ? {catalog_request_id: data.refresh_request_id, catalog_revision: data.revision} : null,
			...extra};
	}

	/**
	 * 先把服务端已有的那份目录给出去（打开弹窗就有东西可选），再申请一次实时刷新；
	 * 刷新完成且快照版本与刷新结果一致才算数（旧版同一判据）。`alive()` 为假时停止轮询。
	 */
	async function catalog(connection, {on_update, alive}) {
		const read = () => p.call(API.connections + "targets", {name: connection.name});
		let data = await read();
		if (!alive()) return;
		if (!data.collector_available) {
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
				if (Number(data.revision) === Number(status.result_revision)) {
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

	/** 目录失败的原因：错误码有对应文案就用，没有就给通用那一句（不把原始码摆给商家）。 */
	function catalog_error(code) {
		const text = __(`intake.catalog.${code}`);
		return text === `intake.catalog.${code}` ? __("intake.catalog.ERR_UNEXPECTED") : text;
	}

	p.intake_targets = {TARGETS, RECOGNIZE_ONLY, catalog, catalog_error, connect_dialog, describe, download_agent,
		express_dialog,
		ready, state};
})();
