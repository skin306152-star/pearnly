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
			page.hide_form(); page.clear_primary_action(); page.clear_secondary_action(); page.clear_indicator(); page.set_title_sub("");
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

		// ── 结果页 ────────────────────────────────────────────────────────────────
		// 数据仍是导出同一个函数生成的工作簿（`recon_center/sheets.py`：每格文字、样式、合并、列宽、冻结），计算与导出不变。
		// 页面外观 2026-09-27 按用户改成原生风格（"改为图二那种，可以点击排序……整屏看起来视觉上协调能对齐"）：
		//   明细表 = 原生表格外观（浅灰表头、点列头排序、行线与悬停色），状态格用原生彩色标签，合计行固定在底部；
		//   汇总表 = 卡片：调节过程一张主卡，账户信息 / 结果 / 文件信息放右侧，一屏看全；
		//   工作表切换放在顶部原生页签。排序只改显示顺序；可改的格子带着自己的输入位置，排序后照样能改。
		const sheets_api = "pearnly_cowork.recon_center.sheets.";
		const lang = () => (frappe.boot.lang || "th").slice(0, 2);
		const clone = value => JSON.parse(JSON.stringify(value));
		// 工作簿里的底色 / 字色 → 原生色名。
		const TONES = { "#DCFCE7": "green", "#FEE2E2": "red", "#FEF3C7": "orange", "#DBEAFE": "blue" };
		const RED_TEXT = new Set(["#FF0000", "#DC2626", "#B91C1C", "#991B1B"]);
		const GREEN_TEXT = new Set(["#166534", "#16A34A", "#065F46"]);
		// 金额 / 数量 / 百分比算数字（右对齐、按数值排）；税号、单号这类长串数字是编号，按文字处理。
		const is_number = text => {
			const value = text.trim();
			return /^[-+]?\(?[\d,]*\.?\d+\)?%?$/.test(value) && (/[.,%]/.test(value) || value.replace(/\D/g, "").length <= 5);
		};
		// 合计行的公式值在工作簿里没带数字格式：照这一列的金额写法补上千分位与两位小数（只改显示）。
		const money = value => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
		const is_header = (row, styles) => row.c.some(item => item && styles[item[1]].bg === "#1F2937");
		const is_blank = row => row.c.every(item => !item || item[0] === "");

		function tone_of(style) {
			return TONES[(style.bg || "").toUpperCase()] || null;
		}

		function text_tone(style) {
			const fg = (style.fg || "").toUpperCase();
			return RED_TEXT.has(fg) ? "red" : GREEN_TEXT.has(fg) ? "green" : null;
		}

		// 一格的内容：状态字（有底色、不是数字）画成原生彩色标签，数字右对齐等宽，红绿字保留。
		function fill_cell(node, text, style) {
			const tone = tone_of(style), numeric = is_number(text);
			node.classList.toggle("is-number", numeric || style.h === "right");
			if (style.h === "center" && !tone) node.classList.add("is-center");
			if (style.b) node.classList.add("is-bold");
			const color = text_tone(style) || (numeric ? tone : null);
			if (color) node.classList.add(`text-${color}`);
			if (tone && !numeric && text) {
				const pill = document.createElement("span");
				pill.className = `indicator-pill no-indicator-dot ${tone} ellipsis`;
				pill.textContent = text.replace(/^[✓❗⚠✗✔]️?\s*/u, "");
				pill.title = text;
				node.append(pill);
			} else {
				node.textContent = text;
				if (text) node.title = text;
			}
		}

		function make_editable(node, text, target, on_edit) {
			node.contentEditable = "true";
			node.classList.add("editable");
			node.addEventListener("keydown", event => {
				if (event.key === "Enter") { event.preventDefault(); node.blur(); }
				if (event.key === "Escape") { node.textContent = text; node.blur(); }
			});
			node.addEventListener("blur", () => {
				const typed = node.innerText.replace(/\n/g, " ").trim();
				on_edit(target, typed === text.trim() ? null : typed);
			});
		}

		// 排序用的键：金额 / 百分比按数值，dd/mm/yyyy 按日期，其余按文字；空格子永远排在最后。
		function sort_key(text) {
			const value = text.trim();
			if (!value || value === "—") return null;
			if (is_number(value)) {
				const negative = value.startsWith("-") || value.startsWith("(");
				const number = parseFloat(value.replace(/[^\d.]/g, ""));
				return negative ? -number : number;
			}
			const date = value.match(/^(\d{2})\/(\d{2})\/(\d{4})(?: (\d{2}):(\d{2}))?$/);
			if (date) return Number(`${date[3]}${date[2]}${date[1]}${date[4] || "00"}${date[5] || "00"}`);
			return value;
		}

		function compare(a, b) {
			if (a === null || b === null) return a === b ? 0 : a === null ? 1 : -1;
			if (typeof a === "number" && typeof b === "number") return a - b;
			return String(a).localeCompare(String(b), frappe.boot.lang || undefined, { numeric: true });
		}

		// 列宽按页面字号量内容（工作簿里的列宽是 Excel 字号的，页面上会截断日期和金额）：表头与每格取最宽，夹在上下限之间。
		const WIDTH_MIN = 56, WIDTH_MAX = 360, CELL_PAD = 24, PILL_PAD = 28, SORT_PAD = 18;
		let measurer;
		function column_widths(host, head, rows, styles) {
			measurer = measurer || document.createElement("canvas").getContext("2d");
			const font = getComputedStyle(host);
			measurer.font = `${font.fontSize} ${font.fontFamily}`;
			return head.c.map((item, c) => {
				let width = item ? measurer.measureText(item[0]).width + SORT_PAD : 0;
				rows.forEach(row => {
					const cell = row.c[c];
					if (!cell || cell[2]?.cs > 1) return;
					const pill = tone_of(styles[cell[1]]) && !is_number(cell[0]);
					width = Math.max(width, measurer.measureText(cell[0]).width + (pill ? PILL_PAD : 0));
				});
				// 泰文等由备用字体画出来比量的略宽：留 8% 余量。
				return Math.round(Math.min(WIDTH_MAX, Math.max(WIDTH_MIN, width * 1.08 + CELL_PAD)));
			});
		}

		// 明细表：表头一行 + 数据行 + 末尾的合计行（加粗灰底，固定在底部、不参与排序）。
		function draw_list(host, sheet, styles, sort, on_sort, on_edit) {
			const head_index = sheet.rows.findIndex(row => is_header(row, styles));
			const head = sheet.rows[head_index];
			let body = sheet.rows.slice(head_index + 1).filter(row => !is_blank(row));
			let footer = [];
			while (body.length) {
				const last = body[body.length - 1], first = last.c.find(Boolean);
				if (!first || !styles[first[1]].b || (styles[first[1]].bg || "").toUpperCase() !== "#F3F4F6") break;
				footer.unshift(body.pop());
			}
			const money_columns = head.c.map((_item, c) => body.some(row => /\.\d{2}$/.test(row.c[c]?.[0] || "")));
			footer = footer.map(row => ({ ...row, c: row.c.map((item, c) => item && money_columns[c] && /^-?\d+(\.\d+)?$/.test(item[0])
				? [money(item[0]), ...item.slice(1)] : item) }));
			const merged = body.some(row => row.c.some(item => item?.[2]?.cs > 1 || item?.[2]?.rs > 1));
			if (sort && !merged) {
				const keyed = body.map((row, index) => ({ row, index, key: sort_key(row.c[sort.column]?.[0] || "") }));
				keyed.sort((a, b) => {
					if (a.key === null || b.key === null) return compare(a.key, b.key) || a.index - b.index;
					return (sort.order === "desc" ? compare(b.key, a.key) : compare(a.key, b.key)) || a.index - b.index;
				});
				body = keyed.map(item => item.row);
			}
			const frozen = sheet.freeze[1] || 0;
			const widths = column_widths(host, head, [...body, ...footer], styles);
			const lefts = widths.reduce((out, width, i) => [...out, i ? out[i - 1] + widths[i - 1] : 0], []);
			const table = document.createElement("table");
			table.className = "pearnly-recon-table";
			table.style.minWidth = `${widths.reduce((sum, width) => sum + width, 0)}px`;
			const columns = document.createElement("colgroup");
			widths.forEach(width => {
				const column = document.createElement("col");
				column.style.width = `${width}px`;
				columns.append(column);
			});
			const pin = (node, c) => {
				if (c >= frozen) return;
				node.classList.add("is-pinned");
				node.style.setProperty("--pin-left", `${lefts[c]}px`);
				if (c === frozen - 1) node.classList.add("is-pinned-last");
			};
			const thead = document.createElement("thead");
			const head_line = document.createElement("tr");
			head.c.forEach((item, c) => {
				if (!item) return;
				const th = document.createElement("th");
				const label = document.createElement("span");
				label.className = "ellipsis";
				label.textContent = item[0];
				th.title = item[0];
				th.append(label);
				// 表头的对齐跟着这一列的数据走（金额列右对齐）。
				const sample = body.map(row => row.c[c]?.[0]).find(value => value);
				if (sample && is_number(sample)) th.classList.add("is-number");
				if (!merged) {
					th.classList.add("sortable");
					const arrow = document.createElement("span");
					arrow.className = "pearnly-recon-sort";
					arrow.textContent = sort?.column === c ? (sort.order === "asc" ? "↑" : "↓") : "";
					th.append(arrow);
					th.addEventListener("click", () => on_sort(c));
				}
				pin(th, c);
				head_line.append(th);
			});
			thead.append(head_line);
			const line_of = (row, footer_row) => {
				const line = document.createElement("tr");
				row.c.forEach((item, c) => {
					if (!item) return;
					const [text, style_id, extra = {}] = item;
					const node = document.createElement("td");
					if (extra.cs > 1) node.colSpan = extra.cs;
					if (extra.rs > 1) node.rowSpan = extra.rs;
					fill_cell(node, text, footer_row ? { ...styles[style_id], bg: null } : styles[style_id]);
					if (extra.e) make_editable(node, text, extra.e, on_edit);
					pin(node, c);
					line.append(node);
				});
				return line;
			};
			const tbody = document.createElement("tbody");
			body.forEach(row => tbody.append(line_of(row, false)));
			table.append(columns, thead, tbody);
			if (footer.length) {
				const tfoot = document.createElement("tfoot");
				footer.forEach(row => tfoot.append(line_of(row, true)));
				table.append(tfoot);
			}
			host.replaceChildren(table);
		}

		// 汇总表（两列：项目 / 金额）：按空行分块，带表头的那块是调节过程（主卡），其余块是右侧的小卡。
		function draw_summary(host, sheet, styles) {
			const blocks = [[]];
			sheet.rows.forEach(row => {
				if (is_blank(row)) { if (blocks[blocks.length - 1].length) blocks.push([]); return; }
				blocks[blocks.length - 1].push(row);
			});
			if (!blocks[blocks.length - 1].length) blocks.pop();
			const span_all = row => row.c[0]?.[2]?.cs >= sheet.widths.length;
			const card_of = rows => {
				const card = document.createElement("div");
				card.className = "pearnly-recon-card";
				let rest = rows;
				// 块的第一行是整行合并的标题（如"银行对账 · KBANK""文件信息"）→ 卡片标题。
				if (rows.length > 1 && span_all(rows[0])) {
					const heading = document.createElement("div");
					heading.className = "pearnly-recon-card-title";
					heading.textContent = rows[0].c[0][0];
					card.append(heading);
					rest = rows.slice(1);
				}
				const table = document.createElement("table");
				table.className = "pearnly-recon-table pearnly-recon-summary";
				const columns = document.createElement("colgroup");
				columns.innerHTML = '<col><col class="amount">';
				table.append(columns);
				const tbody = document.createElement("tbody");
				rest.forEach(row => {
					const line = document.createElement("tr");
					if (is_header(row, styles)) {
						row.c.forEach(item => {
							if (!item) return;
							const th = document.createElement("th");
							th.textContent = item[0];
							if (styles[item[1]].h === "center" && line.children.length) th.classList.add("is-number");
							line.append(th);
						});
						table.createTHead().append(line);
						return;
					}
					const first = styles[row.c[0]?.[1]] || {};
					if (span_all(row)) line.classList.add("is-section");
					else if (first.bg && (first.bg || "").toUpperCase() === "#F3F4F6") line.classList.add("is-subtotal");
					const tone = tone_of(first);
					if (tone) line.classList.add(`tone-${tone}`);
					row.c.forEach((item, c) => {
						if (!item) return;
						const [text, style_id, extra = {}] = item;
						const style = styles[style_id];
						const node = document.createElement("td");
						if (extra.cs > 1) node.colSpan = extra.cs;
						const indent = c === 0 ? (style.in || 0) : 0;
						if (indent > 1 || /^\s{2,}·/.test(text)) node.classList.add("is-detail");
						fill_cell(node, c === 0 ? text.trim() : text, tone && c === 0 ? { ...style, bg: null } : style);
						line.append(node);
					});
					tbody.append(line);
				});
				table.append(tbody);
				card.append(table);
				return card;
			};
			const main = blocks.findIndex(rows => rows.some(row => is_header(row, styles)));
			const layout = document.createElement("div");
			layout.className = "pearnly-recon-summary-layout";
			const side = document.createElement("div");
			side.className = "pearnly-recon-summary-side";
			blocks.forEach((rows, index) => {
				if (index === main) return;
				side.append(card_of(rows));
			});
			if (main >= 0) layout.append(card_of(blocks[main]));
			if (side.children.length) layout.append(side);
			else layout.classList.add("single");
			host.replaceChildren(layout);
		}

		function draw_sheet(host, sheet, styles, sort, on_sort, on_edit) {
			const list = sheet.widths.length > 2 && sheet.rows.some(row => is_header(row, styles));
			host.classList.toggle("is-summary", !list);
			if (list) draw_list(host, sheet, styles, sort, on_sort, on_edit);
			else draw_summary(host, sheet, styles);
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
			// 为什么要人看一眼：一句话写在标题下（用户 2026-09-28：只在页面写，导出的 Excel 不加）。
			page.set_title_sub((data.task.notes || []).length ? p.escape(data.task.notes.join(" · ")) : "", "orange");
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
			// 顶部原生页签切换工作表；表格区按窗口高度铺满，只在表内滚动（表头、合计行、冻结列固定）。
			const book = document.createElement("div");
			book.className = "pearnly-recon-book";
			const tabs = document.createElement("nav");
			tabs.className = "form-tabs-list pearnly-recon-tabs";
			const tab_list = document.createElement("ul");
			tab_list.className = "nav form-tabs";
			tabs.append(tab_list);
			const scroller = document.createElement("div");
			scroller.className = "pearnly-recon-scroll";
			book.append(tabs, scroller);
			page.main.append(book);
			const sorts = {};
			const fit = () => {
				if (!scroller.isConnected) { window.removeEventListener("resize", fit); return; }
				// 汇总是卡片，跟整页一起滚；明细表铺满窗口余下的高度，只在表内滚。
				const top = scroller.getBoundingClientRect().top + window.scrollY;
				scroller.style.maxHeight = scroller.classList.contains("is-summary") ? ""
					: `${Math.max(320, window.innerHeight - top - 24)}px`;
			};
			window.addEventListener("resize", fit);

			const paint = () => {
				// 正在改某一格时先不重画，等这一格改完（失焦）再画最新的。
				if (book.contains(document.activeElement) && document.activeElement.isContentEditable) { waiting = view; return; }
				waiting = null;
				active = Math.min(active, view.sheets.length - 1);
				const sheet = view.sheets[active];
				const scroll = [scroller.scrollLeft, scroller.scrollTop];
				draw_sheet(scroller, sheet, view.styles, sorts[active], on_sort, edit);
				[scroller.scrollLeft, scroller.scrollTop] = scroll;
				tab_list.replaceChildren(...view.sheets.map((item, index) => {
					const tab = document.createElement("li");
					tab.className = "nav-item";
					const link = document.createElement("button");
					link.type = "button";
					link.className = `nav-link${index === active ? " active" : ""}`;
					link.textContent = item.name;
					link.addEventListener("click", () => {
						if (index === active) return;
						active = index; scroller.scrollLeft = 0; scroller.scrollTop = 0; paint();
					});
					tab.append(link);
					return tab;
				}));
				tabs.classList.toggle("hide", view.sheets.length < 2);
				fit();
			};
			// 点列头：升序 → 降序 → 恢复原顺序（与原生表格相同）。
			function on_sort(column) {
				const sort = sorts[active];
				sorts[active] = sort?.column !== column ? { column, order: "asc" }
					: sort.order === "asc" ? { column, order: "desc" } : undefined;
				paint();
			}
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
