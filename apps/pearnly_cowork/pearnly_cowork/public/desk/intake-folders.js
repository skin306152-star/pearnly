/* 自动录入规则（录入工作台子路由 `pearnly-intake/folders`，2026-10-07）。
 *
 * 版面照录入工作台本身：原生列表（`p.list`）一行一条规则，点一行打开原生弹窗编辑；
 * 行尾一颗「立即运行」，停用 / 启用与运行记录放在弹窗左下角官方的自定义动作位。
 * 规则 = 新建录入那一组前置条件 + 文件夹 + 每月几号几点 + 遇到时的默认处理。
 * 本功能的词条在 Cowork 自己的翻译包里，一律经原生 `__()` 取（录入工作台按前缀取的文案包里没有 `cowork.`）。
 */
(() => {
	const p = frappe.pearnly;
	const API = "pearnly_cowork.folder_intake.api.";
	const CONNECTIONS = "pearnly_cowork.connections.api.";
	const ft = (key, ...args) => __(key, args);
	// 规则弹窗里的读取（目录、选项）只是为了铺下拉：读不到就下次再铺，不弹错误框打断填写。
	const quiet = async (method, args) => (await frappe.call({method, args, type: "GET", silent: true})).message;
	const days = () => [...Array.from({length: 28}, (_, i) => ({value: String(i + 1), label: ft("cowork.folder.day", i + 1)})),
		{value: "0", label: ft("cowork.folder.lastDay")}];
	const times = () => Array.from({length: 48}, (_, i) => {
		const value = `${String(Math.floor(i / 2)).padStart(2, "0")}:${i % 2 ? "30" : "00"}`;
		return {value, label: value};
	});
	const day_label = day => days().find(row => row.value === String(day))?.label || "";

	p.intake_folders = async ({page, t, still_current}) => {
		const root = "pearnly-intake";
		page.set_title(ft("cowork.folder.title"));
		p.breadcrumbs({label: t("intake.title"), route: p.subroute(root), trail: [{label: ft("cowork.folder.title"), route: ""}]});
		let data = {rules: [], connections: []};
		// 文件夹不可用：小助手报这条规则的文件夹不在了（授权移除 / 改名 / 子文件夹删了），到点也不会跑。
		const status = row => (!row.enabled ? ["gray", ft("cowork.folder.off")]
			: !row.agent_fresh ? ["orange", ft("cowork.folder.agentStale")]
			: ["root_missing", "folder_missing"].includes(row.folder_state) ? ["red", ft("cowork.folder.folderMissing")]
			: ["green", ft("cowork.folder.on")]);
		const edit = rule => open_rule({t, data, rule, on_saved: () => list.reload()});

		const list = p.list({
			page, doctype: "Cowork Folder Intake Rule", compact_columns: true,
			columns: [
				{fieldname: "title", width: 200, min_width: 140, label: ft("cowork.folder.field.title")},
				{fieldname: "account_label", width: 180, min_width: 110, label: __("intake.target.accountSet")},
				{fieldname: "folder", width: 160, min_width: 100, label: ft("cowork.folder.field.folder"),
					format: (value, row) => value || row.root_label || ""},
				{fieldname: "run_day", width: 110, min_width: 90, label: ft("cowork.folder.col.schedule"),
					format: (value, row) => `${day_label(value)} ${row.run_time || ""}`},
				{fieldname: "counts", width: 130, min_width: 100, label: ft("cowork.folder.col.result"),
					format: (value, row) => ft("cowork.folder.counts", row.results?.entered || 0, row.results?.attention || 0)},
				{fieldname: "enabled", width: 90, min_width: 80, label: t("intake.col.status"),
					format: (value, row) => status(row)[1], cell_class: (value, row) => `indicator-pill ${status(row)[0]}`},
			],
			load: async () => {
				data = await p.call(API + "listing");
				return {items: data.rules, total: data.rules.length};
			},
			on_open: row => edit(row),
			row_actions: row => [
				// 有没推出去的票：直接到录入工作台、只看「失败」的那些，在复核页按原因处理（选一次 / 删掉）。
				...(row.results?.attention ? [{id: "attention", label: ft("cowork.folder.viewAttention"), action: () => {
					frappe.route_options = {push_state: "failed"};
					frappe.set_route(root);
				}}] : []),
				...(row.enabled ? [{id: "run", label: ft("cowork.folder.runNow"), action: async () => {
					await p.call(API + "run_now", {name: row.name}, "POST");
					frappe.show_alert({message: p.escape(ft("cowork.folder.runRequested")), indicator: "green"});
					await list.reload();
				}}] : []),
			],
			empty_action: {label: ft("cowork.folder.new"), action: () => edit(null)},
		});
		page.set_primary_action(ft("cowork.folder.new"), () => edit(null));
		await list.ready;
		return () => list.destroy();
	};

	/** 新建 / 编辑一条规则（原生弹窗）。 */
	function open_rule({t, data, rule, on_saved}) {
		const state = {connection: rule?.connection || data.connections[0]?.name || null, groups: [], options: null,
			account_set_key: rule?.account_set_key || null, loading: 0};
		const fields = [
			{fieldtype: "Data", fieldname: "title", label: ft("cowork.folder.field.title"), reqd: 1, default: rule?.title || ""},
			{fieldtype: "Select", fieldname: "connection", label: ft("cowork.folder.field.connection"), reqd: 1,
				hidden: data.connections.length > 1 ? 0 : 1, read_only: rule ? 1 : 0, default: state.connection,
				options: data.connections.map(row => ({value: row.name, label: row.label})), onchange: () => void on_connection()},
			{fieldtype: "Select", fieldname: "year", label: __("intake.target.year"), options: [], onchange: () => void on_year()},
			{fieldtype: "Select", fieldname: "account_set_key", label: __("intake.target.accountSet"), reqd: 1, options: [],
				max_items: 1000, onchange: () => void on_account()},
			{fieldtype: "Column Break"},
			{fieldtype: "Select", fieldname: "direction", label: t("intake.field.direction"), reqd: 1,
				options: [{value: "purchase", label: t("intake.dir.purchase")}, {value: "sales", label: t("intake.dir.sales")}],
				default: rule?.direction || "purchase", onchange: () => paint_defaults()},
			{fieldtype: "Select", fieldname: "posting_kind", label: __("Posting Kind"), reqd: 1,
				options: [{value: "service", label: __("Service")}, {value: "stock", label: __("Stock")}],
				default: rule?.posting_kind || "service", onchange: () => {paint_stock(); paint_defaults();}},
			{fieldtype: "Select", fieldname: "stock_acc_group", label: __("Inventory account group"), options: [], hidden: 1},
			{fieldtype: "Section Break"},
			{fieldtype: "Select", fieldname: "folder", label: ft("cowork.folder.field.folder"), reqd: 1, options: [],
				max_items: 2000, description: ft("cowork.folder.folderHint")},
			{fieldtype: "Column Break"},
			{fieldtype: "Select", fieldname: "run_day", label: ft("cowork.folder.field.day"), reqd: 1, options: days(),
				default: String(rule?.run_day ?? 1)},
			{fieldtype: "Select", fieldname: "run_time", label: ft("cowork.folder.field.time"), reqd: 1, options: times(),
				default: rule?.run_time || "22:00"},
			{fieldtype: "Section Break", label: ft("cowork.folder.section.defaults"), collapsible: 1},
			{fieldtype: "Select", fieldname: "d_payment", label: ft("cowork.folder.default.payment"),
				options: [{value: "credit", label: __("Credit")}, {value: "cash", label: __("Cash")}]},
			{fieldtype: "Select", fieldname: "d_vat", label: ft("cowork.folder.default.vat"),
				options: [{value: "claim", label: ft("cowork.folder.vat.claim")}, {value: "expense", label: ft("cowork.folder.vat.expense")}]},
			{fieldtype: "Select", fieldname: "d_item", label: ft("cowork.folder.default.item"),
				options: [{value: "__new__", label: ft("cowork.folder.item.new")}, {value: "ask", label: ft("cowork.folder.item.ask")}]},
			{fieldtype: "Column Break"},
			{fieldtype: "Select", fieldname: "d_account", label: ft("cowork.folder.default.account"), options: [], max_items: 2000},
			{fieldtype: "Select", fieldname: "d_department", label: ft("cowork.folder.default.department"), options: []},
		];
		const d = p.dialog(rule ? rule.title : ft("cowork.folder.new"), fields, __("Save"), async values => {
			if (needs_stock_group()) {
				if (!values.stock_acc_group) {
					frappe.msgprint(p.escape(ft("cowork.folder.stock_group_needed")));
					return;
				}
				await p.intake_targets.choose_stock_group(connection(), values.account_set_key, values.stock_acc_group);
			}
			const defaults = {};
			for (const kind of ["payment", "vat", "account", "department", "item"]) {
				if (!d.fields_dict["d_" + kind].df.hidden && values["d_" + kind]) defaults[kind] = values["d_" + kind];
			}
			const [root_id, ...folder] = String(values.folder || "").split("|");
			await p.call(API + "save", {values: JSON.stringify({name: rule?.name || null, title: values.title,
				connection: state.connection, account_set_key: values.account_set_key, direction: values.direction,
				posting_kind: values.posting_kind, root_id, folder: folder.join("|"),
				run_day: Number(values.run_day), run_time: values.run_time, defaults})}, "POST");
			d.hide();
			frappe.show_alert({message: p.escape(__("Saved")), indicator: "green"});
			await on_saved();
		});
		d.$wrapper.find(".modal-dialog").addClass("modal-lg");
		if (rule) {
			// 官方的自定义动作位（弹窗左下角，同 QuickEntryForm 的「全部编辑」）：停用 / 启用、运行记录。
			d.add_custom_action(rule.enabled ? ft("cowork.folder.disable") : ft("cowork.folder.enable"), () =>
				frappe.confirm(p.escape(ft(rule.enabled ? "cowork.folder.disableConfirm" : "cowork.folder.enableConfirm")), async () => {
					await p.call(API + "set_enabled", {name: rule.name, enabled: rule.enabled ? 0 : 1}, "POST");
					d.hide();
					await on_saved();
				}));
			d.add_custom_action(ft("cowork.folder.runs"), () => void runs(rule));
		}
		const connection = () => ({name: state.connection, adapter: "express"});
		const set_options = (name, list, keep) => {
			d.set_df_property(name, "options", list);
			const current = keep ?? d.get_value(name);
			if (current && list.some(row => row.value === current)) return d.set_value(name, current);
			if (list.length === 1) return d.set_value(name, list[0].value);
			return d.set_value(name, "");
		};
		const needs_stock_group = () => d.get_value("posting_kind") === "stock"
			&& p.intake_targets.stock_group_needed(state.options?.stock_group);
		const paint_stock = () => {
			const show = needs_stock_group();
			d.set_df_property("stock_acc_group", "hidden", show ? 0 : 1);
			if (show) set_options("stock_acc_group", p.intake_targets.stock_group_options(state.options.stock_group));
		};
		// 只摆这条规则用得到的项（与服务端 `contracts.applicable` 同一口径）。
		const paint_defaults = () => {
			const purchase = d.get_value("direction") === "purchase", stock = d.get_value("posting_kind") === "stock";
			const show = {payment: purchase, vat: purchase, account: purchase && !stock, item: stock,
				department: Boolean(state.options?.departments?.length)};
			for (const [kind, visible] of Object.entries(show)) d.set_df_property("d_" + kind, "hidden", visible ? 0 : 1);
			set_options("d_account", (state.options?.accounts || []).map(row => ({value: row.code, label: `${row.code} · ${row.name}`})),
				rule?.defaults?.account);
			set_options("d_department", (state.options?.departments || []).map(row => ({value: row.code,
				label: `${row.code} · ${row.name}`})), rule?.defaults?.department);
		};
		// 文件夹一个下拉：本机允许读取的根目录与其下的文件夹，值是「根目录编号|相对路径」。
		const paint_folders = () => {
			const folders = (state.options?.roots || []).flatMap(item => [
				{value: `${item.id}|`, label: item.label},
				...(item.folders || []).map(name => ({value: `${item.id}|${name}`, label: `${item.label}\\${name}`}))]);
			// 小助手这会儿还没报上来（刚重启等）：规则原来的文件夹照样摆着，不让编辑时被清空。
			const saved = rule ? `${rule.root_id}|${rule.folder || ""}` : null;
			if (saved && !folders.some(row => row.value === saved)) {
				folders.unshift({value: saved, label: [rule.root_label, rule.folder].filter(Boolean).join("\\") || rule.folder});
			}
			set_options("folder", folders, rule ? `${rule.root_id}|${rule.folder || ""}` : null);
			// 小助手在线但这台电脑还没给这条连接添加文件夹：下拉是空的，说清去哪添加。
			const hint = !state.options ? "cowork.folder.folderHint" : !state.options.agent?.fresh ? "cowork.folder.agentStaleHint"
				: !(state.options.roots || []).length ? "cowork.folder.noRoots" : "cowork.folder.folderHint";
			d.set_df_property("folder", "description", ft(hint));
		};
		const load_options = async () => {
			if (!state.connection) return;
			const ticket = ++state.loading;  // 连着换了几次账套：只认最后一次的结果
			const options = await quiet(API + "options", {connection: state.connection,
				account_set_key: d.get_value("account_set_key") || undefined}).catch(() => null);
			if (ticket !== state.loading || !options) return;
			state.options = options;
			paint_folders();
			paint_stock();
			paint_defaults();
		};
		const on_connection = async () => {
			state.connection = d.get_value("connection") || state.connection;
			if (!state.connection) return;
			const view = await quiet(CONNECTIONS + "targets", {name: state.connection}).catch(() => null);
			state.groups = view?.groups || [];
			const owner = state.groups.find(group => group.items.some(item => item.value === state.account_set_key));
			await set_options("year", state.groups.map(row => ({value: row.value, label: row.label})), owner?.value);
			await on_year();
		};
		const on_year = async () => {
			const group = state.groups.find(row => row.value === d.get_value("year"));
			await set_options("account_set_key", (group?.items || []).map(row => ({value: row.value, label: row.label})),
				state.account_set_key);
			await on_account();
		};
		const on_account = async () => {
			const next = d.get_value("account_set_key") || null;
			// 换年度时下拉先被清空一次：不把规则原来的账套也清掉，等新的选项铺好再按它预选。
			if (!next || (next === state.account_set_key && state.options)) return;
			state.account_set_key = next;
			// 与新建录入一样：换选了账套就请小助手读这份账套的主档（科目、部门、存货科目组）；打开已有规则不重复请求。
			if (next !== rule?.account_set_key) p.intake_targets.prefetch_account(connection(), next);
			await load_options();
		};
		for (const kind of ["payment", "vat", "account", "department", "item"]) {
			if (rule?.defaults?.[kind]) d.set_value("d_" + kind, rule.defaults[kind]);
		}
		if (state.connection) d.set_value("connection", state.connection);
		void on_connection();
		return d;
	}

	/** 运行记录：每次扫了几份、传了几份，以及这一次的文件各走到哪一步。 */
	async function runs(rule) {
		const view = await p.call(API + "runs", {name: rule.name});
		const STATES = ["done", "pushing", "recognized", "submitted", "source_ready", "uploaded", "registered",
			"not_invoice", "failed"];
		const lines = (view.runs || []).map(run => {
			const states = STATES.filter(name => run.states?.[name])
				.map(name => `${ft("cowork.folder.state." + name)} ${run.states[name]}`).join(" · ");
			const missing = ["root_missing", "folder_missing"].includes(run.note) ? ` · ${ft("cowork.folder.folderMissing")}` : "";
			return `<div class="pearnly-folder-run"><div>${p.escape(frappe.datetime.str_to_user(run.started_at))} · `
				+ `${p.escape(ft("cowork.folder.trigger." + (run.trigger || "schedule")) + missing)}</div>`
				+ `<div class="text-muted small">${p.escape(ft("cowork.folder.runScan", run.found || 0, run.new_files || 0,
					run.skipped || 0, run.upload_failed || 0))}${states ? " · " + p.escape(states) : ""}</div></div>`;
		}).join("") || `<div class="text-muted">${p.escape(ft("cowork.folder.noRuns"))}</div>`;
		const d = new frappe.ui.Dialog({title: `${ft("cowork.folder.runs")} · ${rule.title}`,
			fields: [{fieldtype: "HTML", fieldname: "runs", options: lines}]});
		d.$wrapper.addClass("pearnly-dialog");
		d.show();
	}

	// 「新建录入」弹窗的第四张卡片（所有 Cowork 租户都有，用户 2026-10-07：功能不按租户开关）。
	p.intake_folder_card = () => ({value: "folder", label: ft("cowork.folder.title")});
})();
