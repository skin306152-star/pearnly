/* 「新建录入」的弹窗：这一批的口径都在这里选完，二级页面只做上传与复核。
 *
 *   1. 目标 ERP（MR.ERP / Express / 仅识别）—— 先看连接状态：没连上 / 助手不在线的那张卡不可选，
 *      卡片自带「连接」；Express 的助手状态开着弹窗时每 30 秒重读一次（旧版同一节奏）；
 *   2. 公司、年度 —— **目标 ERP 自己的账套目录，实时读取**（用户 2026-09-26，见 `targets.js`
 *      的 `catalog`）：两家都是「公司 → 年度」；先给服务端已有的那份，同时刷新一次，刷新到了就换成最新的；
 *   3. 进项 / 销项；
 *   4. 入账方式 —— **以第三方为准**（用户 2026-09-26），与旧线上 `mode_options` 一致：
 *      · MR.ERP 选付款方式（现金 / 赊账）；进项只有赊账（旧版对 MR.ERP 进项现金明确拦截，
 *        `mrerp_purchase_cash_unverified`）；不选过账类型；
 *      · Express 选过账类型（服务 / 存货）；付款按票面由六级漏斗判定，复核页仍可逐张改。
 *   「仅识别」（用户 2026-09-26）：识别后导出复核工作簿、不推送，只选进项 / 销项。
 * 这几排是**官方下拉**（用户 2026-09-25：ERP 那种卡片只留给目标系统）。都选好「继续」才可点；
 * 目标系统没就绪之前，后面几排没有选项、也点不动。目标卡片只有一份实现（`choice.js`）。
 */
