/* 对账中心（Cowork）：银行对账 / 收入对账 各一个侧栏入口，同一套页面（用户 2026-09-26）。
 *
 *   列表：官方列表，列头排序、勾选删除，右上角「新建任务」；
 *   新建：上方两个上传口（各传一份对应的文件）→「开始对账」；
 *   核对中：官方整屏遮罩（`frappe.dom.freeze`），只显示阶段文字（照旧线上，不显示百分比）；
 *   完成：结果页就是导出的复核表（用户 2026-09-26）——四个汇总数 + 工作簿各页；输入那几页可在线改，
 *         改了自动重算（服务端用同一套比对与同一个导出函数），有改动才出现「保存」。
 *
 * 全部是官方控件（列表、FieldGroup、DataTable、遮罩、msgprint），不自画控件。
 * 服务端：`recon_center/api.py`（任务、阶段、列表、结果、删除），导出沿用各对账域的下载接口。
 */
(() => {
	const p = frappe.pearnly;
	const api = "pearnly_cowork.recon_center.api.";
	const POLL_MS = 1500, POLL_LIMIT_MS = 20 * 60 * 1000;

	// 每种对账的差异：上传口与列表列。结果页的各页见下面 `INPUTS` 与服务端 `recon_center/sheets.py`。
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
		},
		vat: {
			root: "pearnly-recon-vat", title: "cowork.nav.reconVat", doctype: "Cowork VAT Check",
			export: "pearnly_cowork.recon_center.api.export_vat", multiple: true,
			accept: ".pdf,.png,.jpg,.jpeg,.webp,.xlsx,.xls,.csv,.tsv",
			left: "cowork.recon.vat.left", right: "cowork.recon.vat.right",
			list: t => [
				{ fieldname: "title", width: 220, min_width: 160, label: t("cowork.recon.col.title") },
				{ fieldname: "period", width: 90, min_width: 72, label: t("cowork.recon.col.period") },
				{ fieldname: "invoice_count", width: 80, min_width: 64, label: t("cowork.recon.col.invoices") },
				{ fieldname: "matched_count", width: 90, min_width: 72, label: t("cowork.recon.col.matched") },
				{ fieldname: "diff_count", width: 90, min_width: 72, label: t("cowork.recon.col.differences"),
					cell_class: value => Number(value) ? "pearnly-difference" : "" },
				{ fieldname: "unmatched_count", width: 90, min_width: 72, label: t("cowork.recon.col.unmatched"),
					cell_class: value => Number(value) ? "pearnly-difference" : "" },
			],
			sort: ["title", "period", "invoice_count", "matched_count", "diff_count", "unmatched_count", "created_at"],
		},
	};

	p.coworkRecon = kind => async (page, _shell_t, wrapper) => {
		const spec = KINDS[kind];
		const t = await p.texts("cowork.recon.");
		const root = spec.root;
		let list, current, sequence = 0;
		const title = () => __(spec.title);
		const breadcrumbs = trail => p.breadcrumbs({ label: title(), route: p.subroute(root), trail: trail || [] });
		p.add_refresh(page, () => current ? show_result(current) : list?.reload());

		function clear() {
			list?.destroy();
			list = null;
			page.page_form.detach();
			page.main.empty().append(page.page_form).removeClass("layout-main-list list-view");
			page.clear_fields(); page.fields_dict = {}; page.clear_inner_toolbar();
			page.hide_form(); page.clear_primary_action(); page.clear_secondary_action(); page.clear_indicator();
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
					const files = await p.pick_files(spec.accept || ".pdf,.xlsx,.xls,.csv,.tsv", Boolean(spec.multiple));
					if (!files.length) return;
					chosen[side] = spec.multiple ? files : files[0];
					await form.set_value(`${side}_file`, files.length > 1
						? t("cowork.recon.files", files.length) : files[0].name);
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

		// ── 结果 = 导出的复核表：各页按工作簿顺序排成官方分区；输入那几页可改，改了自动重算，有改动才出现「保存」 ──
		const sheets_api = "pearnly_cowork.recon_center.sheets.";
		const lang = () => (frappe.boot.lang || "th").slice(0, 2);
		const INPUTS = {
			bank: [
				{ key: "statement", label: "cowork.recon.sheet.statement", fields: [
					["date", "Data", "cowork.recon.col.date", 2], ["description", "Data", "cowork.recon.col.description", 3],
					["withdrawal", "Float", "cowork.recon.col.withdrawal", 1], ["deposit", "Float", "cowork.recon.col.deposit", 1],
					["balance", "Float", "cowork.recon.col.balance", 2]],
					// 读取器留下的三个标记随行带着（新加的行按“人工录入”：高置信、未校验、未自动更正）。
					keep: { confidence: "high", balance_ok: "", autocorrected: 0 } },
				{ key: "gl", label: "cowork.recon.sheet.gl", fields: [
					["date", "Data", "cowork.recon.col.date", 2], ["doc_no", "Data", "cowork.recon.col.voucher", 1],
					["account_code", "Data", "cowork.recon.col.account", 1], ["description", "Data", "cowork.recon.col.description", 2],
					["debit", "Float", "cowork.recon.col.debit", 1], ["credit", "Float", "cowork.recon.col.credit", 1],
					["balance", "Float", "cowork.recon.col.balance", 2]], keep: {} },
			],
			income: [
				{ key: "vat", label: "cowork.recon.sheet.difference", fields: [
					["doc_no", "Data", "cowork.recon.col.docNo", 1], ["date", "Data", "cowork.recon.col.date", 1],
					["customer_name", "Data", "cowork.recon.col.customer", 2], ["vat_amount", "Float", "cowork.recon.col.vatAmount", 1],
					["gl_amount", "Float", "cowork.recon.col.glAmount", 1], ["diff", "Float", "cowork.recon.col.difference", 1, true],
					["status", "Data", "cowork.recon.col.status", 2, true]],
					keep: { has_gl: 0, account_code: "" } },
			],
		};
		INPUTS.vat = [
			{ key: "invoices", label: "cowork.recon.sheet.invoices", all: true, fields: [
				["invoice_no", "Data", "cowork.recon.col.invoiceNo", 1], ["invoice_date", "Data", "cowork.recon.col.date", 1],
				["buyer_name", "Data", "cowork.recon.col.customer", 2], ["buyer_tax_id", "Data", "cowork.recon.col.taxId", 2],
				["amount_pre_vat", "Float", "cowork.recon.col.preVat", 1], ["vat_amount", "Float", "cowork.recon.col.vat", 1],
				["total_amount", "Float", "cowork.recon.col.total", 1], ["filename", "Data", "cowork.recon.file", 1, true]], keep: {} },
			{ key: "report_rows", label: "cowork.recon.sheet.report", all: true, fields: [
				["report_invoice_no", "Data", "cowork.recon.col.invoiceNo", 1], ["report_date", "Data", "cowork.recon.col.date", 1],
				["report_buyer_name", "Data", "cowork.recon.col.customer", 2], ["report_buyer_tax_id", "Data", "cowork.recon.col.taxId", 2],
				["report_amount_pre_vat", "Float", "cowork.recon.col.preVat", 1], ["report_vat_amount", "Float", "cowork.recon.col.vat", 1],
				["report_amount", "Float", "cowork.recon.col.total", 2]], keep: {} },
		];
		const status_of = row => !row.has_gl ? "unmatched" : Math.abs(Number(row.diff || 0)) >= 0.005 ? "pending" : "matched";

		async function show_result(name) {
			const request = ++sequence;
			const data = await p.call(sheets_api + "load", { kind, task_id: name, lang: lang() });
			if (request !== sequence || frappe.get_route()[1] !== name) return;
			clear();
			let modified = data.task.modified, dirty = false, timer = null, pending = null;
			page.set_title(data.task.title || name); breadcrumbs([{ label: data.task.title || name }]);
			page.set_indicator(t(`cowork.recon.task.${data.task.status}`), data.task.status === "completed" ? "green" : "orange");
			const export_now = () => window.location.assign(`/api/method/${spec.export}?task_id=${encodeURIComponent(name)}&lang=${lang()}`);
			const buttons = () => {
				page.clear_primary_action(); page.clear_secondary_action();
				if (dirty) {
					page.set_primary_action(__("Save"), save);
					page.set_secondary_action(t("cowork.recon.export"), export_now);
				} else {
					page.set_primary_action(t("cowork.recon.export"), export_now);
				}
			};
			// 四个汇总数（匹配率 / 已匹配 / 待处理差异 / 未匹配）：官方只读字段一行。
			const kpi = p.layout(page.main, ["rate", "matched", "pending", "unmatched"].flatMap((key, index) => [
				...(index ? [{ fieldtype: "Column Break" }] : []),
				{ fieldtype: "Data", fieldname: key, label: t(`cowork.recon.kpi.${key}`), read_only: 1 }]));
			const paint_kpi = counts => {
				const rate = counts.total ? Math.round((counts.matched / counts.total) * 1000) / 10 : 0;
				kpi.set_value("rate", `${rate}%`);
				["matched", "pending", "unmatched"].forEach(key => kpi.set_value(key, String(counts[key])));
			};
			// 各页：只读页（由导出同一函数生成）用官方表格显示；输入页是官方可编辑表格（Grid）。
			const inputs = JSON.parse(JSON.stringify(data.inputs));
			const layout_fields = [];
			const read_only = data.sheets.map((sheet, index) => ({ ...sheet, field: `sheet_${index}` }));
			const section = (label, collapsible = false) => ({ fieldtype: "Section Break", label, collapsible: collapsible ? 1 : 0 });
			const read_only_field = (sheet, collapsed) => [section(sheet.name, collapsed), { fieldtype: "HTML", fieldname: sheet.field }];
			const grid_field = spec_input => [section(t(spec_input.label)), {
				fieldtype: "Table", fieldname: spec_input.key, in_place_edit: true, cannot_add_rows: kind === "income",
				data: inputs[spec_input.key].map(row => ({ ...row, status: kind === "income" ? t(`cowork.recon.status.${status_of(row)}`) : undefined })),
				fields: spec_input.fields.map(([fieldname, fieldtype, label, columns, locked]) => ({
					fieldname, fieldtype, label: t(label), in_list_view: 1, columns, read_only: locked ? 1 : 0,
					...(fieldtype === "Float" ? { formatter: value => value == null || value === "" ? "" : p.number(value) } : {}),
					onchange: () => changed() })) }];
			if (kind === "bank") {
				layout_fields.push(...read_only_field(read_only[0]), ...read_only_field(read_only[1]));
				INPUTS.bank.forEach(item => layout_fields.push(...grid_field(item)));
			} else if (kind === "vat") {
				layout_fields.push(...read_only_field(read_only[0]));
				INPUTS.vat.forEach(item => layout_fields.push(...grid_field(item)));
				layout_fields.push(...read_only_field(read_only[1], true));
			} else {
				INPUTS.income.forEach(item => layout_fields.push(...grid_field(item)));
				layout_fields.push(...read_only_field(read_only[0]), ...read_only_field(read_only[1], true));
			}
			const form = p.layout(page.main, layout_fields);
			const tables = {};
			const paint_sheets = async sheets => {
				for (const [index, sheet] of sheets.entries()) {
					tables[index]?.destroy();
					const host = form.fields_dict[`sheet_${index}`].$wrapper;
					const width = Math.max(...sheet.rows.map(row => row.length), 1);
					// 工作簿的一页常是“标题块 + 表头 + 明细”：第一行“大部分格有字、且全是文字”的当表头，它上面的标题块原样列在表前。
					const filled = row => row.filter(value => value !== null && value !== "").length;
					const at = sheet.rows.findIndex(row => filled(row) >= Math.max(Math.min(width, 3), Math.ceil(width * 0.6))
						&& row.filter(value => value !== null && value !== "").every(value => typeof value === "string"));
					const header = at >= 0 && at < sheet.rows.length - 1;
					const preface = header ? sheet.rows.slice(0, at) : [];
					host.empty();
					const lead = $("<div class='text-muted small mb-2'>").appendTo(host);
					preface.forEach(row => $("<div>").text(row.filter(v => v !== null && v !== "")
						.map(v => typeof v === "number" ? p.number(v) : String(v)).join(" · ")).appendTo(lead));
					const grid_host = $("<div>").appendTo(host);
					const columns = header ? sheet.rows[at].map(value => String(value ?? ""))
						: width === 2 ? [t("cowork.recon.col.item"), t("cowork.recon.col.amount")]
						: Array.from({ length: width }, (_v, i) => String.fromCharCode(65 + (i % 26)));
					const body = (header ? sheet.rows.slice(at + 1) : sheet.rows).map(row => columns.map((_c, i) => {
						const value = row[i];
						return typeof value === "number" ? p.number(value) : value ?? "";
					}));
					tables[index] = await p.table(grid_host, columns.map(name => ({ name, format: p.escape })), body,
						{ language: frappe.boot.lang, translations: frappe.utils.datatable.get_translations() });
				}
			};
			const collect = () => {
				const out = {};
				for (const item of INPUTS[kind]) {
					out[item.key] = form.fields_dict[item.key].grid.get_data().map(row => {
						// 销项税核查的行带着很多识别 / 解析留下的字段（来源文件、页码、分支……），整行带回，
						// 只去掉表格自己加的那几个键；银行两张表按固定字段收（服务端要求字段集合一致）。
						const clean = item.all ? Object.fromEntries(Object.entries(row).filter(([key]) =>
							!key.startsWith("__") && !["name", "idx", "doctype", "parent", "parentfield", "parenttype", "owner",
								"creation", "modified", "modified_by", "docstatus"].includes(key))) : {};
						for (const [fieldname] of item.fields) clean[fieldname] = row[fieldname] ?? "";
						for (const [key, fallback] of Object.entries(item.keep)) clean[key] = row[key] ?? fallback;
						delete clean.status;
						return clean;
					});
				}
				if (kind === "income") out.gl_only = inputs.gl_only;
				if (kind === "vat") {
					for (const key of ["seller_name", "seller_tax_id", "period_year", "period_month", "failed_files"]) out[key] = inputs[key];
				}
				return out;
			};
			const recalc = async () => {
				const payload = collect();
				pending = payload;
				const result = await p.call(sheets_api + "recalc", { kind, task_id: name, inputs: JSON.stringify(payload), lang: lang() }, "POST");
				if (pending !== payload || frappe.get_route()[1] !== name) return;
				paint_kpi(result.counts);
				await paint_sheets(result.sheets);
				if (kind === "income") {
					// 差额与状态是算出来的：按重算结果回填到表格里（不算用户的改动）。
					const grid = form.fields_dict.vat.grid;
					grid.get_data().forEach((row, i) => {
						const fresh = result.inputs.vat[i];
						if (!fresh) return;
						Object.assign(row, { diff: fresh.diff, has_gl: fresh.has_gl, status: t(`cowork.recon.status.${status_of(fresh)}`) });
					});
					grid.refresh();
				}
			};
			function changed() {
				if (!dirty) { dirty = true; buttons(); }
				clearTimeout(timer);
				timer = setTimeout(() => void recalc(), 500);
			}
			async function save() {
				clearTimeout(timer);
				const saved = await p.call(sheets_api + "save", { kind, task_id: name, inputs: JSON.stringify(collect()),
					modified, lang: lang() }, "POST");
				modified = saved.task.modified; dirty = false;
				p.success(t("cowork.recon.saved"));
				await show_result(name);
			}
			buttons();
			paint_kpi(data.counts);
			await frappe.require("report.bundle.js");
			await paint_sheets(read_only);
		}

		async function show_route() {
			const route = frappe.get_route();
			if (route[0] !== root) return;
			++sequence;
			clear();
			current = null;
			if (!route[1]) show_list();
			else if (route[1] === "new") show_new();
			else { current = route[1]; await show_result(current); }
		}

		wrapper.on_page_show = () => { p.restore_sidebar(); void show_route(); };
		$(wrapper).on("hide", () => { ++sequence; });
		await show_route();
	};
})();
