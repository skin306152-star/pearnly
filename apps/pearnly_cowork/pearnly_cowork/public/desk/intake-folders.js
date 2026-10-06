/* 自动录入规则（录入工作台子路由 `pearnly-intake/folders`，2026-10-07）。
 *
 * 入口：「新建录入」弹窗里的第四张卡片。一页列出所有规则，每条各自「立即运行 / 编辑 / 运行记录 / 停用」。
 * 规则 = 新建录入那一组前置条件（Express 连接、年度账套、进项 / 销项、服务 / 存货）+ 文件夹 + 每月几号几点
 * + 「这一类以后都这样处理」。页面顶上直接摆文件夹放法，规定写清楚，少让人猜。
 * 全部用原生 Page / Dialog / 控件；出错只影响本页，不碰录入列表（`intake.js` 里包了一层）。
 */
(() => {
	const p = frappe.pearnly;
	const API = "pearnly_cowork.folder_intake.api.";
	const CONNECTIONS = "pearnly_cowork.connections.api.";
	const RESULT_FOLDER = "_Pearnly结果";
	// 本功能的词条在 Cowork 自己的翻译包里（`cowork.` 前缀不在录入工作台那份按前缀取的文案包中），一律经原生 `__()` 取。
	const ft = (key, ...args) => __(key, args);
	// 选项在打开弹窗时才生成：脚本加载时翻译表可能还没就绪。
	const days = () => [...Array.from({length: 28}, (_, i) => ({value: String(i + 1), label: ft("cowork.folder.day", i + 1)})),
		{value: "0", label: ft("cowork.folder.lastDay")}];
	const TIMES = Array.from({length: 48}, (_, i) => {
		const value = `${String(Math.floor(i / 2)).padStart(2, "0")}:${i % 2 ? "30" : "00"}`;
		return {value, label: value};
	});

	/** 文件夹放法与规定（页面顶上、规则弹窗顶上同一份）。 */
	const guide = t => {
		const line = (indent, text, note = "") => `<div style="padding-left:${indent * 18}px">${p.escape(text)}`
			+ (note ? ` <span class="text-muted">← ${p.escape(note)}</span>` : "") + "</div>";
		return `<div class="pearnly-folder-guide">
			<div class="text-muted small">${p.escape(ft("cowork.folder.guide.title"))}</div>
			<div class="pearnly-folder-tree">
				${line(0, "S:\\Pearnly\\", ft("cowork.folder.guide.root"))}
				${line(1, ft("cowork.folder.guide.sampleA") + "\\", ft("cowork.folder.guide.rule", 1))}
				${line(2, "2026-10\\", ft("cowork.folder.guide.sub"))}
				${line(3, "INV-001.pdf · photo.jpg")}
				${line(2, RESULT_FOLDER + "\\2026-10\\", ft("cowork.folder.guide.result"))}
				${line(1, ft("cowork.folder.guide.sampleB") + "\\", ft("cowork.folder.guide.rule", 2))}
			</div>
			<ul class="pearnly-folder-rules">
				${["one", "sub", "types", "result", "year", "since"].map(name =>
					`<li>${p.escape(ft("cowork.folder.guide." + name + "Rule"))}</li>`).join("")}
			</ul>
		</div>`;
	};

	const direction_label = (t, value) => t(value === "sales" ? "intake.dir.sales" : "intake.dir.purchase");
	const kind_label = value => __(value === "stock" ? "Stock" : "Service");
	const schedule_label = (t, rule) => ft("cowork.folder.schedule", days().find(row => row.value === String(rule.run_day))?.label
		|| "", rule.run_time || "");

	p.intake_folders = async ({page, t, go, still_current}) => {
		const root = "pearnly-intake";
		page.set_title(ft("cowork.folder.title"));
		p.breadcrumbs({label: t("intake.title"), route: p.subroute(root), trail: [{label: ft("cowork.folder.title"), route: ""}]});
		const body = $("<div class='pearnly-folder-page'>").appendTo(page.main);
		let data = {rules: [], connections: []};

		const load = async () => {
			data = await p.call(API + "listing");
			if (!still_current()) return;
			paint();
		};

		const paint = () => {
			body.empty();
			$(guide(t)).appendTo(body);
			const list = $("<div class='frappe-list pearnly-folder-list'>").appendTo(body);
			if (!data.connections.length) {
				$("<div class='text-muted pearnly-folder-empty'>").text(ft("cowork.folder.noExpress")).appendTo(list);
				return;
			}
			if (!data.rules.length) {
				$("<div class='text-muted pearnly-folder-empty'>").text(ft("cowork.folder.empty")).appendTo(list);
				return;
			}
			for (const rule of data.rules) row(rule).appendTo(list);
		};

		const counts_text = rule => {
			const c = rule.counts || {};
			const pushed = c.done || 0;
			const waiting = (c.registered || 0) + (c.uploaded || 0) + (c.source_ready || 0) + (c.submitted || 0)
				+ (c.recognized || 0) + (c.pushing || 0);
			return ft("cowork.folder.counts", pushed, waiting, c.failed || 0, c.not_invoice || 0);
		};

		const row = rule => {
			const item = $("<div class='list-row-container pearnly-folder-row'>");
			const main = $("<div class='pearnly-folder-main'>").appendTo(item);
			$("<div class='pearnly-folder-name bold'>").text(rule.title).appendTo(main);
			$("<div class='text-muted small'>").text([rule.connection_label, rule.account_label, direction_label(t, rule.direction),
				kind_label(rule.posting_kind)].filter(Boolean).join(" · ")).appendTo(main);
			$("<div class='text-muted small'>").text([`${rule.folder || ft("cowork.folder.rootItself")}`,
				schedule_label(t, rule)].join(" · ")).appendTo(main);
			const side = $("<div class='pearnly-folder-side'>").appendTo(item);
			const [tone, label] = !rule.enabled ? ["gray", ft("cowork.folder.off")]
				: !rule.agent_fresh ? ["orange", ft("cowork.folder.agentStale")] : ["green", ft("cowork.folder.on")];
			$("<span class='indicator-pill'>").addClass(tone).text(label).appendTo(side);
			$("<div class='text-muted small'>").text(counts_text(rule)).appendTo(side);
			const actions = $("<div class='pearnly-folder-actions'>").appendTo(item);
			const button = (text, action, enabled = true) => $("<button type='button' class='btn btn-default btn-xs'>")
				.text(text).prop("disabled", !enabled).on("click", () => void action()).appendTo(actions);
			button(ft("cowork.folder.runNow"), () => run_now(rule), Boolean(rule.enabled));
			button(__("Edit"), () => edit(rule));
			button(ft("cowork.folder.runs"), () => runs(rule));
			button(rule.enabled ? ft("cowork.folder.disable") : ft("cowork.folder.enable"), () => toggle(rule));
			return item;
		};

		const run_now = async rule => {
			await p.call(API + "run_now", {name: rule.name}, "POST");
			frappe.show_alert({message: p.escape(ft("cowork.folder.runRequested")), indicator: "green"});
			await load();
		};

		const toggle = rule => {
			const next = rule.enabled ? 0 : 1;
			frappe.confirm(p.escape(ft(next ? "cowork.folder.enableConfirm" : "cowork.folder.disableConfirm")), async () => {
				await p.call(API + "set_enabled", {name: rule.name, enabled: next}, "POST");
				await load();
			});
		};

		const runs = async rule => {
			const view = await p.call(API + "runs", {name: rule.name});
			const STATES = ["done", "pushing", "recognized", "submitted", "source_ready", "uploaded", "registered",
				"not_invoice", "failed"];
			const html = (view.runs || []).map(run => {
				const states = STATES.filter(name => run.states?.[name])
					.map(name => `${p.escape(ft("cowork.folder.state." + name))} ${run.states[name]}`).join(" · ");
				return `<div class="pearnly-folder-run">
					<div class="bold">${p.escape(frappe.datetime.str_to_user(run.started_at))} · ${p.escape(ft("cowork.folder.trigger." + (run.trigger || "schedule")))}</div>
					<div class="text-muted small">${p.escape(ft("cowork.folder.runScan", run.found || 0, run.new_files || 0, run.skipped || 0, run.upload_failed || 0))}</div>
					${states ? `<div class="small">${states}</div>` : ""}
					${run.note ? `<div class="text-muted small">${p.escape(run.note)}</div>` : ""}
				</div>`;
			}).join("") || `<div class="text-muted">${p.escape(ft("cowork.folder.noRuns"))}</div>`;
			const d = new frappe.ui.Dialog({title: `${ft("cowork.folder.runs")} · ${rule.title}`,
				fields: [{fieldtype: "HTML", fieldname: "runs", options: html}]});
			d.$wrapper.addClass("pearnly-dialog");
			d.show();
		};

		const edit = rule => open_rule({t, data, rule, on_saved: load});

		page.set_primary_action(ft("cowork.folder.new"), () => open_rule({t, data, rule: null, on_saved: load}));
		p.add_refresh(page, () => load());
		await load();
		return () => body.remove();
	};

	/** 新建 / 编辑一条规则（原生弹窗）。 */
	function open_rule({t, data, rule, on_saved}) {
		const state = {connection: rule?.connection || data.connections[0]?.name || null, groups: [], options: null,
			year: null, account_set_key: rule?.account_set_key || null};
		const opts = list => list.map(row => ({value: row.value, label: row.label}));
		const fields = [
			{fieldtype: "HTML", fieldname: "guide", options: guide(t)},
			{fieldtype: "Data", fieldname: "title", label: ft("cowork.folder.field.title"), reqd: 1, default: rule?.title || ""},
			{fieldtype: "Section Break", label: ft("cowork.folder.section.target")},
			{fieldtype: "Select", fieldname: "connection", label: ft("cowork.folder.field.connection"), reqd: 1,
				options: data.connections.map(row => ({value: row.name, label: row.label})), default: state.connection,
				read_only: rule ? 1 : 0, onchange: () => on_connection()},
			{fieldtype: "Select", fieldname: "year", label: __("intake.target.year"), options: [], onchange: () => on_year()},
			{fieldtype: "Select", fieldname: "account_set_key", label: __("intake.target.accountSet"), reqd: 1, options: [],
				max_items: 1000, onchange: () => on_account()},
			{fieldtype: "Select", fieldname: "direction", label: t("intake.field.direction"), reqd: 1,
				options: [{value: "purchase", label: t("intake.dir.purchase")}, {value: "sales", label: t("intake.dir.sales")}],
				default: rule?.direction || "purchase", onchange: () => paint_defaults()},
			{fieldtype: "Select", fieldname: "posting_kind", label: __("Posting Kind"), reqd: 1,
				options: [{value: "service", label: __("Service")}, {value: "stock", label: __("Stock")}],
				default: rule?.posting_kind || "service", onchange: () => {paint_stock(); paint_defaults();}},
			{fieldtype: "Select", fieldname: "stock_acc_group", label: __("Inventory account group"), options: [], hidden: 1},
			{fieldtype: "Section Break", label: ft("cowork.folder.section.folder")},
			{fieldtype: "Select", fieldname: "root_id", label: ft("cowork.folder.field.root"), reqd: 1, options: [],
				onchange: () => paint_folders()},
			{fieldtype: "Select", fieldname: "folder", label: ft("cowork.folder.field.folder"), options: [], max_items: 2000},
			{fieldtype: "Column Break"},
			{fieldtype: "Select", fieldname: "run_day", label: ft("cowork.folder.field.day"), reqd: 1, options: days(),
				default: String(rule?.run_day ?? 1)},
			{fieldtype: "Select", fieldname: "run_time", label: ft("cowork.folder.field.time"), reqd: 1, options: TIMES,
				default: rule?.run_time || "22:00"},
			{fieldtype: "Section Break", label: ft("cowork.folder.section.defaults"),
				description: ft("cowork.folder.defaultsHint")},
			{fieldtype: "Select", fieldname: "d_payment", label: ft("cowork.folder.default.payment"),
				options: [{value: "credit", label: __("Credit")}, {value: "cash", label: __("Cash")}]},
			{fieldtype: "Select", fieldname: "d_vat", label: ft("cowork.folder.default.vat"),
				options: [{value: "claim", label: ft("cowork.folder.vat.claim")}, {value: "expense", label: ft("cowork.folder.vat.expense")}]},
			{fieldtype: "Select", fieldname: "d_account", label: ft("cowork.folder.default.account"), options: [], max_items: 2000},
			{fieldtype: "Select", fieldname: "d_department", label: ft("cowork.folder.default.department"), options: []},
			{fieldtype: "Select", fieldname: "d_item", label: ft("cowork.folder.default.item"),
				options: [{value: "__new__", label: ft("cowork.folder.item.new")}, {value: "ask", label: ft("cowork.folder.item.ask")}]},
		];
		const d = p.dialog(rule ? ft("cowork.folder.edit") : ft("cowork.folder.new"), fields, __("Save"), async values => {
			if (needs_stock_group()) {
				if (!values.stock_acc_group) {
					frappe.msgprint(p.escape(ft("cowork.folder.stock_group_needed")));
					return;
				}
				await p.intake_targets.choose_stock_group(connection(), values.account_set_key, values.stock_acc_group);
			}
			const defaults = {};
			for (const kind of ["payment", "vat", "account", "department", "item"]) {
				if (d.fields_dict["d_" + kind].df.hidden) continue;
				if (values["d_" + kind]) defaults[kind] = values["d_" + kind];
			}
			await p.call(API + "save", {values: JSON.stringify({name: rule?.name || null, title: values.title,
				connection: values.connection, account_set_key: values.account_set_key, direction: values.direction,
				posting_kind: values.posting_kind, root_id: values.root_id, folder: values.folder === "\\" ? "" : values.folder || "",
				run_day: Number(values.run_day), run_time: values.run_time, defaults})}, "POST");
			d.hide();
			frappe.show_alert({message: p.escape(__("Saved")), indicator: "green"});
			await on_saved();
		});
		d.$wrapper.addClass("pearnly-folder-dialog");
		const connection = () => ({name: state.connection, adapter: "express"});
		const set_options = (name, list, keep) => {
			d.set_df_property(name, "options", list);
			const current = keep ?? d.get_value(name);
			if (current && list.some(row => (row.value ?? row) === current)) d.set_value(name, current);
			else if (list.length === 1) d.set_value(name, list[0].value ?? list[0]);
			else d.set_value(name, "");
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
			const accounts = state.options?.accounts;
			set_options("d_account", (accounts || []).map(row => ({value: row.code, label: `${row.code} · ${row.name}`})),
				rule?.defaults?.account);
			set_options("d_department", (state.options?.departments || []).map(row => ({value: row.code,
				label: `${row.code} · ${row.name}`})), rule?.defaults?.department);
			d.set_df_property("d_account", "description", purchase && !stock && accounts == null
				? ft("cowork.folder.mastersLoading") : "");
		};
		const paint_folders = () => {
			const root = (state.options?.roots || []).find(row => row.id === d.get_value("root_id"));
			const folders = [{value: "\\", label: ft("cowork.folder.rootItself")},
				...((root?.folders) || []).map(folder => ({value: folder, label: folder}))];
			set_options("folder", folders, rule && rule.root_id === root?.id ? (rule.folder || "\\") : null);
		};
		const load_options = async () => {
			state.options = await p.call(API + "options", {connection: state.connection,
				account_set_key: d.get_value("account_set_key") || undefined}).catch(() => null);
			const roots = state.options?.roots || [];
			set_options("root_id", roots.map(row => ({value: row.id, label: row.label})), rule?.root_id);
			d.set_df_property("root_id", "description", state.options?.agent?.fresh ? ""
				: ft("cowork.folder.agentStaleHint"));
			paint_folders();
			paint_stock();
			paint_defaults();
		};
		const on_connection = async () => {
			state.connection = d.get_value("connection");
			const view = state.connection ? await p.call(CONNECTIONS + "targets", {name: state.connection}).catch(() => null) : null;
			state.groups = view?.groups || [];
			d.set_df_property("year", "description", state.groups.length ? "" : ft("cowork.folder.catalogEmpty"));
			const owner = state.groups.find(group => group.items.some(item => item.value === state.account_set_key));
			set_options("year", opts(state.groups), owner?.value);
			on_year();
		};
		const on_year = () => {
			const group = state.groups.find(row => row.value === d.get_value("year"));
			set_options("account_set_key", opts(group?.items || []), state.account_set_key);
			void on_account();
		};
		const on_account = async () => {
			state.account_set_key = d.get_value("account_set_key") || null;
			// 与新建录入一样：选定账套就请小助手读这份账套的主档（科目、部门、存货科目组），读完下拉里才有。
			if (state.account_set_key) p.intake_targets.prefetch_account(connection(), state.account_set_key);
			await load_options();
		};
		for (const kind of ["payment", "vat", "account", "department", "item"]) {
			if (rule?.defaults?.[kind]) d.set_value("d_" + kind, rule.defaults[kind]);
		}
		void on_connection();
		return d;
	}

	// 「新建录入」弹窗的第四张卡片：只有站点开了文件夹自动录入（启动信息里的总闸）才摆，没开的站点弹窗照旧三张。
	p.intake_folder_card = () => (frappe.boot.pearnly_cowork_folder_intake
		? {value: "folder", label: ft("cowork.folder.title"), hint: ft("cowork.folder.cardHint")} : null);
})();