(() => {
	const p = frappe.pearnly;

	p.intake_create = async (t, on_start) => {
		let snapshot = null;
		let catalog = null;            // 当前目标的公司/年度目录（见 `targets.js` 的 `catalog`）
		let generation = 0;            // 换目标就作废上一次的目录轮询
		const picked = {target: null, connection: null, company: null, account_set_key: null,
			account_label: null, direction: null, posting_kind: null, payment: null};
		const STEP_FIELDS = ["company", "year", "direction", "posting_kind", "payment"];
		// 每个目标在入口要选的那一样（旧线上 `line_target_projection.mode_options`）。
		const MODE = {mrerp: "payment", express: "posting_kind"};
		const options = {
			direction: () => [{value: "purchase", label: t("intake.dir.purchase")},
				{value: "sales", label: t("intake.dir.sales")}],
			posting_kind: () => [{value: "service", label: __("Service")}, {value: "stock", label: __("Stock")}],
			payment: () => (picked.direction === "purchase" ? [] : [{value: "cash", label: __("Cash")}])
				.concat([{value: "credit", label: __("Credit")}]),
			company: () => (catalog?.companies || []).map(({value, label}) => ({value, label})),
			year: () => (catalog?.companies || []).find(row => row.value === picked.company)?.years || [],
		};

		// 下拉的变化一律走字段自己的 `onchange`（用户选和程序赋值都经过它）：产品皮肤把下拉换成了
		// 可搜索的组合框，输入框上的原生 change 只在失焦时才有，靠不住。
		const changed = name => () => on_change(name);
		const fields = [
			{fieldtype: "HTML", fieldname: "target"},
			{fieldtype: "Select", fieldname: "company", label: __("Company"), options: [], onchange: changed("company")},
			{fieldtype: "Select", fieldname: "year", label: __("intake.target.year"), options: [], onchange: changed("year")},
			// 按钮一开始是隐藏的，Frappe 这时还没建按钮元素：点击要写在字段定义上，不能事后绑。
			{fieldtype: "Button", fieldname: "catalog_retry", label: __("Retry"), hidden: 1,
				click: () => load_catalog()},
			{fieldtype: "Select", fieldname: "direction", label: t("intake.field.direction"), options: [],
				onchange: changed("direction")},
			{fieldtype: "Select", fieldname: "posting_kind", label: __("Posting Kind"), options: [], hidden: 1,
				onchange: changed("posting_kind")},
			{fieldtype: "Select", fieldname: "payment", label: __("Payment Method"), options: [], hidden: 1,
				onchange: changed("payment")},
		];
		const d = p.dialog(t("intake.action.new"), fields, __("Continue"), () => {
			d.hide();
			on_start({...picked, ...(catalog?.evidence || {})});
		});

		const mode = () => MODE[picked.target] || null;
		const recognize_only = () => picked.target === p.intake_targets.RECOGNIZE_ONLY;
		const gated = () => Boolean(snapshot) && p.intake_targets.ready(picked.target, snapshot);
		// 「继续」只在该选的都选好时可用；其余情况是真禁用（不是只看着灰）。
		const paint_primary = () => {
			const ready = gated() && Boolean(picked.direction
				&& (recognize_only() || (picked.account_set_key && mode() && picked[mode()])));
			if (ready) d.enable_primary_action();
			else d.disable_primary_action();
			d.get_primary_btn?.()?.prop("disabled", !ready);
		};
		const set_value = (name, value) => {
			const key = name === "year" ? "account_set_key" : name;
			picked[key] = value || null;
			if (d.get_value(name) !== (value || "")) d.set_value(name, value || "");
		};
		/** 按当前状态重铺一排下拉：没就绪就清空并禁用；选过的值不在新选项里就清掉。 */
		const paint = name => {
			const ready = gated() && (name !== "year" || Boolean(picked.company));
			const list = ready ? options[name]() : [];
			d.set_df_property(name, "options", list);
			d.fields_dict[name].$input?.prop("disabled", !ready || !list.length);
			const key = name === "year" ? "account_set_key" : name;
			if (picked[key] && !list.some(row => row.value === picked[key])) set_value(name, null);
			else if (!picked[key] && d.get_value(name)) d.set_value(name, "");  // 上一级换了：清掉旧显示
			if (!picked[key] && list.length === 1 && ["company", "year", "payment"].includes(name)) {
				set_value(name, list[0].value);  // 只有一个可选就直接选上（例如 MR.ERP 进项只有赊账）
			}
		};
		const paint_year = () => {
			paint("year");
			const year = options.year().find(row => row.value === picked.account_set_key);
			const company = options.company().find(row => row.value === picked.company);
			picked.account_label = year ? [company?.label, year.label].filter(Boolean).join(" · ") : null;
		};
		const paint_mode = () => {
			d.set_df_property("posting_kind", "hidden", mode() !== "posting_kind");
			d.set_df_property("payment", "hidden", mode() !== "payment");
			for (const name of ["posting_kind", "payment"]) {
				if (mode() !== name && picked[name]) set_value(name, null);
				paint(name);
			}
		};
		const paint_catalog = () => {
			const connection_label = p.intake_targets.TARGETS.find(row => row.adapter === picked.target)?.label || "";
			let note = "";
			if (catalog?.loading) note = __("intake.catalog.loading", [connection_label]);
			else if (catalog?.error_code) note = p.intake_targets.catalog_error(catalog.error_code);
			else if (catalog && !catalog.companies.length) note = __("intake.catalog.ERR_ACCOUNT_SET_EMPTY");
			d.set_df_property("company", "description", note);
			d.set_df_property("catalog_retry", "hidden", !(catalog && !catalog.loading && catalog.error_code));
			// 仅识别不对接任何 ERP：公司 / 年度整排不摆。
			for (const name of ["company", "year"]) d.set_df_property(name, "hidden", recognize_only() ? 1 : 0);
			paint("company");
			paint_year();
			paint_primary();
		};
		const open_steps = () => {
			for (const name of STEP_FIELDS) paint(name);
			paint_year();
			paint_mode();
			paint_catalog();
		};

		/** 读目标 ERP 的公司/年度：先有先给，刷新到了再换；默认账套在目录里就预选。 */
		const load_catalog = () => {
			const run = ++generation;
			catalog = null;
			const connection = snapshot?.connections?.[picked.target];
			if (!gated() || !connection || recognize_only()) {
				paint_catalog();
				return;
			}
			void p.intake_targets.catalog(connection, {
				alive: () => run === generation,
				on_update: next => {
					catalog = next;
					paint_catalog();  // 先把选项铺上，下面的预选才落得进下拉
					const home = !picked.company && next.default_key
						&& next.companies.find(row => row.years.some(year => year.value === next.default_key));
					if (home) {
						set_value("company", home.value);
						paint_year();
						set_value("year", next.default_key);
						paint_catalog();
					}
				},
			}).catch(error => {
				if (run !== generation) return;
				catalog = {companies: [], loading: false, error_code: error?.exc_type || "ERR_UNEXPECTED"};
				paint_catalog();
			});
		};

		const paint_targets = () => {
			picked.connection = snapshot.connections[picked.target]?.name || null;
			target_choice.set_items(p.intake_targets.TARGETS.map(target => ({
				value: target.adapter,
				label: target.label,
				status: p.intake_targets.describe(target.adapter, snapshot),
				disabled: !p.intake_targets.ready(target.adapter, snapshot),
				action: target.action !== "connect" ? null : {label: __("Connect"), on_click: () => void (
					target.adapter === "express"
						? p.intake_targets.express_dialog({connection: snapshot.connections.express, on_done: refresh})
						: p.intake_targets.connect_dialog({connection: snapshot.connections.mrerp, on_done: refresh}))},
			})));
		};
		const refresh = async () => {
			snapshot = await p.intake_targets.state();
			paint_targets();
			open_steps();
			load_catalog();
		};
		// 助手上下线只体现在心跳上，没有推送事件：开着弹窗时定时重读。只有状态真的变了才重画，
		// 当前目标的就绪与否变了才重读目录（不打断正在选的下拉）。
		const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
		const signature = state => p.intake_targets.TARGETS.map(target =>
			[p.intake_targets.describe(target.adapter, state), p.intake_targets.ready(target.adapter, state)]);
		const watch = setInterval(async () => {
			if (document.hidden || !snapshot) return;
			const next = await p.intake_targets.state().catch(() => null);
			if (!next || same(signature(next), signature(snapshot))) return;
			const was_ready = gated();
			snapshot = next;
			paint_targets();
			if (gated() !== was_ready) {
				open_steps();
				load_catalog();
			}
		}, 30000);

		function on_change(name) {
			const key = name === "year" ? "account_set_key" : name;
			const value = d.get_value(name) || null;
			if (picked[key] === value) return;  // 程序赋值时已先记进 picked，这里不重复处理
			picked[key] = value;
			if (name === "company") {
				picked.account_set_key = null;  // 换公司，年度要重选
				paint_year();
			}
			if (name === "year") paint_year();
			if (name === "direction") paint_mode();  // 进项 / 销项决定 MR.ERP 能选哪几种付款方式
			paint_primary();
		}
		d.onhide = () => {  // 关掉弹窗就停止目录轮询与助手状态重读
			generation += 1;
			clearInterval(watch);
		};

		const host = name => d.fields_dict[name]?.$wrapper;
		const target_choice = p.cowork_choice(host("target"), {
			items: p.intake_targets.TARGETS.map(target => ({value: target.adapter, label: target.label})),
			on_change: value => {
				picked.target = value;
				picked.connection = snapshot?.connections?.[value]?.name || null;
				for (const name of ["company", "year", "posting_kind", "payment"]) set_value(name, null);
				open_steps();
				load_catalog();
			},
		});
		// 目标系统这一组是卡片，自己带一个标题（下拉那几组的标题由字段标签给）。
		$("<div class='pearnly-choice-title'>").text(__(t("intake.target.title"))).prependTo(host("target"));
		target_choice.set_enabled(true);
		open_steps();
		await refresh();
		return d;
	};
})();
