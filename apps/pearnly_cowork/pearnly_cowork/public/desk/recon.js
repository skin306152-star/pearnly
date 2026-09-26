/* 对账中心（Cowork）：银行对账 / 收入对账 各一个侧栏入口，同一套页面（用户 2026-09-26）。
 *
 *   列表：官方列表，列头排序、勾选删除，右上角「新建任务」；
 *   新建：上方两个上传口（各传一份对应的文件）→「开始对账」；
 *   核对中：官方整屏遮罩（`frappe.dom.freeze`），只显示阶段文字（照旧线上，不显示百分比）；
 *   完成：结果页就是导出的复核表，按导出的 Excel 逐格照画（用户 2026-09-26）；输入落在表里的格子可在线改，
 *         改了自动重算（服务端用同一套比对与同一个导出函数），有改动才出现「保存」。
 *
 * 列表、新建、遮罩、提示用官方控件；复核表是用户明确要求的自画例外（照 Excel 画，见任务记录）。
 * 服务端：`recon_center/api.py`（任务、阶段、列表、结果、删除），导出沿用各对账域的下载接口。
 */
(() => {
	const p = frappe.pearnly;
	const api = "pearnly_cowork.recon_center.api.";
	const POLL_MS = 1500, POLL_LIMIT_MS = 20 * 60 * 1000;

	// 每种对账的差异：上传口与列表列。结果页（复核表）的数据见服务端 `recon_center/sheets.py`。
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

		// ── 结果 = 导出的复核表，逐格照画（用户 2026-09-26：按导出表格 1:1 画，可以不用官方组件，不出现使用说明）──
		// 服务端（`recon_center/sheets.py`）用导出同一个函数生成工作簿，把每格的文字与样式、合并、列宽、行高、冻结窗格交过来；
		// 这里照着画成表格，底下是 Excel 那样的工作表标签。带输入位置的格子可以直接改，改完自动重算、重画，有改动才出现「保存」。
		const sheets_api = "pearnly_cowork.recon_center.sheets.";
		const lang = () => (frappe.boot.lang || "th").slice(0, 2);
		const clone = value => JSON.parse(JSON.stringify(value));

		function cell_style(node, style, grid) {
			const side = value => value ? `${value[0]}px ${value[2]} ${value[1]}` : grid && !style.bg ? "1px solid #d9d9d9" : "none";
			const sides = style.bd || [];
			Object.assign(node.style, {
				background: style.bg || "", color: style.fg || "", fontWeight: style.b ? "700" : "", fontStyle: style.i ? "italic" : "",
				textDecoration: style.u ? "underline" : "", fontSize: `${style.sz || 11}pt`,
				fontFamily: style.fn ? `"${style.fn}", Calibri, Arial, sans-serif` : "",
				textAlign: style.h, verticalAlign: style.v, whiteSpace: style.w ? "pre-wrap" : "pre",
				paddingLeft: style.in ? `${style.in * 9 + 3}px` : "",
				borderTop: side(sides[0]), borderRight: side(sides[1]), borderBottom: side(sides[2]), borderLeft: side(sides[3]),
			});
		}

		function draw_sheet(host, sheet, styles, on_edit) {
			const table = document.createElement("table");
			table.className = "pearnly-sheet-table";
			table.style.width = `${sheet.widths.reduce((sum, width) => sum + width, 0)}px`;
			const columns = document.createElement("colgroup");
			sheet.widths.forEach(width => {
				const column = document.createElement("col");
				column.style.width = `${width}px`;
				columns.append(column);
			});
			table.append(columns);
			const body = document.createElement("tbody");
			const [frozen_rows, frozen_columns] = sheet.freeze;
			const lefts = sheet.widths.reduce((out, width, i) => [...out, i ? out[i - 1] + sheet.widths[i - 1] : 0], []);
			let top = 0;
			sheet.rows.forEach((row, r) => {
				const line = document.createElement("tr");
				const height = row.h || 20;
				line.style.height = `${height}px`;
				row.c.forEach((item, c) => {
					if (!item) return;
					const [text, style_id, extra = {}] = item;
					const node = document.createElement("td");
					if (extra.cs > 1) node.colSpan = extra.cs;
					if (extra.rs > 1) node.rowSpan = extra.rs;
					const style = styles[style_id];
					cell_style(node, style, sheet.grid);
					node.textContent = text;
					// 不换行的字像 Excel 一样可以伸到右边的空格子里；右边有字就截断。
					const next = row.c[c + (extra.cs || 1)];
					const spill = text !== "" && !style.w && style.h !== "right" && (!next || (next[0] === "" && !next[2]?.e));
					if (spill) node.classList.add("spill");
					const frozen_row = r < frozen_rows, frozen_column = c < frozen_columns;
					if (frozen_row || frozen_column) {
						node.classList.add("frozen");
						if (!node.style.background) node.style.background = "#fff";
						if (frozen_row) node.style.top = `${top}px`;
						if (frozen_column) node.style.left = `${lefts[c]}px`;
						// 冻结的格子各自叠在上层；伸出去的字要再高一层，才不会被右边的白底盖住。
						node.style.zIndex = String((frozen_row && frozen_column ? 3 : 2) + (spill ? 2 : 0));
					}
					if (extra.e) {
						node.contentEditable = "true";
						node.classList.add("editable");
						node.addEventListener("keydown", event => {
							if (event.key === "Enter") { event.preventDefault(); node.blur(); }
							if (event.key === "Escape") { node.textContent = text; node.blur(); }
						});
						node.addEventListener("blur", () => {
							const typed = node.innerText.replace(/\n/g, " ").trim();
							on_edit(extra.e, typed === text.trim() ? null : typed);
						});
					}
					line.append(node);
				});
				if (r < frozen_rows) top += height;
				body.append(line);
			});
			table.append(body);
			host.replaceChildren(table);
		}

		async function show_result(name) {
			const request = ++sequence;
			const data = await p.call(sheets_api + "load", { kind, task_id: name, lang: lang() });
			if (request !== sequence || frappe.get_route()[1] !== name) return;
			clear();
			let modified = data.task.modified, dirty = false, pending = null, active = 0, view = data, waiting = null;
			let inputs = clone(data.inputs), good = clone(data.inputs);
			page.set_title(data.task.title || name); breadcrumbs([{ label: data.task.title || name }]);
			if (data.task.status !== "completed") page.set_indicator(t(`cowork.recon.task.${data.task.status}`), "orange");
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
			const book = document.createElement("div");
			book.className = "pearnly-sheet";
			const scroller = document.createElement("div");
			scroller.className = "pearnly-sheet-scroll";
			const tabs = document.createElement("div");
			tabs.className = "pearnly-sheet-tabs";
			book.append(scroller, tabs);
			page.main.append(book);

			const paint = () => {
				// 正在改某一格时先不重画，等这一格改完（失焦）再画最新的。
				if (book.contains(document.activeElement) && document.activeElement.isContentEditable) { waiting = view; return; }
				waiting = null;
				const sheet = view.sheets[Math.min(active, view.sheets.length - 1)];
				const scroll = [scroller.scrollLeft, scroller.scrollTop];
				draw_sheet(scroller, sheet, view.styles, edit);
				[scroller.scrollLeft, scroller.scrollTop] = scroll;
				tabs.replaceChildren(...view.sheets.map((item, index) => {
					const tab = document.createElement("a");
					tab.className = `pearnly-sheet-tab${index === active ? " active" : ""}`;
					tab.textContent = item.name;
					tab.addEventListener("click", () => {
						if (index === active) return;
						active = index; scroller.scrollLeft = 0; scroller.scrollTop = 0; paint();
					});
					return tab;
				}));
			};
			const recalc = async () => {
				const payload = clone(inputs);
				pending = payload;
				try {
					const result = await p.call(sheets_api + "recalc", { kind, task_id: name, inputs: JSON.stringify(payload), lang: lang() }, "POST");
					if (pending !== payload || frappe.get_route()[1] !== name) return;
					view = result; inputs = clone(result.inputs); good = clone(result.inputs);
				} catch (error) {
					// 打进去的字不合格（日期、金额）：退回上一次算得通的输入，表也画回去。
					if (pending !== payload) return;
					inputs = clone(good);
				}
				paint();
			};
			function edit(target, typed) {
				if (typed === null) { if (waiting) paint(); return; }
				const [key, index, field] = target;
				inputs[key][index][field] = typed;
				if (!dirty) { dirty = true; buttons(); }
				void recalc();
			}
			async function save() {
				const saved = await p.call(sheets_api + "save", { kind, task_id: name, inputs: JSON.stringify(inputs),
					modified, lang: lang() }, "POST");
				modified = saved.task.modified; dirty = false;
				view = saved; inputs = clone(saved.inputs); good = clone(saved.inputs);
				buttons(); paint();
				p.success(t("cowork.recon.saved"));
			}
			buttons();
			paint();
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
