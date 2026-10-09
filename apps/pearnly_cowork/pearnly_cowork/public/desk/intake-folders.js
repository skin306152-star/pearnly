/* 定时任务（原「自动录入规则」，2026-10-09 改名并加 MR.ERP；录入工作台子路由 `pearnly-intake/folders`，2026-10-07）。
 *
 * 版面照录入工作台本身：原生列表（`p.list`）一行一个任务，点一行打开原生弹窗编辑；
 * 行尾一颗「立即运行」，停用 / 启用与运行记录放在弹窗左下角官方的自定义动作位；勾选后右上角「删除」。
 * 任务 = 新建录入那一组前置条件（目标 ERP 卡片起）+ 文件夹 + 每月几号几点 + 遇到时的默认处理（Express）。
 * 本功能的词条在 Cowork 自己的翻译包里，一律经原生 `__()` 取（录入工作台按前缀取的文案包里没有 `cowork.`）。
 */
(() => {
	const p = frappe.pearnly;
	const API = "pearnly_cowork.folder_intake.api.";
	const CONNECTIONS = "pearnly_cowork.connections.api.";
	const ft = (key, ...args) => __(key, args);
	// 任务弹窗里的读取（目录、选项）只是为了铺下拉：读不到就下次再铺，不弹错误框打断填写。
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
		// 文件夹不可用：小助手报这个任务的文件夹不在了（授权移除 / 改名 / 子文件夹删了），到点也不会跑。
		const status = row => (!row.enabled ? ["gray", ft("cowork.folder.off")]
			: !row.agent_fresh ? ["orange", ft("cowork.folder.agentStale")]
			: ["root_missing", "folder_missing"].includes(row.folder_state) ? ["red", ft("cowork.folder.folderMissing")]
			: ["green", ft("cowork.folder.on")]);
		const edit = rule => void open_rule({t, rule, on_saved: () => list.reload()});
		const create = () => page.set_primary_action(ft("cowork.folder.new"), () => edit(null));
		// 勾选后右上角换成「删除」（用户 2026-10-08：只加删除）。确认语与提示音照官方列表的批量删除
		// （`list_view.js` 的 bulk_delete：同一句确认、删完只放 delete 音不弹提示）。
		const selection = p.selection_action({
			page, list: () => list, empty: create,
			label: () => __("Delete"),
			confirm: rows => rows.length > 1
				? __("Delete {0} items permanently?", [rows.length], "Title of confirmation dialog")
				: __("Delete {0} item permanently?", [rows.length], "Title of confirmation dialog"),
			run: rows => p.call(API + "delete", {names: JSON.stringify(rows.map(row => row.name))}, "POST"),
			sound: "delete",
			// 官方列表删完不弹提示，只放声音：不给提示文字，声音在这里自己放（`p.success` 没有文字就不放声）。
			success: () => {
				frappe.utils.play_sound("delete");
				return "";
			},
		});

		const list = p.list({
			page, doctype: "Cowork Folder Intake Rule", compact_columns: true,
			columns: [
				{fieldname: "title", width: 200, min_width: 140, label: ft("cowork.folder.field.title")},
				{fieldname: "adapter", width: 110, min_width: 90, label: __("intake.target.title"),
					format: value => p.intake_targets.TARGETS.find(row => row.adapter === value)?.label || ""},
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
			on_selection: selection,
			empty_action: {label: ft("cowork.folder.new"), action: () => edit(null)},
		});
		create();
		await list.ready;
		return () => list.destroy();
	};

	/** 新建 / 编辑一个定时任务（原生弹窗）。
	 *
	 * 与「新建录入」同一套交互（2026-10-09 加 MR.ERP，用户：两者细节、视觉、交互逻辑对齐）：
	 *   1. 目标 ERP 卡片（MR.ERP / Express，同一组件、同一状态与「连接」按钮）；编辑时目标固定；
	 *   2. 两级账套：MR.ERP「公司 → 年度」，Express「年度 → 账套」（`targets.catalog`，同一份目录）；
	 *   3. 进项 / 销项；4. MR.ERP 选付款方式（进项只有赊账），Express 选过账类型（存货且多个科目组时当场选一次）；
	 *   5. 文件夹：这条连接的小助手允许读取的根目录与其下文件夹。MR.ERP 的小助手只读文件夹，没配就「连接小助手」；
	 *   6. 每月几号几点；7. 默认处理只有 Express 有（MR.ERP 的菜单与默认值保存前按「新建录入」同一个弹窗问一次）。
	 */
	async function open_rule({t, rule, on_saved}) {
		let snapshot = await p.intake_targets.state().catch(() => ({connections: {}, agent: null}));
		const ADAPTERS = ["mrerp", "express"];
		const picked = {target: rule?.adapter || null};
		const state = {groups: [], options: null, loading: 0, catalog: 0,
			account_set_key: rule?.account_set_key || null, group: null};
		// 「新建录入」记在这台浏览器上的「这条连接上次选的账套」，同一个键，两边互相预选。
		const remembered = name => {
			try { return name ? localStorage.getItem("pearnly.intake.account_set." + name) || "" : ""; } catch (error) { return ""; }
		};
		const connection = () => (rule ? {name: rule.connection, adapter: rule.adapter}
			: snapshot.connections[picked.target] || null);
		const express = () => picked.target === "express";
		const fields = [
			// 目标 ERP 卡片独占一行（与「新建录入」一样在最上面），下面才分两栏。
			{fieldtype: "HTML", fieldname: "target"},
			{fieldtype: "Section Break"},
			{fieldtype: "Data", fieldname: "title", label: ft("cowork.folder.field.title"), reqd: 1, default: rule?.title || ""},
			{fieldtype: "Select", fieldname: "year", label: __("Company"), options: [], onchange: () => void on_group()},
			{fieldtype: "Select", fieldname: "account_set_key", label: __("intake.target.year"), reqd: 1, options: [],
				max_items: 1000, onchange: () => void on_account()},
			{fieldtype: "Column Break"},
			{fieldtype: "Select", fieldname: "direction", label: t("intake.field.direction"), reqd: 1,
				options: [{value: "purchase", label: t("intake.dir.purchase")}, {value: "sales", label: t("intake.dir.sales")}],
				default: rule?.direction || "purchase", onchange: () => {paint_mode(); paint_defaults();}},
			{fieldtype: "Select", fieldname: "posting_kind", label: __("Posting Kind"),
				options: [{value: "service", label: __("Service")}, {value: "stock", label: __("Stock")}],
				default: rule?.posting_kind || "service", onchange: () => {paint_stock(); paint_defaults();}},
			{fieldtype: "Select", fieldname: "payment", label: __("Payment Method"), options: []},
			{fieldtype: "Select", fieldname: "stock_acc_group", label: __("Inventory account group"), options: [], hidden: 1},
			{fieldtype: "Section Break"},
			{fieldtype: "Select", fieldname: "folder", label: ft("cowork.folder.field.folder"), reqd: 1, options: [],
				max_items: 2000, description: ft("cowork.folder.folderHint")},
			// 按钮一开始隐藏时 Frappe 还没建元素：点击写在字段定义上（同「新建录入」的「重新读取」）。
			{fieldtype: "Button", fieldname: "connect_helper", label: ft("cowork.folder.connectHelper"), hidden: 1,
				click: () => void connect_helper()},
			{fieldtype: "Column Break"},
			{fieldtype: "Select", fieldname: "run_day", label: ft("cowork.folder.field.day"), reqd: 1, options: days(),
				default: String(rule?.run_day ?? 1)},
			{fieldtype: "Select", fieldname: "run_time", label: ft("cowork.folder.field.time"), reqd: 1, options: times(),
				default: rule?.run_time || "22:00"},
			{fieldtype: "Section Break", fieldname: "defaults_section", label: ft("cowork.folder.section.defaults"),
				collapsible: 1},
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
			const target = connection();
			if (!target) return;
			if (needs_stock_group()) {
				if (!values.stock_acc_group) {
					frappe.msgprint(p.escape(ft("cowork.folder.stock_group_needed")));
					return;
				}
				await p.intake_targets.choose_stock_group(target, values.account_set_key, values.stock_acc_group);
			}
			// MR.ERP 的单据菜单与默认值：没定过就用「新建录入」提交前同一个弹窗问一次，之后自动推送照用。
			if (!express() && !(await p.intake_targets.mrerp_menu_ready(target, values.account_set_key, values.direction))) return;
			const defaults = {};
			for (const kind of ["payment", "vat", "account", "department", "item"]) {
				if (express() && !d.fields_dict["d_" + kind].df.hidden && values["d_" + kind]) defaults[kind] = values["d_" + kind];
			}
			const [root_id, ...folder] = String(values.folder || "").split("|");
			await p.call(API + "save", {values: JSON.stringify({name: rule?.name || null, title: values.title,
				connection: target.name, account_set_key: values.account_set_key, direction: values.direction,
				posting_kind: express() ? values.posting_kind : "", payment: express() ? "" : values.payment,
				root_id, folder: folder.join("|"), run_day: Number(values.run_day), run_time: values.run_time, defaults})},
			"POST");
			d.hide();
			p.success(__("Saved"), "click");
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

		// ── 目标 ERP 卡片：与「新建录入」同一组件、同一状态与「连接」按钮 ──
		const host = d.fields_dict.target.$wrapper;
		$("<div class='pearnly-choice-title'>").text(__("intake.target.title")).appendTo(host);
		const target_choice = p.cowork_choice(host, {value: picked.target, enabled: !rule, on_change: value => {
			picked.target = value;
			Object.assign(state, {groups: [], options: null, account_set_key: null, group: null});
			for (const name of ["year", "account_set_key", "folder", "d_account", "d_department"]) d.set_value(name, "");
			open_steps();
		}});
		const paint_targets = () => target_choice.set_items(p.intake_targets.TARGETS
			.filter(target => ADAPTERS.includes(target.adapter))
			.map(target => ({
				value: target.adapter, label: target.label,
				status: rule ? null : p.intake_targets.describe(target.adapter, snapshot),
				disabled: !rule && !p.intake_targets.ready(target.adapter, snapshot),
				action: rule ? null : {label: __("Connect"), on_click: () => void (target.adapter === "express"
					? p.intake_targets.express_dialog({connection: snapshot.connections.express, on_done: refresh})
					: p.intake_targets.connect_dialog({connection: snapshot.connections.mrerp, on_done: refresh}))},
			})));
		const refresh = async () => {
			snapshot = await p.intake_targets.state().catch(() => snapshot);
			paint_targets();
			if (picked.target && !rule && !p.intake_targets.ready(picked.target, snapshot)) return;
			if (picked.target) await load_catalog();
		};

		// ── 账套 / 方向 / 入账方式：与「新建录入」同一口径 ──
		const set_options = (name, list, keep) => {
			d.set_df_property(name, "options", list);
			const current = keep ?? d.get_value(name);
			if (current && list.some(row => row.value === current)) return d.set_value(name, current);
			if (list.length === 1) return d.set_value(name, list[0].value);
			return d.set_value(name, "");
		};
		const enable = (name, on) => d.fields_dict[name].$input?.prop("disabled", !on);
		const paint_mode = () => {
			d.set_df_property("posting_kind", "hidden", express() ? 0 : 1);
			d.set_df_property("payment", "hidden", picked.target && !express() ? 0 : 1);
			d.set_df_property("posting_kind", "reqd", express() ? 1 : 0);
			d.set_df_property("payment", "reqd", picked.target && !express() ? 1 : 0);
			const purchase = d.get_value("direction") === "purchase";
			set_options("payment", (purchase ? [] : [{value: "cash", label: __("Cash")}]).concat(
				[{value: "credit", label: __("Credit")}]), rule?.payment);
			paint_stock();
		};
		const needs_stock_group = () => express() && d.get_value("posting_kind") === "stock"
			&& p.intake_targets.stock_group_needed(state.options?.stock_group);
		const paint_stock = () => {
			const show = needs_stock_group();
			d.set_df_property("stock_acc_group", "hidden", show ? 0 : 1);
			if (show) set_options("stock_acc_group", p.intake_targets.stock_group_options(state.options.stock_group));
		};
		// 默认处理只摆这条任务用得到的项（与服务端 `contracts.applicable` 同一口径）；MR.ERP 整段不摆。
		const paint_defaults = () => {
			const purchase = d.get_value("direction") === "purchase", stock = d.get_value("posting_kind") === "stock";
			d.set_df_property("defaults_section", "hidden", express() ? 0 : 1);
			const show = {payment: purchase, vat: purchase, account: purchase && !stock, item: stock,
				department: Boolean(state.options?.departments?.length)};
			for (const [kind, visible] of Object.entries(show)) d.set_df_property("d_" + kind, "hidden", express() && visible ? 0 : 1);
			set_options("d_account", (state.options?.accounts || []).map(row => ({value: row.code, label: `${row.code} · ${row.name}`})),
				rule?.defaults?.account);
			set_options("d_department", (state.options?.departments || []).map(row => ({value: row.code,
				label: `${row.code} · ${row.name}`})), rule?.defaults?.department);
		};
		// 文件夹一个下拉：这条连接的小助手允许读取的根目录与其下的文件夹，值是「根目录编号|相对路径」。
		const paint_folders = () => {
			const folders = (state.options?.roots || []).flatMap(item => [
				{value: `${item.id}|`, label: item.label},
				...(item.folders || []).map(name => ({value: `${item.id}|${name}`, label: `${item.label}\\${name}`}))]);
			// 小助手这会儿还没报上来（刚重启等）：任务原来的文件夹照样摆着，不让编辑时被清空。
			const saved = rule ? `${rule.root_id}|${rule.folder || ""}` : null;
			if (saved && !folders.some(row => row.value === saved)) {
				folders.unshift({value: saved, label: [rule.root_label, rule.folder].filter(Boolean).join("\\") || rule.folder});
			}
			set_options("folder", folders, saved);
			enable("folder", Boolean(folders.length));
			const agent = state.options?.agent;
			// 没配过小助手（只可能是 MR.ERP：Express 卡片要小助手在线才可选）→「连接小助手」，同「连接 Express」的弹窗。
			const unpaired = Boolean(picked.target) && !express() && !agent?.version && !(state.options?.roots || []).length;
			d.set_df_property("connect_helper", "hidden", unpaired && !rule ? 0 : 1);
			const hint = !state.options ? "cowork.folder.folderHint" : unpaired ? "cowork.folder.noRootsAny"
				: !agent?.fresh ? "cowork.folder.agentStaleHint"
				: !(state.options.roots || []).length ? "cowork.folder.noRoots" : "cowork.folder.folderHint";
			d.set_df_property("folder", "description", ft(hint));
		};
		const connect_helper = async () => {
			const target = connection();
			if (!target) return;
			await p.intake_targets.helper_dialog({connection: target, title: ft("cowork.folder.connectHelper"),
				on_done: () => void load_options()});
		};
		const load_options = async () => {
			const target = connection();
			if (!target) return;
			const ticket = ++state.loading;  // 连着换了几次账套：只认最后一次的结果
			const options = await quiet(API + "options", {connection: target.name,
				account_set_key: express() ? d.get_value("account_set_key") || undefined : undefined}).catch(() => null);
			if (ticket !== state.loading || !options) return;
			state.options = options;
			paint_folders();
			paint_stock();
			paint_defaults();
		};
		// 两级账套：MR.ERP「公司 → 年度」，Express「年度 → 账套」（与「新建录入」同一份目录与标签）。
		const load_catalog = async () => {
			const target = connection();
			const run = ++state.catalog;
			d.set_df_property("year", "label", express() ? __("intake.target.year") : __("Company"));
			d.set_df_property("account_set_key", "label", express() ? __("intake.target.accountSet") : __("intake.target.year"));
			if (!target) return;
			await p.intake_targets.catalog(target, {alive: () => run === state.catalog, on_update: view => {
				state.groups = view.groups || [];
				let note = "";
				if (view.loading) note = __("intake.catalog.loading", [p.intake_targets.TARGETS.find(row => row.adapter === picked.target)?.label || ""]);
				else if (view.error_code) note = p.intake_targets.catalog_error(view.error_code);
				else if (!state.groups.length) note = __("intake.catalog.ERR_ACCOUNT_SET_EMPTY");
				d.set_df_property("year", "description", note);
				// 预选与「新建录入」一样：这个任务原来的账套 → 这台浏览器上次在这条连接选的 → 连接的默认账套。
				// Express 的账套键是规范化后的路径（小写、反斜杠），连接上存的默认账套是原样路径。
				const norm = value => String(value || "").replace(/\//g, "\\").replace(/\\+$/, "").toLowerCase();
				let found = null;
				for (const key of [state.account_set_key, remembered(target.name), view.default_key]) {
					for (const group of key ? state.groups : []) {
						const item = group.items.find(row => norm(row.value) === norm(key));
						if (item && !found) found = {group, item};
					}
				}
				if (found) state.account_set_key = found.item.value;
				set_options("year", state.groups.map(row => ({value: row.value, label: row.label})), found?.group.value);
				enable("year", Boolean(state.groups.length));
				void on_group();
			}});
			await load_options();
		};
		const on_group = async () => {
			const group = state.groups.find(row => row.value === d.get_value("year"));
			const items = (group?.items || []).map(row => ({value: row.value, label: row.label}));
			set_options("account_set_key", items, state.account_set_key);
			enable("account_set_key", Boolean(items.length));
			await on_account();
		};
		const on_account = async () => {
			const next = d.get_value("account_set_key") || null;
			// 换年度时下拉先被清空一次：不把任务原来的账套也清掉，等新的选项铺好再按它预选。
			if (!next || (next === state.account_set_key && state.options)) return;
			state.account_set_key = next;
			// 与「新建录入」一样：选了 Express 账套就请小助手读这份账套的主档（科目、部门、存货科目组）；打开已有任务不重复请求。
			if (express() && next !== rule?.account_set_key) p.intake_targets.prefetch_account(connection(), next);
			await load_options();
		};
		const open_steps = () => {
			paint_mode();
			paint_defaults();
			paint_folders();
			for (const name of ["year", "account_set_key", "direction", "posting_kind", "payment", "folder", "run_day", "run_time"]) {
				enable(name, Boolean(picked.target));
			}
			if (picked.target) void load_catalog();
		};
		for (const kind of ["payment", "vat", "account", "department", "item"]) {
			if (rule?.defaults?.[kind]) d.set_value("d_" + kind, rule.defaults[kind]);
		}
		paint_targets();
		open_steps();
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
