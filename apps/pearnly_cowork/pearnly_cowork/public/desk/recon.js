/* 对账中心（Cowork）：银行对账 / 收入对账 各一个侧栏入口，同一套页面（用户 2026-09-26）。
 *
 *   列表：官方列表，列头排序、勾选删除，右上角「新建任务」；
 *   新建：上方两个上传口（各传一份对应的文件）→「开始对账」；
 *   核对中：官方整屏遮罩（`frappe.dom.freeze`），只显示阶段文字（照旧线上，不显示百分比）；
 *   完成：结果页——四个汇总数（匹配率 / 已匹配 / 待处理差异 / 未匹配，旧线上同一组）、筛选、明细表、导出。
 *
 * 全部是官方控件（列表、FieldGroup、DataTable、遮罩、msgprint），不自画控件。
 * 服务端：`recon_center/api.py`（任务、阶段、列表、结果、删除），导出沿用各对账域的下载接口。
 */
(() => {
	const p = frappe.pearnly;
	const api = "pearnly_cowork.recon_center.api.";
	const POLL_MS = 1500, POLL_LIMIT_MS = 20 * 60 * 1000;
	const money = value => value == null || value === "" ? "—" : p.number(value);

	// 每种对账的差异：上传口、列表列、明细列与行状态。状态键见 `cowork.recon.status.*`。
	const KINDS = {
		bank: {
			root: "pearnly-recon-bank", title: "cowork.nav.reconBank", doctype: "Cowork Bank Reconciliation",
			export: "pearnly_cowork.reconciliation.bank_recon_api.export",
			left: "cowork.recon.bank.left", right: "cowork.recon.bank.right",
			list: t => [
				{ fieldname: "title", width: 240, min_width: 160, label: t("cowork.recon.col.title") },
				{ fieldname: "bank_code", width: 90, min_width: 70, label: t("cowork.recon.col.bank") },
				{ fieldname: "status", width: 100, min_width: 80, label: t("cowork.recon.col.status"),
					format: value => t(`cowork.recon.task.${value}`) },
				{ fieldname: "row_count", width: 80, min_width: 64, label: t("cowork.recon.col.rows") },
				{ fieldname: "matched_count", width: 90, min_width: 72, label: t("cowork.recon.col.matched") },
			],
			sort: ["title", "bank_code", "status", "row_count", "matched_count", "created_at"],
			state: r => r.match_status === "matched" ? (Number(r.match_layer) > 1 ? "pending" : "matched") : "unmatched",
			detail: t => [t("cowork.recon.col.date"), t("cowork.recon.col.reference"), t("cowork.recon.col.stmtAmount"),
				t("cowork.recon.col.glAmount"), t("cowork.recon.col.kind")],
			cells: (r, t) => {
				const stmt = Number(r.stmt_deposit || 0) - Number(r.stmt_withdrawal || 0);
				const gl = Number(r.gl_debit || 0) - Number(r.gl_credit || 0);
				const has_stmt = !String(r.match_status).startsWith("gl_");
				const has_gl = !String(r.match_status).startsWith("stmt_");
				return [r.stmt_date || r.gl_date || "", [r.gl_doc_no, r.stmt_desc || r.gl_desc].filter(Boolean).join(" · "),
					has_stmt ? money(stmt) : "—", has_gl ? money(gl) : "—",
					t(`cowork.recon.bank.${r.match_status}`)];
			},
		},
		income: {
			root: "pearnly-recon-income", title: "cowork.nav.reconIncome", doctype: "Cowork Income Reconciliation",
			export: "pearnly_cowork.reconciliation.income_recon_api.export",
			left: "cowork.recon.income.left", right: "cowork.recon.income.right",
			list: t => [
				{ fieldname: "title", width: 240, min_width: 160, label: t("cowork.recon.col.title") },
				{ fieldname: "row_count", width: 80, min_width: 64, label: t("cowork.recon.col.rows") },
				{ fieldname: "matched_count", width: 90, min_width: 72, label: t("cowork.recon.col.matched") },
				{ fieldname: "diff_count", width: 90, min_width: 72, label: t("cowork.recon.col.differences"),
					cell_class: value => Number(value) ? "pearnly-difference" : "" },
				{ fieldname: "unmatched_count", width: 90, min_width: 72, label: t("cowork.recon.col.unmatched"),
					cell_class: value => Number(value) ? "pearnly-difference" : "" },
			],
			sort: ["title", "row_count", "matched_count", "diff_count", "unmatched_count", "created_at"],
			state: r => !r.has_gl_match ? "unmatched" : Math.abs(Number(r.diff || 0)) >= 0.005 ? "pending" : "matched",
			detail: t => [t("cowork.recon.col.docNo"), t("cowork.recon.col.date"), t("cowork.recon.col.customer"),
				t("cowork.recon.col.vatAmount"), t("cowork.recon.col.glAmount"), t("cowork.recon.col.difference")],
			cells: r => [r.doc_no || "", r.vat_date || "", r.customer_name || "", money(r.vat_amount),
				r.has_gl_match ? money(r.gl_amount) : "—", r.has_gl_match ? money(r.diff) : "—"],
		},
	};

	p.coworkRecon = kind => async (page, _shell_t, wrapper) => {
		const spec = KINDS[kind];
		const t = await p.texts("cowork.recon.");
		const root = spec.root;
		let list, table, current, rows = [], only = "all", sequence = 0;
		const title = () => __(spec.title);
		const breadcrumbs = trail => p.breadcrumbs({ label: title(), route: p.subroute(root), trail: trail || [] });
		p.add_refresh(page, () => current ? show_result(current) : list?.reload());

		function clear() {
			list?.destroy(); table?.destroy();
			list = table = null;
			page.page_form.detach();
			page.main.empty().append(page.page_form).removeClass("layout-main-list list-view");
			page.clear_fields(); page.fields_dict = {}; page.clear_inner_toolbar();
			page.hide_form(); page.clear_primary_action(); page.clear_indicator();
		}

		// ── 列表 ──────────────────────────────────────────────────────────────
		function show_list() {
			page.set_title(title()); breadcrumbs();
			const selection = p.selection_action({
				page, list: () => list,
				empty: () => { if (frappe.get_route().length === 1) page.set_primary_action(t("cowork.recon.new"), open_new); },
				label: picked => t("cowork.recon.deleteSelected", picked.length),
				confirm: picked => t("cowork.recon.deleteConfirm", picked.length),
				run: async picked => {
					await p.call(api + "delete", { kind, task_ids: JSON.stringify(picked.map(row => row.name)) }, "POST");
				},
				success: picked => t("cowork.recon.deleted", picked.length),
				sound: "delete",
			});
			list = p.list({ page, doctype: spec.doctype, selectable: true, compact_columns: true, on_selection: selection,
				on_open: row => frappe.set_route(root, row.name),
				empty_action: { label: t("cowork.recon.new"), action: open_new },
				sort_fields: spec.sort,
				load: async ({ start, page_length, sort_by, sort_order }) => {
					const result = await p.call(api + "tasks", { kind, start, limit: page_length, sort_by, sort_order });
					return { items: result.tasks, total: result.total };
				},
				columns: [...spec.list(t),
					{ fieldname: "created_at", width: 160, min_width: 148, label: t("cowork.recon.col.createdAt"),
						format: value => p.datetime_text(value) }],
			});
			page.set_primary_action(t("cowork.recon.new"), open_new);
		}
		const open_new = () => frappe.set_route(root, "new");

		// ── 新建：两个上传口 → 开始对账 ────────────────────────────────────────
		function show_new() {
			page.set_title(t("cowork.recon.new")); breadcrumbs([{ label: t("cowork.recon.new") }]);
			const chosen = { left: null, right: null };
			// 两个上传口左右并排：官方 FieldGroup 的两栏，每栏一个标题 + 选择文件 + 文件名。
			const slot = side => [
				{ fieldtype: "Heading", fieldname: `${side}_heading`, label: t(spec[side]) },
				{ fieldtype: "Button", fieldname: `${side}_pick`, label: t("cowork.recon.pick"), click: async () => {
					const file = await p.pick_file(".pdf,.xlsx,.xls,.csv,.tsv");
					if (!file) return;
					chosen[side] = file;
					await form.set_value(`${side}_file`, file.name);
				} },
				{ fieldtype: "Data", fieldname: `${side}_file`, label: t("cowork.recon.file"), read_only: 1 },
			];
			const form = p.layout(page.main, [
				...slot("left"),
				{ fieldtype: "Column Break" },
				...slot("right"),
				{ fieldtype: "Section Break" },
				{ fieldtype: "Data", fieldname: "title", label: t("cowork.recon.col.title") },
			]);
			page.set_primary_action(t("cowork.recon.start"), async () => {
				if (!chosen.left || !chosen.right) { frappe.msgprint(t("cowork.recon.bothFiles")); return; }
				await run(chosen, form.get_value("title") || "");
			});
			page.set_secondary_action(__("Cancel"), () => frappe.set_route(root));
		}

		// 核对中：整屏遮罩，只写阶段文字；结论出来才收起。
		async function run(chosen, task_title) {
			frappe.dom.freeze(p.escape(t("cowork.recon.checking")));
			const lead = () => document.querySelector("#freeze .freeze-message p.lead");
			const say = stage => { const node = lead(); if (node) node.innerText = `${t("cowork.recon.checking")}\n${t(`cowork.recon.stage.${stage}`)}`; };
			try {
				say("upload");
				const started = await p.post_files(api + "start", { kind, title: task_title, left: chosen.left,
					right: chosen.right, lang: (frappe.boot.lang || "th").slice(0, 2) });
				const began = Date.now();
				for (;;) {
					await new Promise(resolve => setTimeout(resolve, POLL_MS));
					let state;
					try { state = await frappe.xcall(api + "status", { job: started.job }); } catch (error) { continue; }
					if (state.state === "done") { frappe.dom.unfreeze(); frappe.set_route(root, state.task); return; }
					if (state.state === "failed") {
						frappe.dom.unfreeze();
						frappe.msgprint({ title: t("cowork.recon.failed"), message: p.escape(state.error || ""), indicator: "red" });
						return;
					}
					say(state.stage || "queued");
					if (Date.now() - began > POLL_LIMIT_MS) throw new Error("timeout");
				}
			} catch (error) {
				frappe.dom.unfreeze();
				if (error?.message === "timeout") frappe.msgprint(t("cowork.recon.timeout"));
			}
		}

		// ── 结果 ──────────────────────────────────────────────────────────────
		async function show_result(name) {
			const request = ++sequence;
			const first = await p.call(api + "detail", { kind, task_id: name, start: 0, limit: 200 });
			let all = first.rows;
			while (all.length < first.total) {
				const more = await p.call(api + "detail", { kind, task_id: name, start: all.length, limit: 200 });
				if (!more.rows.length) break;
				all = all.concat(more.rows);
			}
			if (request !== sequence || frappe.get_route()[1] !== name) return;
			clear();
			rows = all.map(row => ({ ...row, state: spec.state(row) }));
			const counts = { matched: 0, pending: 0, unmatched: 0 };
			rows.forEach(row => { counts[row.state] += 1; });
			const rate = rows.length ? Math.round((counts.matched / rows.length) * 1000) / 10 : 0;
			const task_title = first.task.title || name;
			page.set_title(task_title); breadcrumbs([{ label: task_title }]);
			page.set_indicator(t(`cowork.recon.task.${first.task.status}`), first.task.status === "completed" ? "green" : "orange");
			page.set_primary_action(t("cowork.recon.export"), () => window.location.assign(
				`/api/method/${spec.export}?task_id=${encodeURIComponent(name)}&lang=${encodeURIComponent((frappe.boot.lang || "th").slice(0, 2))}`));
			// 四个汇总数：官方只读字段排成一行（旧线上结果页同一组）。
			p.layout(page.main, [
				{ fieldtype: "Data", fieldname: "rate", label: t("cowork.recon.kpi.rate"), read_only: 1, default: `${rate}%` },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Data", fieldname: "matched", label: t("cowork.recon.kpi.matched"), read_only: 1, default: String(counts.matched) },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Data", fieldname: "pending", label: t("cowork.recon.kpi.pending"), read_only: 1, default: String(counts.pending) },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Data", fieldname: "unmatched", label: t("cowork.recon.kpi.unmatched"), read_only: 1, default: String(counts.unmatched) },
			]);
			const filter = page.add_field({ fieldtype: "Select", fieldname: "only", label: t("cowork.recon.filter"), default: only,
				options: ["all", "matched", "pending", "unmatched"].map(value => ({ value, label: t(`cowork.recon.filter.${value}`) })),
				change: () => { only = filter.get_value() || "all"; void paint(); } });
			const host = $("<div class='report-wrapper'>").appendTo(page.main);
			const paint = async () => {
				table?.destroy(); table = null;
				const shown = rows.filter(row => only === "all" || row.state === only);
				if (!shown.length) {
					host.empty().append($(frappe.views.ListView.prototype.get_no_result_message.call({ doctype: spec.doctype,
						meta: { description: __("Nothing to show") }, can_create: false, get_documentation_link: () => "" })));
					return;
				}
				const status = row => {
					const text = p.escape(t(`cowork.recon.status.${row.state}`));
					return row.state === "matched" ? text : `<span style="color: var(--red-600)">${text}</span>`;
				};
				table = await p.table(host, [...spec.detail(t).map(name => ({ name, format: p.escape })),
					{ name: t("cowork.recon.col.status"), format: value => value }],
					shown.map(row => [...spec.cells(row, t), status(row)]),
					{ language: frappe.boot.lang, translations: frappe.utils.datatable.get_translations() });
			};
			await frappe.require("report.bundle.js");
			await paint();
		}

		async function show_route() {
			const route = frappe.get_route();
			if (route[0] !== root) return;
			++sequence;
			clear();
			current = null; only = "all";
			if (!route[1]) show_list();
			else if (route[1] === "new") show_new();
			else { current = route[1]; await show_result(current); }
		}

		wrapper.on_page_show = () => { p.restore_sidebar(); void show_route(); };
		$(wrapper).on("hide", () => { ++sequence; table?.destroy(); table = null; });
		await show_route();
	};
})();
